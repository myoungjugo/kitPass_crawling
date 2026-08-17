"""
사이트 무관 공통 필터 로직.

쇼핑몰 리스트(수정됨) 문서 기준 (CFS 전용으로 좁혀진 최신 조건):
    - 축구 "팀" 유니폼만 (트레이닝복/액세서리/기타 굿즈 제외)
    - S, M, L 중 하나라도 재고 있는 상품만
    - Product Style: Home / Away / Third / Fourth / GK 중 하나
    - Condition: Brand New - With Tags

이 판단 로직을 fetcher마다 따로 구현하지 않고 여기 한 곳에 모아둔다.
fetcher는 "이 사이트에서 뭘 title/category로 볼지"만 결정해서
Product.classification_text / Product.condition 에 채워 넣으면 된다.

## 이번에 바뀐 것
- is_team_shirt(product)의 실제 판별 로직을 is_team_shirt_text(text)로 분리했다.
  fetchers/classicfootballshirts.py가 목록 페이지 단계(아직 Product 객체를 만들기
  전, 제목 문자열만 있는 상태)에서도 같은 판별 로직을 재사용해야 해서다.
  is_team_shirt(product)는 그대로 남아있고 동작도 동일하다 (내부적으로
  is_team_shirt_text를 호출하도록만 바뀜) — 기존 호출부(core/results_store.py)는
  코드 변경 없이 그대로 동작한다.
- has_target_style(), is_target_condition() 신규 추가 (현재는 CFS fetcher만 사용).
"""
from __future__ import annotations

from typing import List, Optional

from core.models import Product

# --- 축구팀 유니폼 판별 -------------------------------------------------

# 이 중 하나라도 포함되어야 "유니폼 후보"로 취급
_INCLUDE_KEYWORDS = ["shirt", "jersey"]

# 후보였더라도 이 키워드가 있으면 제외 (트레이닝복/액세서리/기타 굿즈)
_EXCLUDE_KEYWORDS = [
    "gift card", "voucher",
    "training", "hoodie", "jacket", "track top", "tracksuit",
    "shorts", "socks", "scarf", "cap", "beanie", "bag", "wallet",
    "phone case", "pin badge", "polo", "sweatshirt", "sweat top",
    "mug", "keyring", "poster", "flag", "quarter zip", "1/4 zip",
    "gilet", "boots", "mystery box",
    # "shirt"를 부분 문자열로 포함하지만 유니폼이 아닌 케이스
    "t-shirt", "tee",
]


def is_team_shirt_text(text: str) -> bool:
    """제목(또는 제목+브랜드 등) 문자열만으로 축구팀 유니폼인지 판별.
    Product 객체가 아직 없는 단계(예: 목록 페이지 사전 필터)에서 사용."""
    text = (text or "").lower()
    if not any(keyword in text for keyword in _INCLUDE_KEYWORDS):
        return False
    if any(keyword in text for keyword in _EXCLUDE_KEYWORDS):
        return False
    return True


def is_team_shirt(product: Product) -> bool:
    return is_team_shirt_text(product.classification_text or product.title)


# --- Product Style 판별 (Home/Away/Third/Fourth/GK) ----------------------

_STYLE_KEYWORDS = ["home", "away", "third", "fourth", "4th", "gk", "goalkeeper"]


def has_target_style(text: str) -> bool:
    """쇼핑몰 리스트(수정됨) 문서의 'product style : home away third fourth GK' 조건.
    대부분의 CFS 상품명이 '... Home Shirt', '... Away Shirt' 식으로 스타일을 포함하는
    명명 규칙이라 제목 텍스트만으로 판별한다 (검증 상태는 fetcher 쪽 docstring 참고)."""
    text = (text or "").lower()
    return any(keyword in text for keyword in _STYLE_KEYWORDS)


# --- Condition 판별 (Brand New - With Tags) -------------------------------


def is_target_condition(condition: str) -> bool:
    """쇼핑몰 리스트(수정됨) 문서의 'condition : brand-new with tags' 조건.
    condition이 빈 문자열(=파싱 실패)이면 무조건 False — 모르면 조건을 만족한다고
    우기지 않고 제외한다 (이 프로젝트의 기존 원칙과 동일)."""
    text = (condition or "").lower()
    if not text:
        return False
    return "brand new" in text and "tag" in text


# --- 사이즈 정규화 (S/M/L) ------------------------------------------------

_SIZE_ALIASES = {
    "s": "S", "small": "S", "sm": "S",
    "m": "M", "medium": "M", "med": "M",
    "l": "L", "large": "L", "lg": "L",
}
TARGET_SIZES = {"S", "M", "L"}


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