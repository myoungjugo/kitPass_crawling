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

## 진단 로그 (이번에 리팩터링)
"요청은 다 성공했는데 최종 상품이 0개"인 문제를 추적하려고 필터 탈락 원인
(사이즈 불일치 / 유니폼 키워드 불일치) 로그를 사이트당 한 번씩 찍는데, 그
보일러플레이트(한 번만 찍기/샘플 모으기/정리해서 출력)는 fetchers/cultkits.py의
파싱 단계 로그와 겹쳐서 core/diagnostics.py의 ParseStatsLogger 공통 유틸로 뺐다.
필터 로직 자체(is_team_shirt, filter_target_sizes)는 전혀 안 바뀌었다.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Dict, List

from core.diagnostics import ParseStatsLogger
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
        # 사이트당 필터 통과/탈락 현황을 첫 배치에서 한 번만 로그로 남기기 위한 공통 유틸
        self._filter_loggers: Dict[str, ParseStatsLogger] = {
            site: ParseStatsLogger(site=site, stage="FILTER") for site in sites
        }

    def add_batch(self, site: str, batch: List[Product]) -> None:
        if not batch:
            return

        logger = self._filter_loggers.setdefault(site, ParseStatsLogger(site=site, stage="FILTER"))
        should_log = logger.should_log()

        qualified: List[Product] = []
        for product in batch:
            sizes = filter_target_sizes(product.sizes_in_stock)
            shirt_ok = is_team_shirt(product)

            if not sizes:
                if should_log:
                    logger.add_sample("사이즈(S/M/L) 불일치", (product.title, product.sizes_in_stock))
                continue
            if not shirt_ok:
                if should_log:
                    logger.add_sample("유니폼 키워드(shirt/jersey) 불일치", product.title)
                continue

            if should_log:
                logger.mark_ok()
            product.sizes_in_stock = sizes  # S/M/L로 정규화된 값으로 교체
            qualified.append(product)

        if should_log:
            logger.print_summary(total=len(batch))

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