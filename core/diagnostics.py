"""
범용 네트워크 진단 도구.

여러 fetcher(cultkits, cfs, unitedstore, kitbag)가 공통으로 재사용.
지금은 "왜 느린지" 확인용이지만, 운영 시작한 뒤에도
- 특정 사이트가 갑자기 느려졌는지 (레이트리밋/구조변경 감지)
- 에러가 늘었는지
- 어느 요청이 병목인지
를 같은 방식으로 계속 확인할 수 있게 만든 범용 모듈.

사용법:
    from core.diagnostics import Diagnostics

    diag = Diagnostics(site="cultkits")
    resp = diag.timed_get(session, url, params={...}, label="page 1")
    ...
    diag.print_summary()
    diag.save_log()   # data/diagnostics/{site}_{시각}.jsonl 로 저장

환경변수:
    DIAG_VERBOSE=0   콘솔에 요청별 로그 안 찍음 (기본: 켜짐)
    DIAG_LOG=0        파일로 저장 안 함 (기본: 켜짐)
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

import requests

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
DIAG_DIR = os.path.join(DATA_DIR, "diagnostics")
os.makedirs(DIAG_DIR, exist_ok=True)

VERBOSE_DEFAULT = os.environ.get("DIAG_VERBOSE", "1") == "1"
LOG_TO_FILE = os.environ.get("DIAG_LOG", "1") == "1"


@dataclass
class RequestRecord:
    label: str
    elapsed_sec: float
    size_bytes: int
    status_code: Optional[int]
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class Diagnostics:
    """사이트 하나(=한 fetcher 실행)에 대한 요청 기록을 모아서 요약/저장."""

    def __init__(self, site: str, verbose: Optional[bool] = None):
        self.site = site
        self.verbose = VERBOSE_DEFAULT if verbose is None else verbose
        self.records: list[RequestRecord] = []
        self._lock = threading.Lock()  # 병렬 fetcher에서 동시에 기록해도 안전하게
        self._run_start = time.perf_counter()

    def timed_get(self, session: requests.Session, url: str, *, label: str, **kwargs) -> requests.Response:
        """requests.Session.get을 감싸서 소요시간/용량/에러를 자동 기록.
        예외가 나도 기록은 남기고 그대로 다시 raise (호출부 로직은 그대로 유지)."""
        t0 = time.perf_counter()
        try:
            resp = session.get(url, **kwargs)
            elapsed = time.perf_counter() - t0
            size = len(resp.content)
            self._record(label, elapsed, size, resp.status_code, None)
            return resp
        except Exception as e:
            elapsed = time.perf_counter() - t0
            self._record(label, elapsed, 0, None, str(e))
            raise

    def _record(self, label: str, elapsed: float, size: int, status_code: Optional[int], error: Optional[str]):
        rec = RequestRecord(label=label, elapsed_sec=elapsed, size_bytes=size, status_code=status_code, error=error)
        with self._lock:
            self.records.append(rec)
        if self.verbose:
            if error:
                print(f"  [{self.site}] {label:<12} 실패: {error}")
            else:
                kb = size / 1024
                print(f"  [{self.site}] {label:<12} {elapsed:5.2f}초 | {kb:7.1f} KB | HTTP {status_code}")

    def summary(self) -> dict:
        ok = [r for r in self.records if r.error is None]
        errors = [r for r in self.records if r.error is not None]
        times = [r.elapsed_sec for r in ok]
        sizes = [r.size_bytes for r in ok]
        total_elapsed = time.perf_counter() - self._run_start

        return {
            "site": self.site,
            "total_requests": len(self.records),
            "success": len(ok),
            "errors": len(errors),
            "total_wall_time_sec": round(total_elapsed, 2),
            "avg_request_sec": round(sum(times) / len(times), 2) if times else None,
            "min_request_sec": round(min(times), 2) if times else None,
            "max_request_sec": round(max(times), 2) if times else None,
            "total_downloaded_kb": round(sum(sizes) / 1024, 1) if sizes else 0,
        }

    def print_summary(self):
        s = self.summary()
        print(
            f"\n[{s['site']}] 진단 요약 — "
            f"요청 {s['total_requests']}건(성공 {s['success']}, 실패 {s['errors']}) | "
            f"총 {s['total_wall_time_sec']}초 | "
            f"평균 {s['avg_request_sec']}초 (최소 {s['min_request_sec']} / 최대 {s['max_request_sec']}) | "
            f"다운로드 {s['total_downloaded_kb']} KB"
        )
        if s["errors"]:
            print("  실패한 요청:")
            for r in self.records:
                if r.error:
                    print(f"    - {r.label}: {r.error}")

    def save_log(self) -> Optional[str]:
        """운영 중 문제 추적용으로 파일에 남김. data/diagnostics/{site}_{시각}.jsonl"""
        if not LOG_TO_FILE:
            return None
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = os.path.join(DIAG_DIR, f"{self.site}_{ts}.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"summary": self.summary()}, ensure_ascii=False) + "\n")
            for r in self.records:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
        return path
