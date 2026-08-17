"""
대시보드의 '지금 수집하기' 버튼 지원 모듈.

동시성 관리를 파일 기반 잠금(data/results/collection_status.json)으로 하는 이유:
나중에 gunicorn으로 배포하면 워커 프로세스가 여러 개일 수 있는데, 메모리 안의
플래그(예: 전역 변수)는 워커 A가 시작한 수집을 워커 B가 처리한 요청에서는 모른다.
파일 기반이면 어느 워커가 상태를 확인해도 항상 일관된 답을 준다.

스레드로 돌리는 이유: Flask 요청 핸들러 안에서 수집이 다 끝나길 동기적으로
기다리면 CFS만 해도 수십 초~수 분이라 요청이 타임아웃난다. 버튼을 누르면
즉시 응답하고, 실제 수집은 별도 스레드에서 진행하면서 원래 있던 점진적 저장
(core/results_store.py)이 계속 latest.json을 갱신 — 대시보드는 원래 하던
폴링만으로 진행상황을 그대로 보여준다.

한계: 정말 견고하게 하려면 별도 워커 큐(Celery/RQ 등)가 맞지만, 개인용
프로젝트 규모에서는 이 정도로 충분하다.

## 쿨타임 (이번에 추가)
CultKits를 수집 끝나자마자 바로 다시 돌렸더니 페이지 하나가 커넥션 타임아웃 나면서
그 실행 전체가 0개로 끝난 적이 있었다 (조금 있다 다시 돌리니 정상). 정확한 원인
(레이트리밋인지, 일시적 네트워크 문제인지)은 CultKits가 products.json 엔드포인트에
대해 공식적으로 숫자를 발표한 게 없어서 확실친 않지만, 재발 방지 차원에서 수집이
끝난 뒤 일정 시간 동안은 재수집 버튼 자체를 막는 쿨타임을 넣었다.

COLLECTION_COOLDOWN_SEC 환경변수로 조정 가능 (기본 180초 = 3분, 근거 있는 숫자가
아니라 보수적으로 잡은 값 — 너무 자주 막힌다/여유있다 싶으면 조정할 것).
쿨타임은 "성공적으로 끝난 실행"이든 "에러로 끝난 실행"이든 상관없이 finished_at
기준으로 동일하게 적용한다 (에러여도 방금 요청을 많이 날린 뒤일 수 있으므로).
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from core.results_store import RESULTS_DIR

STATUS_PATH = os.path.join(RESULTS_DIR, "collection_status.json")
STALE_AFTER_SEC = 30 * 60  # 이만큼 지나도 "running"이면 죽은 걸로 간주하고 재시작 허용
COOLDOWN_SEC = int(os.environ.get("COLLECTION_COOLDOWN_SEC", "180"))  # 기본 3분
_lock = threading.Lock()


def _write_status(status: str, **extra) -> None:
    payload = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat(), **extra}
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _seconds_since(iso_timestamp: str) -> float:
    return time.time() - datetime.fromisoformat(iso_timestamp).timestamp()


def _raw_status() -> dict:
    if not os.path.exists(STATUS_PATH):
        return {"status": "idle"}
    try:
        with open(STATUS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"status": "idle"}


def get_status() -> dict:
    """idle / running / cooldown / error 중 하나.
    running: 지금 수집 진행 중.
    cooldown: 방금 끝났고(finished_at 기준) 아직 COOLDOWN_SEC이 안 지남 —
      버튼은 막혀 있지만 "실패"는 아니므로 error와는 구분된 상태로 반환한다.
    idle: 바로 새로 수집 시작해도 됨.
    """
    data = _raw_status()

    if data.get("status") == "running" and data.get("started_at"):
        if _seconds_since(data["started_at"]) > STALE_AFTER_SEC:
            return {"status": "idle"}  # 죽은 프로세스로 간주, 재시작 허용
        return data  # 그대로 running

    finished_at = data.get("finished_at")
    if finished_at:
        elapsed = _seconds_since(finished_at)
        remaining = COOLDOWN_SEC - elapsed
        if remaining > 0:
            return {
                **data,
                "status": "cooldown",
                "cooldown_remaining_sec": int(remaining),
                "cooldown_total_sec": COOLDOWN_SEC,
            }

    return data


def start_collection_if_idle(run_fn: Callable[[], None]) -> bool:
    """run_fn: 인자 없이 호출하면 수집 1회를 실행하는 콜러블 (main.collect_once).
    이미 돌고 있거나 쿨타임 중이면 아무것도 안 하고 False. 새로 시작했으면 True."""
    with _lock:
        current_status = get_status().get("status")
        if current_status in ("running", "cooldown"):
            return False
        _write_status("running", started_at=datetime.now(timezone.utc).isoformat())

    def _worker():
        try:
            run_fn()
            _write_status("idle", finished_at=datetime.now(timezone.utc).isoformat())
        except Exception as e:
            _write_status("error", error=str(e), finished_at=datetime.now(timezone.utc).isoformat())

    threading.Thread(target=_worker, daemon=True).start()
    return True