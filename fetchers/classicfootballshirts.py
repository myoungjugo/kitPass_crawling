"""
Classic Football Shirts (Magento 2) fetcher — v2.

## 무엇이 왜 바뀌었나 (쇼핑몰 리스트(수정됨) 문서 기준)
- 대상을 전체 카탈로그(10만개+, sitemap 9개 파일)가 아니라
  **clearance.html ("View All Clearance") 카테고리 하나**로 좁혔다.
- 조건: 축구팀 유니폼만 / 사이즈 S,M,L 중 하나라도 재고 / product style
  (home/away/third/fourth/GK) / condition(Brand New - With Tags) / 저가순 정렬 / 1000~2000개.

## robots.txt 재확인 (v1과 이어지는 결정, 중요)
- 금지된 건 정확히 두 파라미터뿐이다: `product_list_order=`(정렬), `price=`(가격 필터).
  페이지네이션(`?p=N`)이나 페이지당 개수(`?product_list_limit=N`)는 금지 목록에 없다.
- 그래서 이번 버전은 clearance.html을 `?p=N&product_list_limit=80`으로 **페이지네이션만**
  써서 순회한다(= robots.txt 위반 아님). **정렬은 여전히 사이트 파라미터를 쓰지 않고
  core/results_store.py의 가격 오름차순 정렬로 우리 코드 안에서 한다.**
- Product Size / Product Style / Condition 같은 사이트의 "레이어드 네비게이션" 필터도
  보통은 Magento 특유의 숫자 attribute-id 기반 URL 파라미터(예: `?size=123`)로 걸리는데,
  이 파일을 만든 시점엔 그 필터 UI가 JS로 옵션을 렌더링해서 실제 attribute-id 값을
  도구로 확인하지 못했다. **그래서 이번 버전은 사이트 필터 파라미터를 아예 안 쓰고,
  목록/상세 페이지에서 얻은 텍스트(제목, 사이즈 재고, condition 표기)를 우리 코드에서
  직접 판별한다.** robots.txt에 안 걸린 파라미터라도 값을 확실히 모르는 상태에서
  attribute-id를 추측해서 요청하는 것보다 이쪽이 안전하다고 판단했다.
- 나중에 브라우저 개발자도구로 실제 필터 클릭 시 URL을 확인하면, 그 attribute-id를
  이 파일에 상수로 추가해서 애초에 필터링된 목록만 받아오는 최적화가 가능하다.

## 확인된 것
- clearance.html이 페이지네이션을 지원한다: `?p=2`, `?p=3` ... (robots.txt에 안 걸림)
- 목록 페이지에 "Items 1 to 20 of 6558 total" 식의 총 개수 표기가 있다
  (지금은 안 쓰지만, 나중에 진행률 표시에 활용 가능)
- 상세페이지 메타태그는 기존 v1에서 이미 검증된 것 그대로 재사용한다:
    og:title, product:price:amount, product:price:currency, product:availability,
    product:brand, og:image
- 사이즈 재고(jsonConfig) 추출 로직도 v1 그대로 재사용한다 (검증 상태는 동일하게 미검증)

## 확인 안 된 것 (반드시 실제 실행 후 검증!)
- 목록 페이지 상품 카드의 실제 CSS 클래스(`li.product-item`, `a.product-item-link` 등)는
  Magento 2 Luma 테마의 표준 패턴을 가정한 추측이다. 이 파일을 만든 시점엔 목록 페이지가
  JS 렌더링을 거친 상태만 조회 도구로 볼 수 있어서 원본 HTML 구조를 100% 확인 못 했다.
  `_parse_listing_card()`가 매 페이지 상품을 0개 찾으면, 실제 페이지를 curl로 받아서
  상품 카드 셀렉터를 확인하고 이 함수만 고치면 된다 (다른 코드는 안 건드려도 됨).
- "Condition"(Brand New - With Tags 등) 텍스트는 메타태그에 없어서, 페이지 본문에서
  "Condition" 레이블 근처 텍스트를 정규식으로 추출하는 최선의 추측
  (`_extract_condition_best_effort`)을 새로 추가했다. 실제 마크업 확인 후 검증 필요.
  실패(=빈 문자열)하면 조건 미충족으로 취급해서 그냥 제외한다 (모르면 재고/조건이
  맞다고 우기지 않고 제외하는 이 프로젝트의 기존 원칙과 동일).
- Product Style(Home/Away/Third/Fourth/GK) 판별은 별도 필터 요청 없이 제목에 해당
  키워드가 포함되는지로 판별한다(`services.filters.has_target_style`). 대부분의 상품명이
  "... Home Shirt", "... Away Shirt" 식으로 스타일을 포함하는 명명 규칙이라 실제로도
  잘 맞을 것으로 예상되지만, 실행 후 결과 개수로 확인이 필요하다.

## 2단계 구조 (요청 수를 줄이기 위한 최적화)
1. 목록 페이지 순회 -> 제목만으로 판단 가능한 것(팀 유니폼 키워드 + 스타일 키워드)을
   먼저 걸러서 "후보" 목록을 만든다 (사이즈/컨디션은 아직 모름 -> 여기선 판단 안 함).
2. 후보만 상세페이지를 열어서 정확한 가격/사이즈 재고/condition을 확인하고, 최종
   조건(팀 유니폼 + 스타일 + S/M/L 재고 + Brand New with Tags)을 다 통과해야 Product로 반환.
   (사이즈 재고 최종 확인은 core/results_store.py의 공통 필터가 한 번 더 함)

## 안전장치
- CFS_MAX_PAGES(기본 120): clearance 목록을 몇 페이지까지 순회할지
  (전체 82페이지 안팎으로 예상, 넉넉하게 잡음)
- CFS_MAX_PRODUCTS(기본 3000): 사전 필터를 통과한 "후보"를 몇 개까지 상세페이지로
  확인할지 (문서 목표는 최종 1000~2000개라, 필터를 다 통과해도 여유 있게 잡은 상한)
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import time
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from core.diagnostics import Diagnostics
from core.models import Product
from services.filters import is_team_shirt_text, has_target_style, is_target_condition
from services.http_client import make_session, get_with_retry, FetchFailed
from .base import BaseFetcher, OnBatch

BASE_URL = "https://www.classicfootballshirts.co.uk"
CLEARANCE_URL = f"{BASE_URL}/clearance.html"
LISTING_PAGE_SIZE = 80  # 목록 페이지 "Show" 옵션 중 최댓값 (20/40/60/80 확인됨)

DETAIL_BATCH_SIZE = 6
LISTING_DELAY_SEC = 0.3
DETAIL_BATCH_DELAY_SEC = 0.5
REQUEST_TIMEOUT = 20
MAX_PAGES = int(os.environ.get("CFS_MAX_PAGES", "120"))
MAX_PRODUCTS = int(os.environ.get("CFS_MAX_PRODUCTS", "3000"))

# get_text("\n", strip=True) 로 줄바꿈 기준 텍스트를 만든 뒤 그 안에서 "Condition: ..." 찾기
_CONDITION_RE = re.compile(r"Condition:?\s*([A-Za-z0-9\-\s/]{3,60}?)(?:\n|$)", re.IGNORECASE)
_JSON_CONFIG_RE = re.compile(r'"jsonConfig"\s*:\s*(\{.*?\})\s*,\s*"jsonSwatchConfig"', re.DOTALL)


class ClassicFootballShirtsFetcher(BaseFetcher):
    site_name = "classicfootballshirts"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or make_session()

    # -- 1단계: clearance 목록 페이지 순회 (후보 URL 수집 + 사전 필터) ------

    def _listing_url(self, page: int) -> str:
        return f"{CLEARANCE_URL}?p={page}&product_list_limit={LISTING_PAGE_SIZE}"

    def _fetch_listing_page(self, page: int, diag: Diagnostics) -> Optional[BeautifulSoup]:
        try:
            resp = get_with_retry(
                self.session, diag, self._listing_url(page),
                label=f"clearance p{page}", timeout=REQUEST_TIMEOUT,
            )
        except FetchFailed as e:
            print(f"  [DEBUG] clearance 목록 페이지 실패, 스킵: {e}")
            return None
        return BeautifulSoup(resp.text, "html.parser")

    @staticmethod
    def _parse_listing_card(card) -> Optional[dict]:
        """검증 안 된 최선의 추측 (파일 상단 docstring 참고). Magento 2 Luma 표준
        상품 카드 구조를 가정: a.product-item-link(제목+링크)."""
        link = card.select_one("a.product-item-link") or card.select_one("a[href$='.html']")
        if not link or not link.get("href"):
            return None
        title = link.get_text(strip=True)
        if not title:
            return None
        return {"url": link["href"], "title": title}

    def _list_candidate_urls(self, diag: Diagnostics) -> List[dict]:
        """clearance 목록을 순회하며, 제목만으로 판단 가능한 사전 필터
        (팀 유니폼 키워드 + 스타일 키워드)를 통과한 후보만 모은다."""
        candidates: List[dict] = []
        seen_urls: set[str] = set()
        pages_visited = 0

        for page in range(1, MAX_PAGES + 1):
            soup = self._fetch_listing_page(page, diag)
            if soup is None:
                continue  # 이 페이지만 실패, 순회는 계속

            cards = soup.select("li.product-item, div.product-item-info")
            if not cards:
                print(f"  [DEBUG] p{page}: 상품 카드 0개 — 마지막 페이지이거나 "
                      f"셀렉터가 실제 마크업과 안 맞을 수 있음 (docstring 참고)")
                break

            pages_visited += 1
            page_new_urls = 0
            for card in cards:
                parsed = self._parse_listing_card(card)
                if not parsed or parsed["url"] in seen_urls:
                    continue
                seen_urls.add(parsed["url"])
                page_new_urls += 1

                if not is_team_shirt_text(parsed["title"]):
                    continue
                if not has_target_style(parsed["title"]):
                    continue
                candidates.append(parsed)

            if page_new_urls == 0:
                break  # 새 상품이 없다 = 더 이상 페이지가 없거나 중복만 나옴 -> 종료

            if len(candidates) >= MAX_PRODUCTS:
                print(f"  [DEBUG] 사전 필터 통과 {len(candidates)}개로 CFS_MAX_PRODUCTS 도달, 목록 순회 중단")
                break

            time.sleep(LISTING_DELAY_SEC)

        print(f"  [DEBUG] CFS: 목록 {pages_visited}페이지 순회, 사전 필터(유니폼+스타일) 통과 "
              f"{len(candidates)}개 (CFS_MAX_PAGES={MAX_PAGES}, CFS_MAX_PRODUCTS={MAX_PRODUCTS})")
        return candidates[:MAX_PRODUCTS]

    # -- 2단계: 상세페이지에서 정확한 가격/사이즈/condition 확인 -----------

    def _fetch_detail(self, candidate: dict, diag: Diagnostics) -> Optional[Product]:
        url = candidate["url"]
        try:
            resp = get_with_retry(self.session, diag, url, label="detail", timeout=REQUEST_TIMEOUT)
        except FetchFailed as e:
            print(f"  [DEBUG] 상세페이지 실패, 스킵: {url} ({e})")
            return None
        return self._parse_detail(url, resp.text)

    def _parse_detail(self, url: str, html: str) -> Optional[Product]:
        soup = BeautifulSoup(html, "html.parser")

        def meta(name_or_prop: str, attr: str = "name") -> Optional[str]:
            tag = soup.find("meta", attrs={attr: name_or_prop})
            return tag["content"].strip() if tag and tag.get("content") else None

        title = meta("og:title", "property") or (
            soup.title.string.strip() if soup.title and soup.title.string else None
        )
        price_raw = meta("product:price:amount")
        currency = meta("product:price:currency") or "GBP"
        availability = (meta("product:availability") or "").lower()
        image = meta("og:image", "property")
        brand = meta("product:brand") or ""

        if not title or not price_raw:
            return None
        if availability and "in stock" not in availability:
            return None  # 전체 품절

        try:
            price = float(price_raw)
        except ValueError:
            return None

        # 목록 단계는 제목만 봤으니, 상세페이지 제목으로 스타일 조건을 다시 한번 확인
        if not has_target_style(title):
            return None

        condition = self._extract_condition_best_effort(soup)
        if not is_target_condition(condition):
            return None  # Brand New - With Tags가 아니면 제외

        sizes = self._extract_sizes_best_effort(html)
        product_id = url.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")

        return Product(
            site=self.site_name,
            title=title,
            url=url,
            price=price,
            currency=currency,
            image=image,
            sizes_in_stock=sizes,
            product_id=product_id,
            classification_text=f"{title} {brand}",
            condition=condition,
        )

    @staticmethod
    def _extract_condition_best_effort(soup: BeautifulSoup) -> str:
        """검증 안 된 최선의 추측 (파일 상단 docstring 참고). 'Condition' 레이블이
        붙은 텍스트를 페이지 전체 텍스트에서 정규식으로 찾는다. 못 찾으면 빈 문자열
        (빈 문자열 -> is_target_condition()에서 무조건 조건 미충족 처리)."""
        text = soup.get_text("\n", strip=True)
        match = _CONDITION_RE.search(text)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _extract_sizes_best_effort(html: str) -> List[str]:
        """v1과 동일한 로직 (검증 상태도 동일하게 미검증). Magento 2 Luma 스와치
        렌더러의 jsonConfig에서 code=='size'인 attribute를 찾아 products 배열이
        비어있지 않은(=재고 있는) option의 label을 뽑는다."""
        match = _JSON_CONFIG_RE.search(html)
        if not match:
            return []
        try:
            config = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []

        sizes: List[str] = []
        attributes = config.get("attributes", {})
        for attribute in attributes.values() if isinstance(attributes, dict) else []:
            if str(attribute.get("code", "")).lower() != "size":
                continue
            for option in attribute.get("options", []):
                label = option.get("label")
                if label and option.get("products"):
                    sizes.append(str(label))
        return sizes

    # -- 진입점 ------------------------------------------------------------

    def fetch(self, on_batch: OnBatch) -> None:
        diag = Diagnostics(site=self.site_name)
        candidates = self._list_candidate_urls(diag)

        for i in range(0, len(candidates), DETAIL_BATCH_SIZE):
            chunk = candidates[i:i + DETAIL_BATCH_SIZE]
            batch_products: List[Product] = []
            with cf.ThreadPoolExecutor(max_workers=DETAIL_BATCH_SIZE) as executor:
                for product in executor.map(lambda c: self._fetch_detail(c, diag), chunk):
                    if product:
                        batch_products.append(product)
            if batch_products:
                on_batch(batch_products)
            time.sleep(DETAIL_BATCH_DELAY_SEC)

        diag.print_summary()
        diag.save_log()