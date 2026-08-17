"""
이번 실행에 포함할 사이트 목록.

## 이번에 바뀐 것
쇼핑몰 리스트(수정됨) 문서 기준: "일단 여기(Classic Football Shirts)만 할 것임".

- CultKits: 아예 안 쓰기로 확정 -> 연결 코드(import, 인스턴스 생성) 완전히 제거함.
  fetchers/cultkits.py 파일 자체는 남아있지만 이제 어디서도 안 불러온다.
  더 이상 필요 없으면 그 파일도 지워도 된다 (지금 이 프로젝트 안에서 참조하는 곳 없음).
- Kitbag: 당장은 뺐지만 나중에 다시 쓸 수도 있어서 주석 처리만 해뒀다
  (주석만 풀면 재활성화됨, fetcher 파일도 그대로 둠).
- United Store(manutd): 이전부터 todo 문서 기준 "재고 없음"으로 확인되어 제외 상태.

새 사이트를 추가하려면:
    1. fetchers/새사이트.py 에 BaseFetcher를 상속한 클래스 작성
    2. 아래 ACTIVE_FETCHERS에 인스턴스 추가
그 외 (필터링, 정렬, 저장, 알림)는 전혀 손댈 필요 없음 — services/core가 공통 처리.
"""
from __future__ import annotations

from typing import List

from fetchers.base import BaseFetcher
from fetchers.classicfootballshirts import ClassicFootballShirtsFetcher

# 지금은 CFS 하나만 씀. Kitbag은 나중에 다시 켤 수도 있어서 주석만 처리 (파일은 그대로 있음)
# from fetchers.kitbag import KitbagFetcher


def get_active_fetchers() -> List[BaseFetcher]:
    return [
        ClassicFootballShirtsFetcher(),
        # KitbagFetcher(),
        # UnitedStoreFetcher(),  # todo: 재고 없음 확인되어 제외 (2026-08 기준)
    ]