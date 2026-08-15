"""
알림 on/off 기준점 관리.

코드 최종 방안 문서 스펙:
    - 기본값 off
    - on으로 전환한 시점을 기준점으로 저장 → 이후 새로 감지되는 상품만 알림
    - off로 다시 전환 시 알림 중단 (기준점 유지 여부는 "구현 시 결정" 항목이었음)

이 구현에서 정한 기본값 (README에도 명시):
    - off 상태에서는 매 실행마다 기준점을 초기화한다.
      → 다시 on으로 켜면 "그 켠 시점"부터 새로 기준을 잡는다 (문서 스펙 그대로).
    - 한 번 알림을 보낸 상품 ID는 기준점에 계속 누적한다 (같은 상품 중복 알림 방지).
"""
from __future__ import annotations

import json
import os
from typing import Optional, Set

from core.results_store import RESULTS_DIR

STATE_PATH = os.path.join(RESULTS_DIR, "notify_baseline.json")


def load_baseline_ids() -> Optional[Set[str]]:
    """기준점이 없으면(=알림 최초 활성화) None 반환."""
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH, encoding="utf-8") as f:
        return set(json.load(f).get("product_ids", []))


def save_baseline_ids(ids: Set[str]) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump({"product_ids": sorted(ids)}, f, ensure_ascii=False, indent=2)


def clear_baseline() -> None:
    if os.path.exists(STATE_PATH):
        os.remove(STATE_PATH)
