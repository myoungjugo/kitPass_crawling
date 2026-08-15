"""
실행 진입점.

    python main.py

동작:
    1. core/config.py에 등록된 사이트들을 병렬로 수집 (core/orchestrator.py)
       - 배치가 들어올 때마다 data/results/latest.json이 즉시 갱신됨
       - 이 파일은 전체가 끝날 때까지 기다렸다가 마지막에 요약만 출력
    2. NOTIFY_ENABLED=1 이면, 이전 실행 대비 새로 나타난 상품을 Discord로 전송
       (최초 활성화 시에는 기준점만 저장하고 알림은 안 보냄 — 그 시점 이전
       상품들까지 전부 "신상"으로 오인해서 알림 폭탄이 가는 걸 방지)
"""
from __future__ import annotations

import os
import time

from core.config import get_active_fetchers
from core.models import Product
from core.orchestrator import run_all
from services.notifier import send_new_items
from services.notify_state import load_baseline_ids, save_baseline_ids, clear_baseline


def main() -> None:
    t0 = time.time()
    fetchers = get_active_fetchers()
    print(f"수집 시작: {[f.site_name for f in fetchers]}")

    store = run_all(fetchers)
    snapshot = store.snapshot()

    elapsed = time.time() - t0
    print(f"\n완료: {snapshot['count']}개 상품 (조건 충족분), {elapsed:.1f}초")
    print(f"결과 파일: data/results/latest.json")

    _handle_notifications(snapshot)


def _handle_notifications(snapshot: dict) -> None:
    if os.environ.get("NOTIFY_ENABLED") != "1":
        clear_baseline()
        return

    current_ids = {item["product_id"] for item in snapshot["items"]}
    baseline = load_baseline_ids()

    if baseline is None:
        print("[notify] 알림 최초 활성화 — 현재 상태를 기준점으로 저장 (이번엔 알림 전송 안 함)")
        save_baseline_ids(current_ids)
        return

    new_ids = current_ids - baseline
    if not new_ids:
        print("[notify] 기준점 이후 신상 없음")
        return

    new_items = [Product(**item) for item in snapshot["items"] if item["product_id"] in new_ids]
    price_ceiling_raw = os.environ.get("NOTIFY_PRICE_CEILING")
    price_ceiling = float(price_ceiling_raw) if price_ceiling_raw else None

    send_new_items(new_items, price_ceiling)
    save_baseline_ids(current_ids | baseline)  # 한 번 알림 보낸 상품은 계속 기준점에 누적(중복 알림 방지)


if __name__ == "__main__":
    main()
