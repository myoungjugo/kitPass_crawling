"""
Kitbag fetcher.

## 중요: 프로젝트 문서(코드 최종 방안)의 가정이 지금은 안 맞음
문서에는 "sitemap 없음, 카테고리 페이지 HTML 내 __platform_data__ JSON에 통째로
임베드"라고 되어 있었는데, 실제로 확인해보니 kitbag.com은 현재 Fanatics
플랫폼으로 운영되고 있고 구조가 완전히 다르다:
    - URL이 https://www4.kitbag.com/en/{리그}/{팀}/{slug}/o-...+t-...+p-...+z-... 형태
    - 카테고리 목록 페이지 자체가 서버에서 렌더링되어 제목/가격/이미지/링크가 바로 파싱 가능
      (__platform_data__ 같은 JSON blob은 확인 안 됐음 — 없을 수도, 마크다운 변환 과정에서
      제거됐을 수도 있음)
    - "Football Kits" 카테고리인데도 NFL/MLB/NHL 상품이 섞여서 나옴 → 축구 리그
      URL 경로로 직접 걸러야 함
    - www / www2 / www3 / www4 여러 미러 도메인이 있음 (지역/부하분산 추정, 정확한 규칙은 불명)

## 확인된 것
- 상세페이지 메타태그: meta[property=og:price:amount], og:price:currency,
  og:availability, og:title, og:image — 안정적으로 존재 (CFS와 meta 이름이 다름에 주의:
  여긴 "og:price:amount"이지 "product:price:amount"가 아님)
- 상세페이지 본문에 사이즈 목록(XS S M L XL 2XL...)이 텍스트로 존재

## 확인 안 된 것 (반드시 검증 필요)
- 페이지 텍스트에 사이즈가 나열되긴 하는데, 품절된 사이즈가 disabled 처리되어
  구분되는지를 실제 HTML(class/속성)로 못 봤다 (마크다운 변환이 상태 정보를 지움).
  _extract_sizes_best_effort()는 'size 관련 class를 가지면서 disabled/sold-out류
  class가 없는 요소'라는 흔한 패턴을 가정한 추측이다. 실제 실행 후 검증 필요.
- __platform_data__ 같은 임베드 JSON이 실제로 있는지 (있다면 이 방식보다 훨씬
  안정적이므로, 있으면 그쪽으로 바꾸는 걸 권장 — 브라우저 개발자도구에서 확인)

## robots.txt
- 이 파일을 만든 시점엔 www4.kitbag.com/robots.txt를 도구 제약으로 직접 못 봤다.
  대신 실행 시점에 urllib.robotparser로 실제로 확인해서, 막혀있으면 크롤링을 하지 않는다
  (아래 _robots_allow 참고 — 실제 실행 환경(서버)에서는 인터넷 접근이 자유로우니 정상 동작함).

## 안전장치
- KITBAG_MAX_PAGES(기본 5) : 카테고리 목록에서 몇 페이지까지 볼지
- KITBAG_MAX_PRODUCTS(기본 500) : 상세페이지를 몇 개까지 열어볼지
"""
from __future__ import annotations

import concurrent.futures as cf
import os
import re
import time
import urllib.robotparser as robotparser
from typing import List, Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from core.diagnostics import Diagnostics
from core.models import Product
from services.http_client import DEFAULT_USER_AGENT, make_session, get_with_retry, FetchFailed
from .base import BaseFetcher, OnBatch

BASE_URL = os.environ.get("KITBAG_BASE_URL", "https://www4.kitbag.com")
CATEGORY_PATH = os.environ.get("KITBAG_CATEGORY_PATH", "/en/football-kits-shirts-home/d-15954-15956")
PAGE_SIZE = 72

LISTING_BATCH_SIZE = 10          # 한 목록 페이지에서 뽑은 상품 상세페이지를 몇 개씩 병렬로 볼지
MAX_PAGES = int(os.environ.get("KITBAG_MAX_PAGES", "5"))
MAX_PRODUCTS = int(os.environ.get("KITBAG_MAX_PRODUCTS", "500"))
REQUEST_TIMEOUT = 20

# 축구가 아닌 것으로 확인된 리그 URL 경로 (첫 세그먼트가 /en/ 다음에 오는 값)
NON_FOOTBALL_LEAGUE_SLUGS = {"nfl", "mlb", "nhl", "nba"}

_PRODUCT_URL_RE = re.compile(
    r'href="(https?://[^"]+?/o-\d+\+t-\d+\+p-[0-9A-Za-z]+\+z-[0-9A-Za-z-]+)[^"]*"'
)

_KNOWN_SIZE_LABELS = {"XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL"}


def _robots_allow(path: str) -> bool:
    rp = robotparser.RobotFileParser()
    rp.set_url(f"{BASE_URL}/robots.txt")
    try:
        rp.read()
    except Exception as e:
        print(f"  [DEBUG] robots.txt 조회 실패 ({e}) — 보수적으로 허용 취급하지만 확인 권장")
        return True
    return rp.can_fetch(DEFAULT_USER_AGENT, path)


def _is_football_url(url: str) -> bool:
    parts = [p for p in urlparse(url).path.split("/") if p]
    # 보통 ['en', '{league}', '{team}', '{slug}', 'o-...+t-...+p-...+z-...']
    league = parts[1] if len(parts) > 1 and parts[0] == "en" else (parts[0] if parts else "")
    return league.lower() not in NON_FOOTBALL_LEAGUE_SLUGS


class KitbagFetcher(BaseFetcher):
    site_name = "kitbag"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or make_session()

    def _listing_url(self, page_number: int) -> str:
        sep = "&" if "?" in CATEGORY_PATH else "?"
        return f"{BASE_URL}{CATEGORY_PATH}{sep}pageSize={PAGE_SIZE}&pageNumber={page_number}&sortOption=TopSellers"

    def _list_page_product_urls(self, page_number: int, diag: Diagnostics) -> List[str]:
        url = self._listing_url(page_number)
        try:
            resp = get_with_retry(self.session, diag, url, label=f"list p{page_number}", timeout=REQUEST_TIMEOUT)
        except FetchFailed as e:
            print(f"  [DEBUG] 목록 페이지 실패, 스킵: {e}")
            return []
        urls = {m.group(1) for m in _PRODUCT_URL_RE.finditer(resp.text)}
        return [u for u in urls if _is_football_url(u)]

    def _fetch_detail(self, url: str, diag: Diagnostics) -> Optional[Product]:
        try:
            resp = get_with_retry(self.session, diag, url, label="detail", timeout=REQUEST_TIMEOUT)
        except FetchFailed as e:
            print(f"  [DEBUG] 상세페이지 실패, 스킵: {url} ({e})")
            return None
        return self._parse_detail(url, resp.text)

    def _parse_detail(self, url: str, html: str) -> Optional[Product]:
        soup = BeautifulSoup(html, "html.parser")

        def meta(prop: str) -> Optional[str]:
            tag = soup.find("meta", attrs={"property": prop})
            return tag["content"].strip() if tag and tag.get("content") else None

        title = meta("og:title")
        price_raw = meta("og:price:amount")
        currency = meta("og:price:currency") or "USD"
        availability = (meta("og:availability") or "").lower()
        image = meta("og:image")

        if not title or not price_raw:
            return None
        if availability and "in stock" not in availability:
            return None

        try:
            price = float(price_raw)
        except ValueError:
            return None

        sizes = self._extract_sizes_best_effort(soup)
        product_id = url.rstrip("/").rsplit("/", 1)[-1]

        return Product(
            site=self.site_name,
            title=title,
            url=url,
            price=price,
            currency=currency,
            image=image,
            sizes_in_stock=sizes,
            product_id=product_id,
            classification_text=title,
        )

    @staticmethod
    def _extract_sizes_best_effort(soup: BeautifulSoup) -> List[str]:
        """검증 안 된 최선의 추측 (파일 상단 docstring 참고).
        'size' 관련 class/속성을 가지면서 품절 표시(class)가 없는 요소를 재고 있는
        사이즈로 간주한다. 실제 마크업 확인 후 이 함수만 교체하면 된다."""
        sizes = set()
        candidates = soup.select('[data-size], [class*="size" i]')
        for el in candidates:
            label = (el.get("data-size") or el.get_text(strip=True) or "").strip().upper()
            if label not in _KNOWN_SIZE_LABELS:
                continue
            classes = " ".join(el.get("class", [])).lower()
            if any(bad in classes for bad in ("disabled", "sold", "unavailable", "out-of-stock", "oos")):
                continue
            sizes.add(label)
        return sorted(sizes)

    def fetch(self, on_batch: OnBatch) -> None:
        if not _robots_allow(CATEGORY_PATH):
            print(f"  [DEBUG] robots.txt가 {CATEGORY_PATH}를 막고 있어 kitbag 수집을 건너뜁니다.")
            return

        diag = Diagnostics(site=self.site_name)
        seen_urls: set[str] = set()

        for page_number in range(1, MAX_PAGES + 1):
            if len(seen_urls) >= MAX_PRODUCTS:
                break

            page_urls = self._list_page_product_urls(page_number, diag)
            if not page_urls:
                break  # 더 이상 상품이 없거나 페이지 실패 -> 종료

            new_urls = [u for u in page_urls if u not in seen_urls]
            seen_urls.update(new_urls)
            new_urls = new_urls[: max(MAX_PRODUCTS - len(seen_urls) + len(new_urls), 0)]

            for i in range(0, len(new_urls), LISTING_BATCH_SIZE):
                chunk = new_urls[i:i + LISTING_BATCH_SIZE]
                batch_products: List[Product] = []
                with cf.ThreadPoolExecutor(max_workers=LISTING_BATCH_SIZE) as executor:
                    for product in executor.map(lambda u: self._fetch_detail(u, diag), chunk):
                        if product:
                            batch_products.append(product)
                if batch_products:
                    on_batch(batch_products)

            time.sleep(0.5)

        diag.print_summary()
        diag.save_log()
