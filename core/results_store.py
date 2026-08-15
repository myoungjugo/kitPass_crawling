"""
점진적 저장의 핵심 모듈.

사이트별로 배치 단위(cultkits: 페이지 배치, CFS/Kitbag: 상세페이지 배치)로
들어오는 상품을 누적하면서, 배치가 들어올 때마다:
    1. 공통 필터(축구팀 유니폼 / S,M,L 재고) 적용
    2. 지금까지 들어온 전체 사이트 데이터를 합쳐서 가격순 정렬
    3. data/results/latest.json 에 원자적(atomic)으로 갱신 저장

이렇게 하면 "사이트 하나(심지어 배치 하나)가 끝나기도 전에" 이미 화면에서
볼 수 있는 최신 스냅샷이 계속 갱신된다 — 전체 파이프라인이 다 끝나길
기다릴 필요가 없다. 시각화(viz)는 이 latest.json만 읽으면 된다.

주의: 배치마다 전체 리스트를 재정렬 + 파일 통째로 다시 쓰기 때문에,
상품 수가 수만 개 단위로 아주 커지고 배치가 매우 잦아지면 비효율적일 수 있다.
개인용 프로젝트 규모(수천~수만 개)에서는 문제 없지만, 나중에 병목이 되면
"배치 N개마다 한 번만 flush" 같은 스로틀링을 추가하면 된다.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List

from core.models import Product
from services.filters import is_team_shirt, filter_target_sizes

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)
LATEST_PATH = os.path.join(RESULTS_DIR, "latest.json")


class ResultsStore:
    """오케스트레이터가 사이트 개수만큼 생성해서, 각 fetcher의 on_batch 콜백으로 넘긴다."""

    def __init__(self, sites: List[str]):
        self._lock = threading.Lock()
        self._by_site: Dict[str, List[Product]] = {site: [] for site in sites}
        self._site_done: Dict[str, bool] = {site: False for site in sites}
        self._started_at = datetime.now(timezone.utc).isoformat()

    def add_batch(self, site: str, batch: List[Product]) -> None:
        if not batch:
            return

        qualified: List[Product] = []
        for product in batch:
            sizes = filter_target_sizes(product.sizes_in_stock)
            if not sizes:
                continue
            if not is_team_shirt(product):
                continue
            product.sizes_in_stock = sizes  # S/M/L로 정규화된 값으로 교체
            qualified.append(product)

        with self._lock:
            self._by_site.setdefault(site, []).extend(qualified)
            self._flush_locked()

    def mark_site_done(self, site: str) -> None:
        with self._lock:
            self._site_done[site] = True
            self._flush_locked()

    def snapshot(self) -> dict:
        with self._lock:
            self._flush_locked()
        with open(LATEST_PATH, encoding="utf-8") as f:
            return json.load(f)

    # -- 내부 -----------------------------------------------------------

    def _flush_locked(self) -> None:
        """self._lock을 이미 잡고 있는 상태에서만 호출."""
        merged: List[Product] = []
        for products in self._by_site.values():
            merged.extend(products)
        merged.sort(key=lambda p: p.price)

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "started_at": self._started_at,
            "sites_done": dict(self._site_done),
            "count": len(merged),
            "items": [asdict(p) for p in merged],
        }
        self._atomic_write(payload)

    @staticmethod
    def _atomic_write(payload: dict) -> None:
        """viz가 쓰는 도중 파일을 읽어서 깨진 JSON을 보는 일이 없도록
        임시파일에 쓰고 rename(원자적 교체)한다."""
        fd, tmp_path = tempfile.mkstemp(dir=RESULTS_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, LATEST_PATH)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise
