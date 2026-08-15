"""
알림 on/off + 가격 상한 설정을 대시보드(app.py)에서 직접 켜고 끌 수 있도록 파일로 영속화.

코드 최종 방안 문서 스펙: "알림 on/off 전환 및 가격 상한 설정을 시각화 화면 내 UI에서".
main.py(수집기)와 app.py(대시보드)가 이 파일 하나를 공유해서 읽고 쓴다 —
대시보드에서 토글하면 다음 수집 실행부터 바로 반영된다.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, asdict
from typing import Optional

from core.results_store import RESULTS_DIR

SETTINGS_PATH = os.path.join(RESULTS_DIR, "notify_settings.json")
_lock = threading.Lock()


@dataclass
class NotifySettings:
    enabled: bool = False
    price_ceiling: Optional[float] = None


def load_settings() -> NotifySettings:
    with _lock:
        if not os.path.exists(SETTINGS_PATH):
            return NotifySettings()
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return NotifySettings()
        return NotifySettings(
            enabled=bool(data.get("enabled", False)),
            price_ceiling=data.get("price_ceiling"),
        )


def save_settings(settings: NotifySettings) -> None:
    with _lock:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(asdict(settings), f, ensure_ascii=False, indent=2)