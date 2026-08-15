"""
사이트 무관 공통 데이터 모델.

모든 fetcher(cultkits, cfs, kitbag, ...)는 사이트별로 파싱한 결과를
반드시 이 Product 형태로 변환해서 내보내야 한다.
그래야 core/results_store.py, services/filters.py, services/notifier.py 같은
공통 로직이 사이트가 몇 개든, 새 사이트가 추가되든 그대로 재사용된다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class Product:
    site: str                      # "cultkits", "classicfootballshirts", "kitbag" 등
    title: str
    url: str
    price: float
    currency: str
    image: str | None
    sizes_in_stock: List[str]      # 원본 사이즈 문자열 그대로 (정규화는 services.filters가 담당)
    product_id: str
    # 축구팀 유니폼 판별에 쓸 텍스트. 사이트마다 신뢰할 수 있는 소스가 다르므로
    # (title만 있는 곳도 있고, title+category가 더 정확한 곳도 있음) fetcher가 채워 넣는다.
    # 비어있으면 필터 단계에서 title로 대체한다.
    classification_text: str = ""

    def __post_init__(self):
        if not self.classification_text:
            self.classification_text = self.title
