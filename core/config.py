"""
이번 실행에 포함할 사이트 목록.

## 이번에 바뀐 것 (Cloudflare 이슈 정리는 별도 문서 참고)
- CFS(Classic Football Shirts): Cloudflare Managed Challenge로 sitemap.xml, clearance.html
  등 경로 상관없이 전부 403 차단되는 게 확인됨 -> 자동화 중단, 수동으로 직접 확인하는
  방식으로 전환. fetchers/classicfootballshirts.py 파일은 남겨두되 config에서 뺌.
- CultKits, Kitbag: 둘 다 다시 활성화.
- United Store(manutd): 이전부터 todo 문서 기준 "재고 없음"으로 확인되어 제외 상태.

새 사이트를 추가하려면:
    1. fetchers/새사이트.py 에 BaseFetcher를 상속한 클래스 작성
    2. 아래 ACTIVE_FETCHERS에 인스턴스 추가
그 외 (필터링, 정렬, 저장, 알림)는 전혀 손댈 필요 없음 — services/core가 공통 처리.
"""
from __future__ import annotations

from typing import List

from fetchers.base import BaseFetcher
from fetchers.cultkits import CultKitsFetcher
from fetchers.kitbag import KitbagFetcher

# CFS는 Cloudflare 차단으로 자동화 중단 (수동 확인으로 전환) -> 비활성.
# from fetchers.classicfootballshirts import ClassicFootballShirtsFetcher


def get_active_fetchers() -> List[BaseFetcher]:
    return [
        CultKitsFetcher(),
        KitbagFetcher(),
        # ClassicFootballShirtsFetcher(),  # Cloudflare Managed Challenge로 자동화 불가 (2026-08)
        # UnitedStoreFetcher(),  # todo: 재고 없음 확인되어 제외 (2026-08 기준)
    ]