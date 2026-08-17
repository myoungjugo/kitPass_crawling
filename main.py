"""
실행 진입점.

    python main.py

동작:
    1. core/config.py에 등록된 사이트들을 병렬로 수집 (core/orchestrator.py)
       - 배치가 들어올 때마다 data/results/latest.json이 즉시 갱신됨
       - 이 파일은 전체가 끝날 때까지 기다렸다가 마지막에 요약만 출력
    2. 알림이 켜져 있으면(대시보드 app.py에서 토글, services/notify_settings.py에 저장됨)
       이전 실행 대비 새로 나타난 상품을 Discord로 전송한다
       (최초 활성화 시에는 기준점만 저장하고 알림은 안 보냄 — 그 시점 이전
       상품들까지 전부 "신상"으로 오인해서 알림 폭탄이 가는 걸 방지)

수집(main.py)과 화면(app.py)은 완전히 분리되어 있다: 이 파일은 크롤링만 하고
data/results/latest.json에 쓰기만 한다. cron 등으로 이 파일만 주기 실행하면 되고,
app.py는 그 결과를 읽어서 보여주기만 하므로 대시보드를 아무리 새로고침해도
쇼핑몰에 추가 부하가 가지 않는다.
"""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()  # .env 값을 os.environ에 실제로 올림. 이게 없으면 core.config가
                # import될 때 fetchers/kitbag.py 등이 읽는 KITBAG_* 값도 전부 무시됨.

import time

from core.config import get_active_fetchers
from core.models import Product
from core.orchestrator import run_all
from services.notifier import send_new_items
from services.notify_settings import load_settings
from services.notify_state import load_baseline_ids, save_baseline_ids, clear_baseline


def collect_once() -> dict:
    """수집 1회 실행하고 스냅샷을 반환한다.
    CLI(`python main.py`)와 대시보드의 '지금 수집하기' 버튼(services/collection_runner.py)이
    이 함수 하나를 공유해서 쓴다 — 로직이 두 군데로 갈라지지 않게."""
    t0 = time.time()
    fetchers = get_active_fetchers()
    print(f"수집 시작: {[f.site_name for f in fetchers]}")

    store = run_all(fetchers)
    snapshot = store.snapshot()

    elapsed = time.time() - t0
    print(f"\n완료: {snapshot['count']}개 상품 (조건 충족분), {elapsed:.1f}초")
    print(f"결과 파일: data/results/latest.json")

    _handle_notifications(snapshot)
    return snapshot


def main() -> None:
    collect_once()


def _handle_notifications(snapshot: dict) -> None:
    settings = load_settings()  # 대시보드에서 토글한 값 (services/notify_settings.py)
    if not settings.enabled:
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
    send_new_items(new_items, settings.price_ceiling)
    save_baseline_ids(current_ids | baseline)  # 한 번 알림 보낸 상품은 계속 기준점에 누적(중복 알림 방지)


if __name__ == "__main__":
    main()