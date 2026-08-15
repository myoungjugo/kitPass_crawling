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
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from core.results_store import RESULTS_DIR

STATUS_PATH = os.path.join(RESULTS_DIR, "collection_status.json")
STALE_AFTER_SEC = 30 * 60  # 이만큼 지나도 "running"이면 죽은 걸로 간주하고 재시작 허용
_lock = threading.Lock()


def _write_status(status: str, **extra) -> None:
    payload = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat(), **extra}
    with open(STATUS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def get_status() -> dict:
    if not os.path.exists(STATUS_PATH):
        return {"status": "idle"}
    try:
        with open(STATUS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"status": "idle"}

    if data.get("status") == "running" and data.get("started_at"):
        elapsed = time.time() - datetime.fromisoformat(data["started_at"]).timestamp()
        if elapsed > STALE_AFTER_SEC:
            return {"status": "idle"}  # 죽은 프로세스로 간주, 재시작 허용
    return data


def start_collection_if_idle(run_fn: Callable[[], None]) -> bool:
    """run_fn: 인자 없이 호출하면 수집 1회를 실행하는 콜러블 (main.collect_once).
    이미 돌고 있으면 아무것도 안 하고 False. 새로 시작했으면 True."""
    with _lock:
        if get_status().get("status") == "running":
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