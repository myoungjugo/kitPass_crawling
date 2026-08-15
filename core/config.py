"""
이번 실행에 포함할 사이트 목록.

United Store(manutd)는 todo 문서 기준 "재고 없음"으로 확인되어 제외했다.
새 사이트를 추가하려면:
    1. fetchers/새사이트.py 에 BaseFetcher를 상속한 클래스 작성
    2. 아래 ACTIVE_FETCHERS에 인스턴스 추가
그 외 (필터링, 정렬, 저장, 알림)는 전혀 손댈 필요 없음 — services/core가 공통 처리.
"""
from __future__ import annotations

from typing import List

from fetchers.base import BaseFetcher
from fetchers.cultkits import CultKitsFetcher
from fetchers.classicfootballshirts import ClassicFootballShirtsFetcher
from fetchers.kitbag import KitbagFetcher


def get_active_fetchers() -> List[BaseFetcher]:
    return [
        CultKitsFetcher(),
        ClassicFootballShirtsFetcher(),
        KitbagFetcher(),
        # UnitedStoreFetcher(),  # todo: 재고 없음 확인되어 제외 (2026-08 기준)
    ]
