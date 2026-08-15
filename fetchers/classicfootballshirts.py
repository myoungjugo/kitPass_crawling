"""
Classic Football Shirts (Magento 2) fetcher.

## 확인된 것 (실제 상세페이지 fetch로 검증함)
- <head> 메타태그가 안정적으로 존재:
    meta[property=og:title]              -> 제목
    meta[name=product:price:amount]      -> 가격
    meta[name=product:price:currency]    -> 통화
    meta[name=product:availability]      -> "in stock"/"out of stock" (상품 전체 기준, 사이즈별 아님)
    meta[name=product:brand]             -> 브랜드
    meta[property=og:image]              -> 대표 이미지
- 상품명이 유니폼이면 거의 항상 제목에 "Shirt"가 들어가는 명명 규칙이라
  (예: "2024-25 Manchester City Away Shirt"), 공통 필터(services.filters.is_team_shirt)가
  title만으로도 잘 작동할 것으로 보임
- robots.txt: 사이트맵 경로(/pub/media/sitemap/...)와 상품 상세페이지 URL
  (예: /2024-25-manchester-city-away-shirt775086-02.html)은 Disallow 규칙에 안 걸림.
  (정렬/가격 필터 파라미터만 막혀있는데 우리는 그걸 안 씀)

## 확인 안 된 것 (반드시 실제 실행 후 검증 필요!)
- 사이즈별(S/M/L) 재고는 메타태그에 없음. 페이지 안 어딘가의 JS 설정(Magento
  swatch-renderer의 jsonConfig)에 들어있을 가능성이 높은데, 이 파일을 만든 시점엔
  웹 조회 도구가 <script> 블록 내용을 마크다운 변환 과정에서 제거해버려서 실제
  JSON 구조를 못 봤다.
  _extract_sizes_best_effort()는 표준 Magento Luma 테마 패턴을 가정한 최선의
  추측이며, 검증 방법:
    1) 실제 상품 URL을 브라우저로 열고 '페이지 소스 보기' 또는 curl로 원본 HTML을 받아서
       "jsonConfig" 문자열을 검색
    2) 찾은 JSON 구조에 맞춰 이 함수의 정규식/파싱 로직을 수정
  검증 전까지는 사이즈 추출이 실패해서 상품이 전부 걸러질 수 있음
  (안전한 실패 방향으로 설계함 — 모르면 재고 있다고 우기지 않고 그냥 제외)

## 안전장치
- CFS는 상품이 10만 개+ 규모라 매 실행마다 전체 상세페이지를 여는 건 개인용
  AWS 프리티어에 무리다. CFS_MAX_PRODUCTS(기본 1000) 환경변수로 이번 실행에서
  열어볼 상품 수를 제한해뒀다. todo에 있던 "신상만 diff" 방식으로 넘어가기
  전까지의 임시 보호장치.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from core.diagnostics import Diagnostics
from core.models import Product
from services.http_client import make_session, get_with_retry, FetchFailed
from .base import BaseFetcher, OnBatch

BASE_URL = "https://www.classicfootballshirts.co.uk"

# classicfootballshirts_sitemap 문서에 명시된 9개 상품 사이트맵 파일
SITEMAP_FILES = [
    f"{BASE_URL}/pub/media/sitemap/sitemap_product_{i:03d}.xml" for i in range(1, 10)
]

DETAIL_BATCH_SIZE = 6
BATCH_DELAY_SEC = 0.5
REQUEST_TIMEOUT = 20
MAX_PRODUCTS = int(os.environ.get("CFS_MAX_PRODUCTS", "1000"))

_SITEMAP_NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
_JSON_CONFIG_RE = re.compile(r'"jsonConfig"\s*:\s*(\{.*?\})\s*,\s*"jsonSwatchConfig"', re.DOTALL)


class ClassicFootballShirtsFetcher(BaseFetcher):
    site_name = "classicfootballshirts"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or make_session()

    def _list_product_urls(self, diag: Diagnostics) -> List[str]:
        urls: List[str] = []
        for sitemap_url in SITEMAP_FILES:
            try:
                resp = get_with_retry(
                    self.session, diag, sitemap_url,
                    label=f"sitemap {sitemap_url.rsplit('/', 1)[-1]}",
                    timeout=REQUEST_TIMEOUT,
                )
            except FetchFailed as e:
                print(f"  [DEBUG] 사이트맵 실패, 스킵: {e}")
                continue

            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError as e:
                print(f"  [DEBUG] 사이트맵 XML 파싱 실패: {e}")
                continue

            for loc in root.findall(".//sm:url/sm:loc", _SITEMAP_NS):
                if loc.text:
                    urls.append(loc.text.strip())

            if len(urls) >= MAX_PRODUCTS:
                break

        return urls[:MAX_PRODUCTS]

    def _fetch_detail(self, url: str, diag: Diagnostics) -> Optional[Product]:
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

        title = meta("og:title", "property") or (soup.title.string.strip() if soup.title and soup.title.string else None)
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
        )

    @staticmethod
    def _extract_sizes_best_effort(html: str) -> List[str]:
        """검증 안 된 최선의 추측 (파일 상단 docstring 참고).
        Magento 2 Luma 스와치 렌더러의 jsonConfig에서 code=='size'인 attribute를 찾아
        products 배열이 비어있지 않은(=재고 있는) option의 label을 뽑는다."""
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

    def fetch(self, on_batch: OnBatch) -> None:
        diag = Diagnostics(site=self.site_name)
        urls = self._list_product_urls(diag)
        print(f"  [DEBUG] CFS: 이번 실행 대상 상품 URL {len(urls)}개 (CFS_MAX_PRODUCTS={MAX_PRODUCTS})")

        for i in range(0, len(urls), DETAIL_BATCH_SIZE):
            chunk = urls[i:i + DETAIL_BATCH_SIZE]
            batch_products: List[Product] = []
            with cf.ThreadPoolExecutor(max_workers=DETAIL_BATCH_SIZE) as executor:
                for product in executor.map(lambda u: self._fetch_detail(u, diag), chunk):
                    if product:
                        batch_products.append(product)
            if batch_products:
                on_batch(batch_products)
            time.sleep(BATCH_DELAY_SEC)

        diag.print_summary()
        diag.save_log()
