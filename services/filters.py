"""
사이트 무관 공통 필터 로직.

쇼핑몰 조건/정 문서 기준:
    - 축구 "팀" 유니폼만 (트레이닝복/액세서리/기타 굿즈 제외)
    - S, M, L 중 하나라도 재고 있는 상품만

이 판단 로직을 fetcher마다 따로 구현하지 않고 여기 한 곳에 모아둔다.
fetcher는 "이 사이트에서 뭘 title/category로 볼지"만 결정해서
Product.classification_text에 채워 넣으면 된다 (사이트별 파싱은 fetcher 담당,
판별 규칙은 이 파일 담당 — 새 사이트가 추가돼도 이 파일은 안 건드려도 됨).
"""
from __future__ import annotations

from typing import List, Optional

from core.models import Product

# --- 축구팀 유니폼 판별 -------------------------------------------------

# 이 중 하나라도 포함되어야 "유니폼 후보"로 취급
_INCLUDE_KEYWORDS = ["shirt", "jersey"]

# 2026-08-17: 쇼핑몰_필터링_조건 문서 반영. kitbag에서 어린이용 유니폼
# ("Baby Kit", "- Kids", "- Infants" 등)이 실제로 섞여 나오는 게 확인돼서
# 어린이용/여성용/긴팔/구버전 제외 키워드를 추가함.
_EXCLUDE_KEYWORDS = [
    "gift card", "voucher",
    "training", "hoodie", "jacket", "track top", "tracksuit",
    "shorts", "socks", "scarf", "cap", "beanie", "bag", "wallet",
    "phone case", "pin badge", "polo", "sweatshirt", "sweat top",
    "mug", "keyring", "poster", "flag", "quarter zip", "1/4 zip",
    "gilet", "boots", "mystery box",
    # "shirt"를 부분 문자열로 포함하지만 유니폼이 아닌 케이스
    "t-shirt", "tee",

    # --- 어린이용 제외 (성인용만) ---
    "kids", "kid's", "junior", "juniors", "youth", "toddler", "infant",
    "baby", "babykit", "baby kit", "mini kit", "minikit", "children",
    "newborn", "romper",

    # --- 여성용 제외 (남자 유니폼만) ---
    "womens", "women's", "ladies", "girls", "girl's",

    # --- 긴팔 제외 (반팔만) ---
    "long sleeve",

   
]


def is_team_shirt(product: Product) -> bool:
    text = (product.classification_text or product.title).lower()
    if not any(keyword in text for keyword in _INCLUDE_KEYWORDS):
        return False
    if any(keyword in text for keyword in _EXCLUDE_KEYWORDS):
        return False
    return True


# --- 사이즈 정규화 (S/M/L) ------------------------------------------------

_SIZE_ALIASES = {
    "s": "S", "small": "S", "sm": "S",
    "m": "M", "medium": "M", "med": "M",
    "l": "L", "large": "L", "lg": "L",
    "xl": "XL", "extra large": "XL", "x-large": "XL", "xlarge": "XL",
}
# 2026-08-17: 쇼핑몰_필터링_조건 문서 기준 S/M/L에 XL 추가.
TARGET_SIZES = {"S", "M", "L", "XL"}


def normalize_size(raw: str) -> Optional[str]:
    """'S / 36', 'Small', 'M' 같은 다양한 표기를 S/M/L로 통일. 매칭 안 되면 None."""
    if not raw:
        return None
    # "S / 36", "M(38)" 같은 형태 대비 — 첫 토큰만 사용
    key = raw.strip().lower().split("/")[0].split("(")[0].strip()
    return _SIZE_ALIASES.get(key)


def filter_target_sizes(raw_sizes: List[str]) -> List[str]:
    """원본 사이즈 목록에서 S/M/L만 정규화해서 반환 (정렬됨). 없으면 빈 리스트."""
    normalized = {normalize_size(s) for s in raw_sizes}
    normalized.discard(None)
    return sorted(normalized & TARGET_SIZES)
