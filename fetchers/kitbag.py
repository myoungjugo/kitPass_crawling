"""
Kitbag fetcher.

## Cloudflare 차단 여부 (2026-08-17 확인)
CFS에서 겪은 문제 때문에 코드 작업 전에 먼저 확인함: www4.kitbag.com은
카테고리 목록 페이지/상세페이지 둘 다 `requests` 수준 요청으로 정상적인
서버 렌더링 HTML을 그대로 반환한다. Cf-Mitigated, "Just a moment..." 같은
Cloudflare Managed Challenge 신호 전혀 없음. -> CFS와 달리 자동화 가능,
수동 전환 불필요.

## 플랫폼 (기존 문서와 다름, 이전부터 알려져 있던 내용)
kitbag.com은 현재 Fanatics 플랫폼으로 운영됨.
    - URL: https://www4.kitbag.com/en/{league}/{team}/{slug}/o-...+t-...+p-...+z-...
    - 카테고리 목록 페이지 자체가 서버 렌더링되어 제목/가격/이미지/링크 파싱 가능
      (__platform_data__ 같은 JSON blob은 못 찾음 — 없는 걸로 보임)
    - "Football Kits" 카테고리인데도 NFL/MLB/NHL 상품이 섞여서 나옴
      (실제 확인: 버팔로 빌스 NFL 저지, 시카고 컵스 MLB 저지, NY 아일랜더스 NHL 저지가
      football-kits-shirts-home 카테고리 목록에 그대로 섞여 있었음)
      → 축구 리그 URL 경로로 걸러야 함
    - www/www2/www3/www4 여러 미러 도메인 존재, 정확한 규칙 불명 (www4로 고정 사용 중)

## 확인된 것 (2026-08-17)
- 상세페이지 메타태그: meta[property=og:price:amount], og:price:currency,
  og:availability, og:title, og:image — 안정적으로 존재.
  og:availability는 "In stock" 형태 (전체 상품 재고 여부, 사이즈별 아님).
- 목록 페이지에 실제 리그 슬러그가 이렇게 나온다: premier-league, la-liga,
  serie-a, bundesliga, ligue-1, efl-championship, efl-league-one,
  soccer-national-teams (축구) / nfl, mlb, nhl (비축구, 확인됨).
  아래 NON_FOOTBALL_LEAGUE_SLUGS가 이 확인 결과 기준.

## 사이즈별 재고 — 확정됨 (2026-08-17, 실제 실행 + 디버그로 검증 완료)
`tools/debug_kitbag_platform_data.py`로 실제 상세페이지를 받아서 확인함.
페이지 안에 `var __platform_data__={...};` 형태로 임베드된 JS 상태 객체가 있고,
정확히 이 경로에 사이즈별 재고가 들어있다:

    platform_data["pdp-data"]["pdp"]["sizes"] == [
        {"itemId": "212547319", "size": "XS", "available": true, ...},
        {"itemId": "...", "size": "S", "available": false, ...},
        ...
    ]

`__platform_data__`가 `var __platform_data__=`로 시작하는 JS 변수 할당문이라
(끝에 `window.__XXX__=` 같은 정규식으로는 안 걸림) 중괄호 균형을 맞춰가며
JSON 리터럴 부분만 잘라내서 파싱한다 (`_extract_platform_data`). 이 경로를
못 찾을 때만(페이지 구조가 바뀌었거나 __platform_data__ 자체가 없는 경우)
기존 DOM 기반 최선의 추측으로 폴백한다 — 검증 안 된 최후 수단이라 실제로
쓰일 일은 거의 없어야 정상.

## 목록 페이지 상품 URL — 상대경로 (2026-08-17 확인)
목록 페이지의 상품 링크는 절대 URL이 아니라 `href="/en/..."` 형태의
상대경로다 (첫 실행에서 절대경로만 매칭하는 정규식이 0건을 반환해서 발견).
`_PRODUCT_URL_RE`는 절대/상대 둘 다 잡고, `_list_page_product_urls`에서
`urljoin`으로 절대 URL로 변환한다.

## robots.txt
- 실행 시점에 urllib.robotparser로 실제로 확인해서, 막혀있으면 크롤링을 하지 않는다
  (아래 _robots_allow 참고).

## 안전장치
- KITBAG_MAX_PAGES(기본 5) : 카테고리 목록에서 몇 페이지까지 볼지
  (카테고리 전체는 약 17페이지/1162개 상품 — 이 중 상당수가 NFL/MLB/NHL이라
  실제 축구 유니폼 수집량은 원본 개수보다 훨씬 적을 수 있음. 필요하면 늘릴 것.)
- KITBAG_MAX_PRODUCTS(기본 500) : 상세페이지를 몇 개까지 열어볼지
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import re
import time
import urllib.robotparser as robotparser
from typing import Any, List, Optional, Set
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

from core.diagnostics import Diagnostics, ParseStatsLogger
from core.models import Product
from services.http_client import DEFAULT_USER_AGENT, make_session, get_with_retry, FetchFailed
from .base import BaseFetcher, OnBatch

BASE_URL = os.environ.get("KITBAG_BASE_URL", "https://www4.kitbag.com")

# 2026-08-17: "너무 비싼 것만 뜬다" 문제 확인 -> 기존엔 일반 카테고리 하나만,
# 그것도 sortOption=TopSellers(베스트셀러=신상/정가 위주)로, MAX_PAGES 제한(5페이지=
# 360개) 안에서만 봐서 할인 상품이 있는 뒷페이지까지 거의 못 갔음. 대응:
#   1. outlet(sale-items) 카테고리를 기본으로 같이 순회 (할인 상품만 모아둔 곳)
#   2. 일반 카테고리도 sortOption=LowestPrice로 바꿔서 페이지 제한 안에서
#      싼 것부터 보게 함 (TopSellers -> LowestPrice)
# 콤마로 구분해서 여러 카테고리를 순서대로 순회한다 (한쪽이 막히거나 없으면 다음 것으로).
_DEFAULT_CATEGORY_PATHS = (
    "/en/sale-items/os-78+z-845766706-1170525776,"
    "/en/football-kits-shirts-home/d-15954-15956"
)
CATEGORY_PATHS = [
    p.strip() for p in os.environ.get("KITBAG_CATEGORY_PATHS", _DEFAULT_CATEGORY_PATHS).split(",")
    if p.strip()
]
SORT_OPTION = os.environ.get("KITBAG_SORT_OPTION", "LowestPrice")
PAGE_SIZE = 72

# 2026-08-17: 상세페이지 요청이 짧은 시간에 몰리면서(청크당 10개 동시 요청,
# 청크 사이 텀도 없음) 그 직후 kitbag이 목록 페이지까지 포함해서 통째로 403을
# 내기 시작하는 걸 확인함 (요청량으로 인한 순간적 차단으로 추정). 동시 요청 수를
# 줄이고 청크 사이에도 짧은 텀을 준다.
# 2026-08-17: 요청 텀을 이미 한 번 늘렸는데도(0.4초) 순차적인 단일 요청 몇 개만으로도
# 두 번째 요청부터 403이 나는 걸 확인 -> 이전 테스트(짧은 시간에 상세페이지 30개
# 몰아서 요청)로 인한 IP 단위 차단이 아직 안 풀린 것으로 추정. 차단이 풀린 뒤
# 재발 방지 차원에서 텀을 더 넉넉하게 늘려둠.
LISTING_BATCH_SIZE = 3
DETAIL_BATCH_DELAY_SEC = 1.0
MAX_PAGES = int(os.environ.get("KITBAG_MAX_PAGES", "5"))
MAX_PRODUCTS = int(os.environ.get("KITBAG_MAX_PRODUCTS", "500"))
REQUEST_TIMEOUT = 20

# 2026-08-17 실제 확인: football-kits-shirts-home 카테고리에 nfl/mlb/nhl 상품이
# 섞여서 나옴 (버팔로 빌스/시카고 컵스/NY 아일랜더스 등). Fanatics가 여러 스포츠를
# 공유 카탈로그로 운영하는 구조라, 여기 없는 새 비축구 리그가 나중에 추가될 수도
# 있음 — 그런 경우를 대비해 아래 _looks_like_football_title()로 이중 방어.
NON_FOOTBALL_LEAGUE_SLUGS = {
    "nfl", "mlb", "nhl", "nba", "wnba",
    "ncaafb", "ncaamb", "ncaawb", "ncaa",
    "golf", "nascar", "mma", "cricket", "f1", "motorsport",
}

# 실제 kitbag 목록 페이지는 절대 URL이 아니라 상대경로(href="/en/...")로 링크를
# 거는 것으로 확인됨 (2026-08-17, 실행 로그에서 절대경로 매칭 0건 -> 원인 추적 후 확인).
# 절대/상대 둘 다 매칭하고, _list_page_product_urls에서 urljoin으로 절대화한다.
_PRODUCT_URL_RE = re.compile(
    r'href="([^"]*?/o-\d+\+t-\d+\+p-[0-9A-Za-z]+\+z-[0-9A-Za-z-]+)(?:\?[^"]*)?"'
)

_KNOWN_SIZE_LABELS = {"XS", "S", "M", "L", "XL", "2XL", "3XL", "4XL"}

# 2026-08-17 확인: __platform_data__ (var __platform_data__={...}; 형태로 임베드)
# 안에 정확한 경로로 사이즈/재고 정보가 있음 (tools/debug_kitbag_platform_data.py로
# 실제 상세페이지에서 검증됨):
#   platform_data["pdp-data"]["pdp"]["sizes"] == [
#       {"itemId": "...", "size": "XS", "available": true, ...}, ...
#   ]
# 추측 기반 다단계 스캔(JSON-LD/DOM class) 대신 이 정확한 경로를 직접 읽는다.
_PLATFORM_DATA_MARKER = "var __platform_data__="


def _robots_allow(path: str) -> bool:
    rp = robotparser.RobotFileParser()
    rp.set_url(f"{BASE_URL}/robots.txt")
    try:
        rp.read()
    except Exception as e:
        print(f"  [DEBUG] robots.txt 조회 실패 ({e}) — 보수적으로 허용 취급하지만 확인 권장")
        return True
    return rp.can_fetch(DEFAULT_USER_AGENT, path)


def _league_slug(url: str) -> str:
    parts = [p for p in urlparse(url).path.split("/") if p]
    # 보통 ['en', '{league}', '{team}', '{slug}', 'o-...+t-...+p-...+z-...']
    return (parts[1] if len(parts) > 1 and parts[0] == "en" else (parts[0] if parts else "")).lower()


def _is_football_url(url: str) -> bool:
    return _league_slug(url) not in NON_FOOTBALL_LEAGUE_SLUGS


# 2026-08-17: 클리어런스(sale-items) 카테고리는 셔츠 전용이 아니라 전 상품군
# 할인 카테고리라서, 배지/자석/달력/마스크/키링/목걸이/병따개 등 온갖 잡화가
# 축구 리그 URL 아래 섞여 나온다 (실제 확인됨: crest-badge, magnet, calendar,
# face-coverings, collector-pin, bottle-opener, choker-necklace 등). 이런 잡화
# 종류를 하나하나 블록리스트로 막는 건 끝이 없어서, 반대로 뒤집어서 "실제 유니폼
# slug엔 거의 항상 shirt/jersey/kit(minikit, babykit 포함)가 들어있다"는 걸
# 이용한 화이트리스트로 바꿨다.
_APPAREL_SLUG_HINTS = ("shirt", "jersey", "kit")


def _looks_like_apparel_url(url: str) -> bool:
    slug = urlparse(url).path.lower()
    return any(hint in slug for hint in _APPAREL_SLUG_HINTS)


def _looks_like_football_title(title: str) -> bool:
    """리그 블록리스트에 없는 새 비축구 카테고리가 섞여 들어오는 경우를 대비한
    2차 방어선. 실제로 확인한 패턴: 축구 상품은 거의 다 'Shirt'/'Kit', 미국
    스포츠(NFL/MLB/NHL) 상품은 거의 다 'Jersey'만 쓰고 'Shirt'/'Kit'는 안 씀.
    이 휴리스틱 하나만으로 최종 판단하지 않고, 리그 블록리스트를 통과한
    상품에 대해서만 보조적으로 적용한다."""
    text = title.lower()
    if "jersey" in text and "shirt" not in text and "kit" not in text:
        return False
    return True


def _extract_platform_data(html: str) -> Optional[dict]:
    """'var __platform_data__=' 뒤에서 중괄호 균형을 맞춰가며 JSON 객체
    리터럴만 잘라내서 파싱한다 (JS 변수 할당문이라 뒤에 다른 코드가 이어질 수
    있어서, 정규식보다 중괄호 카운팅이 안전함 — 디버그 스크립트에서 검증된 방식)."""
    start = html.find(_PLATFORM_DATA_MARKER)
    if start == -1:
        return None
    start += len(_PLATFORM_DATA_MARKER)
    while start < len(html) and html[start].isspace():
        start += 1
    if start >= len(html) or html[start] != "{":
        return None

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _sizes_from_platform_data(platform_data: dict) -> List[str]:
    """pdp-data.pdp.sizes 배열에서 available=true인 사이즈 라벨만 뽑는다."""
    try:
        raw_sizes = platform_data["pdp-data"]["pdp"]["sizes"]
    except (KeyError, TypeError):
        return []
    if not isinstance(raw_sizes, list):
        return []

    found: Set[str] = set()
    for entry in raw_sizes:
        if not isinstance(entry, dict):
            continue
        label = str(entry.get("size", "")).strip().upper()
        if label in _KNOWN_SIZE_LABELS and entry.get("available") is True:
            found.add(label)
    return sorted(found)


def _extract_sizes_from_dom(soup: BeautifulSoup) -> List[str]:
    """__platform_data__를 못 찾았을 때만 쓰는 최후 폴백 (검증 안 된 추측)."""
    sizes: Set[str] = set()
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


class KitbagFetcher(BaseFetcher):
    site_name = "kitbag"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or make_session()
        self._parse_logger = ParseStatsLogger(site=self.site_name, stage="PARSE")

    def _listing_url(self, category_path: str, page_number: int) -> str:
        sep = "&" if "?" in category_path else "?"
        return f"{BASE_URL}{category_path}{sep}pageSize={PAGE_SIZE}&pageNumber={page_number}&sortOption={SORT_OPTION}"

    def _list_page_product_urls(self, category_path: str, page_number: int, diag: Diagnostics) -> tuple[List[str], int]:
        """반환값: (필터 통과 URL 목록, 필터 적용 전 원본 매칭 개수).
        원본 매칭 개수를 따로 주는 이유: "이 페이지엔 필터 통과 상품이 0개"와
        "이 페이지 자체가 비어서 카테고리가 끝났다"는 완전히 다른 상황인데,
        예전엔 이 둘을 구분 안 해서 화이트리스트 필터가 빡빡해진 뒤로
        (LowestPrice 정렬이라 배지/자석 같은 싼 잡화가 먼저 나옴) 특정 페이지에
        우연히 셔츠가 0개였을 뿐인데 카테고리 전체를 포기해버리는 문제가 있었음."""
        url = self._listing_url(category_path, page_number)
        try:
            resp = get_with_retry(self.session, diag, url, label=f"list {category_path} p{page_number}", timeout=REQUEST_TIMEOUT)
        except FetchFailed as e:
            print(f"  [DEBUG] 목록 페이지 실패, 스킵: {e}")
            return [], -1  # -1 = 요청 자체가 실패 (진짜 끝인지 판단 불가)
        raw_matches = {urljoin(BASE_URL + "/", m.group(1)) for m in _PRODUCT_URL_RE.finditer(resp.text)}
        football_urls = [u for u in raw_matches if _is_football_url(u) and _looks_like_apparel_url(u)]
        print(
            f"  [DEBUG] kitbag {category_path} p{page_number}: 응답 {len(resp.text)/1024:.1f}KB, "
            f"상품 URL {len(raw_matches)}개 매칭 -> 축구+잡화필터 후 {len(football_urls)}개"
        )
        return football_urls, len(raw_matches)

    def _fetch_detail(self, url: str, diag: Diagnostics) -> Optional[Product]:
        try:
            resp = get_with_retry(self.session, diag, url, label="detail", timeout=REQUEST_TIMEOUT)
        except FetchFailed as e:
            print(f"  [DEBUG] 상세페이지 실패, 스킵: {url} ({e})")
            # 이전엔 이 실패가 _parse_detail() 안의 진단 샘플에 안 잡혀서
            # "통과 0개인데 탈락 샘플도 없음"이라는 사각지대가 있었음 — 여기서도 남긴다.
            if self._parse_logger.should_log():
                self._parse_logger.add_sample("상세페이지 요청 실패(403/네트워크 등)", url)
            return None
        return self._parse_detail(url, resp.text)

    def _extract_sizes(self, html: str, soup: BeautifulSoup) -> tuple[List[str], str]:
        """1순위: __platform_data__["pdp-data"]["pdp"]["sizes"] (2026-08-17 실제
        구조 확인됨, tools/debug_kitbag_platform_data.py로 검증). 못 찾으면 DOM
        폴백(추측 기반, 최후 수단)."""
        platform_data = _extract_platform_data(html)
        if platform_data is not None:
            sizes = _sizes_from_platform_data(platform_data)
            if sizes:
                return sizes, "platform-data"
            # __platform_data__는 찾았는데 sizes가 비었으면 스크립트 구조 자체가
            # 바뀐 것일 수 있으니 DOM으로 넘어가기 전에 구분해서 로그 남김
            return _extract_sizes_from_dom(soup), "platform-data-empty->dom-fallback"
        return _extract_sizes_from_dom(soup), "no-platform-data->dom-fallback"

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

        should_log = self._parse_logger.should_log()

        if not title or not price_raw:
            if should_log:
                self._parse_logger.add_sample("meta(title/price) 없음", url)
            return None
        if availability and "in stock" not in availability:
            if should_log:
                self._parse_logger.add_sample("og:availability가 In stock 아님", title)
            return None
        if not _looks_like_football_title(title):
            if should_log:
                self._parse_logger.add_sample("제목상 비축구(Jersey류) 의심으로 제외", title)
            return None

        try:
            price = float(price_raw)
        except ValueError:
            if should_log:
                self._parse_logger.add_sample("price 파싱 실패", (title, price_raw))
            return None

        sizes, strategy = self._extract_sizes(html, soup)
        if should_log:
            if sizes:
                self._parse_logger.mark_ok()
            else:
                self._parse_logger.add_sample(f"사이즈 파싱 실패 (전략={strategy})", title)

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

    def fetch(self, on_batch: OnBatch) -> None:
        diag = Diagnostics(site=self.site_name)
        seen_urls: set[str] = set()
        first_batch_logged = False

        for category_path in CATEGORY_PATHS:
            if len(seen_urls) >= MAX_PRODUCTS:
                break
            if not _robots_allow(category_path):
                print(f"  [DEBUG] robots.txt가 {category_path}를 막고 있어 이 카테고리는 건너뜁니다.")
                continue

            for page_number in range(1, MAX_PAGES + 1):
                if len(seen_urls) >= MAX_PRODUCTS:
                    break

                page_urls, raw_count = self._list_page_product_urls(category_path, page_number, diag)
                if raw_count <= 0:
                    # -1(요청 자체 실패) 또는 0(진짜 빈 페이지) -> 이 카테고리는 그만
                    # (요청 실패 상태에서 다음 페이지를 계속 두드리는 건 피함)
                    break
                if not page_urls:
                    time.sleep(1.5)
                    continue

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
                    time.sleep(DETAIL_BATCH_DELAY_SEC)

                if not first_batch_logged and self._parse_logger.should_log():
                    self._parse_logger.print_summary(total=len(new_urls))
                    first_batch_logged = True

                time.sleep(0.5)

        diag.print_summary()
        diag.save_log()


if __name__ == "__main__":
    # 단독 테스트용: python -m fetchers.kitbag
    fetcher = KitbagFetcher()
    collected: List[Product] = []
    t0 = time.time()
    fetcher.fetch(on_batch=collected.extend)
    elapsed = time.time() - t0
    print(f"총 {len(collected)}개 상품 수집 ({elapsed:.1f}초, 필터 적용 전)")
    for item in collected[:5]:
        print(item)