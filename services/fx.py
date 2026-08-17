"""
환율 변환 (GBP/USD -> KRW).

무료, 키 필요없는 open.er-api.com (exchangerate-api.com의 오픈 액세스 엔드포인트)을 쓴다.
주의: "실시간"이 아니라 하루 1번 업데이트되는 데이터다 (완전 실시간 환율은 대부분 유료).
개인 프로젝트에서 "대략 원화로 얼마인지" 보여주는 용도로는 충분하지만, 이 점은
알고 쓰는 게 좋다. (이용약관상 출처 표기 필요 — 대시보드 하단에 작게 표기해둠)

대시보드가 2.5초마다 폴링하기 때문에 요청마다 API를 새로 호출하면 안 된다.
두 단계로 캐싱한다:
    1. 프로세스 메모리 캐시 (같은 프로세스 안에서는 디스크도 안 건드림)
    2. 파일 캐시 (data/results/fx_cache.json) — 프로세스 재시작/여러 gunicorn
       워커 사이에서도 공유됨
둘 다 FX_CACHE_TTL_SEC(기본 1시간)이 지나야 실제 API를 다시 호출한다.
API 호출이 실패하면 오래된 캐시라도 있으면 그걸 쓰고, 그마저 없으면 None
(호출부는 원화 변환을 생략하고 원래 통화만 보여줘야 한다).
"""
from __future__ import annotations

import json
import os
import time
from typing import Dict, Optional

import requests

from core.results_store import RESULTS_DIR

CACHE_PATH = os.path.join(RESULTS_DIR, "fx_cache.json")
TTL_SEC = int(os.environ.get("FX_CACHE_TTL_SEC", str(60 * 60)))
API_URL = "https://open.er-api.com/v6/latest/USD"

_memory_cache: Optional[dict] = None


def _fresh(fetched_at: float) -> bool:
    return (time.time() - fetched_at) < TTL_SEC


def _read_file_cache() -> Optional[dict]:
    if not os.path.exists(CACHE_PATH):
        return None
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _write_file_cache(payload: dict) -> None:
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except OSError:
        pass


def _fetch_from_api() -> Optional[dict]:
    try:
        resp = requests.get(API_URL, timeout=10)
        resp.raise_for_status()
        rates = resp.json().get("rates")
        if not rates:
            return None
        return {"fetched_at": time.time(), "rates": rates}
    except requests.RequestException as e:
        print(f"[fx] 환율 조회 실패: {e}")
        return None


def get_rates_table() -> Optional[Dict[str, float]]:
    """USD 기준 {통화코드: 환율} 표를 반환. 완전히 실패하면 None."""
    global _memory_cache

    if _memory_cache and _fresh(_memory_cache["fetched_at"]):
        return _memory_cache["rates"]

    file_cache = _read_file_cache()
    if file_cache and _fresh(file_cache.get("fetched_at", 0)):
        _memory_cache = file_cache
        return file_cache["rates"]

    fetched = _fetch_from_api()
    if fetched:
        _memory_cache = fetched
        _write_file_cache(fetched)
        return fetched["rates"]

    # API 실패 시 오래된 캐시라도 있으면 완전히 없는 것보단 낫다
    if file_cache:
        _memory_cache = file_cache
        return file_cache["rates"]

    return None


def to_krw(amount: float, currency: str, rates: Optional[Dict[str, float]] = None) -> Optional[int]:
    """rates를 미리 받아오면(get_rates_table() 결과) 파일/네트워크를 다시 안 건드림 —
    리스트 전체를 변환할 땐 반드시 rates를 한 번만 구해서 넘겨줄 것 (매 아이템마다
    get_rates_table()을 부르면 그때마다 캐시 체크 오버헤드가 반복됨)."""
    currency = currency.upper()
    if currency == "KRW":
        return round(amount)

    table = rates if rates is not None else get_rates_table()
    if not table:
        return None

    krw = table.get("KRW")
    cur = table.get(currency)
    if not krw or not cur:
        return None
    return round(amount * (krw / cur))