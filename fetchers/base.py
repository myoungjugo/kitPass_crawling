"""
모든 사이트 fetcher가 구현해야 하는 공통 인터페이스.

이전 방식(fetch() -> List[Product] 전체 반환) 대신 콜백 방식으로 바꿨다:
    fetch(on_batch)는 데이터가 준비되는 대로 on_batch(batch)를 여러 번 호출하고,
    끝나면 그냥 return(반환값 없음).

이렇게 바꾼 이유: 점진적 저장(core/results_store.py) 때문이다.
한 사이트, 심지어 한 사이트 안의 한 배치가 끝날 때마다 결과를 즉시
반영하려면, "다 모아서 한 번에 반환"하는 구조로는 불가능하다.

새 사이트를 추가할 때 지켜야 할 것:
    - site_name 클래스 속성 지정
    - fetch(on_batch)에서 적당한 크기(수십~수백 개)로 끊어서 on_batch(batch) 호출
      (한 번에 전체를 모아서 딱 한 번만 호출하면 점진적 저장의 의미가 없어짐)
    - 축구팀 유니폼 판별/사이즈 필터링은 여기서 하지 않는다 (services/filters.py가
      공통으로 처리함) — fetcher는 원본 사이즈 문자열과 classification_text만
      정확하게 채워서 Product를 만들면 됨
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, List

from core.models import Product

OnBatch = Callable[[List[Product]], None]


class BaseFetcher(ABC):
    site_name: str

    @abstractmethod
    def fetch(self, on_batch: OnBatch) -> None:
        """데이터를 가져오는 대로 on_batch(batch)를 호출한다. 반환값 없음."""
        raise NotImplementedError
