"""
Cult Kits (Shopify) fetcher.

agents.md에 명시된 읽기 전용 엔드포인트 사용:
  GET https://www.cultkits.com/products.json?limit=250&page=N

Shopify의 공개 products.json은 로그인 없이 전체 카탈로그를 페이지네이션으로 준다.
관례상 250개/page가 상한.

주의: robots.txt는 /products.json 자체를 막지 않음 (agentic 소비를 위해 공식 지원).

성능: 페이지를 순차로 하나씩 받으면 페이지 수(9~10개) x 왕복시간만큼 그대로
누적돼서 느리게 느껴진다. 총 페이지 수를 미리 모르기 때문에,
"BATCH_SIZE개씩 동시에 요청 -> 그 안에 빈 페이지가 나오면 중단"
방식으로 배치 단위 병렬 요청을 한다. 크롤링 대상 서버에 대한 예의로
동시 연결 수는 BATCH_SIZE로 제한.

점진적 저장: 배치(BATCH_SIZE페이지) 하나가 끝날 때마다 on_batch()를 호출해서
전체 사이트가 다 끝나기 전에도 결과가 즉시 반영되게 한다.

재시도 정책: services.http_client.get_with_retry가 처리한다.
    - 재시도를 다 소진한 페이지는 FetchFailed -> 이 페이지만 스킵(None으로 기록),
      "카탈로그가 끝났다"는 신호(진짜 빈 응답, [])와는 명확히 구분한다.
    - 한 배치의 모든 페이지가 실패하면(사이트 다운 등) 무한루프를 막기 위해
      MAX_CONSECUTIVE_FAILED_BATCHES번 연속 실패 시 중단한다.
"""
from __future__ import annotations

import concurrent.futures as cf
import time
from typing import List, Optional

import requests

from core.diagnostics import Diagnostics
from core.models import Product
from services.http_client import make_session, get_with_retry, FetchFailed
from .base import BaseFetcher, OnBatch

BASE_URL = "https://www.cultkits.com"
PAGE_LIMIT = 250
BATCH_SIZE = 6           # 동시에 날릴 요청 수 (서버 예의상 과도하게 높이지 않기)
BATCH_DELAY_SEC = 0.3     # 배치 사이 짧은 텀
REQUEST_TIMEOUT = 20
MAX_CONSECUTIVE_FAILED_BATCHES = 3  # 이만큼 연속으로 배치 전체가 실패하면 중단


def _extract_size(variant: dict, option_names: List[str]) -> Optional[str]:
    """variant의 option1/2/3 중 'Size' 옵션에 해당하는 값을 찾는다."""
    for idx, name in enumerate(option_names, start=1):
        if name and name.strip().lower() == "size":
            return variant.get(f"option{idx}")
    # Size라는 이름의 옵션이 없으면 그냥 option1을 사이즈로 간주 (많은 셔츠 스토어의 관례)
    return variant.get("option1")


class CultKitsFetcher(BaseFetcher):
    site_name = "cultkits"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or make_session()

    def _fetch_page(self, page: int, diag: Diagnostics) -> list:
        """page 하나를 요청해서 원본 products 배열(list[dict])을 반환.
        재시도를 다 소진하면 FetchFailed가 그대로 위로 전파된다
        (호출부가 '실패'와 '진짜 끝'을 구분할 수 있게, 여기서 삼키지 않는다)."""
        resp = get_with_retry(
            self.session, diag,
            f"{BASE_URL}/products.json",
            label=f"page {page}",
            timeout=REQUEST_TIMEOUT,
            params={"limit": PAGE_LIMIT, "page": page},
        )
        return resp.json().get("products", [])

    def _parse_product(self, p: dict) -> Optional[Product]:
        option_names = [o.get("name", "") for o in p.get("options", [])]
        sizes = []
        price = None
        currency = "GBP"  # TODO: 실제 스토어 통화 확인 후 확정 (로케일별 sitemap 존재함)

        for variant in p.get("variants", []):
            if not variant.get("available"):
                continue
            size = _extract_size(variant, option_names)
            if size:
                sizes.append(size.strip())
            if price is None and variant.get("price"):
                price = float(variant["price"])

        if price is None or not sizes:
            return None  # 재고/가격 정보 없는 상품은 스킵

        image = p["images"][0].get("src") if p.get("images") else None
        title = p.get("title", "")

        return Product(
            site=self.site_name,
            title=title,
            url=f"{BASE_URL}/products/{p.get('handle')}",
            price=price,
            currency=currency,
            image=image,
            sizes_in_stock=sizes,
            product_id=str(p.get("id")),
            classification_text=title,
        )

    def fetch(self, on_batch: OnBatch) -> None:
        start_page = 1
        stop = False
        consecutive_failed_batches = 0
        diag = Diagnostics(site=self.site_name)

        while not stop:
            page_numbers = list(range(start_page, start_page + BATCH_SIZE))

            with cf.ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
                future_to_page = {
                    executor.submit(self._fetch_page, page, diag): page for page in page_numbers
                }
                # None = 재시도 다 실패(=이 페이지만 스킵, 카탈로그 끝 아님)
                # []   = 정상 응답인데 상품이 0개(=카탈로그 끝)
                results: dict[int, Optional[list]] = {}
                for future in cf.as_completed(future_to_page):
                    page = future_to_page[future]
                    try:
                        results[page] = future.result()
                    except FetchFailed as e:
                        print(f"  [DEBUG] page {page} 재시도 끝까지 실패, 스킵: {e}")
                        results[page] = None

            batch_products: List[Product] = []
            for page in page_numbers:
                raw = results.get(page)
                if raw is None:
                    continue  # 실패한 페이지는 건너뛰되 stop 시키지 않음
                if not raw:
                    stop = True  # 진짜로 상품이 없는 페이지 = 카탈로그 끝
                    break
                for item in raw:
                    product = self._parse_product(item)
                    if product:
                        batch_products.append(product)

            if batch_products:
                consecutive_failed_batches = 0
                on_batch(batch_products)
            elif not stop and all(results.get(p) is None for p in page_numbers):
                # 이 배치의 모든 페이지가 (끝나서가 아니라) 실패해서 빈 경우
                consecutive_failed_batches += 1
                print(f"  [DEBUG] 배치 전체 실패 {consecutive_failed_batches}/{MAX_CONSECUTIVE_FAILED_BATCHES}회 연속")
                if consecutive_failed_batches >= MAX_CONSECUTIVE_FAILED_BATCHES:
                    print("  [DEBUG] 연속 실패 한도 초과, 중단")
                    stop = True

            start_page += BATCH_SIZE
            if not stop:
                time.sleep(BATCH_DELAY_SEC)

        diag.print_summary()
        diag.save_log()


if __name__ == "__main__":
    # 단독 테스트용: python -m fetchers.cultkits
    fetcher = CultKitsFetcher()
    collected: List[Product] = []
    t0 = time.time()
    fetcher.fetch(on_batch=collected.extend)
    elapsed = time.time() - t0
    print(f"총 {len(collected)}개 상품 수집 ({elapsed:.1f}초, 필터 적용 전)")
    for item in collected[:5]:
        print(item)
