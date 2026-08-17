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

## ParseStatsLogger (이번에 추가)
"요청은 다 성공했는데 최종 상품이 0개"인 문제를 CFS, CultKits에서 각각 디버깅하면서,
"사이트당 한 번만 로그 찍기 / 실패 원인별로 샘플 몇 개만 모으기 / 정리해서 출력하기"
라는 보일러플레이트를 fetcher마다 따로 짜고 있었다. "무엇을 실패 원인으로 볼지"는
사이트마다 원본 데이터 구조가 달라서(Shopify variants vs Magento jsonConfig vs meta
태그) 공통화할 수 없지만, 그 판단을 뺀 나머지(한 번만 찍기/샘플 모으기/출력 포맷)는
공통으로 뺄 수 있어서 여기로 옮겼다. fetcher/공통 필터 코드는 이제 이 클래스의
add_sample()/print_summary()만 호출하면 된다.
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


class ParseStatsLogger:
    """"원본 개수 대비 파싱/필터 통과 개수 + 탈락 원인별 샘플"을 사이트당 딱 한 번만
    찍기 위한 공통 유틸. 실패 "원인"을 뭘로 분류할지는 호출부(fetcher, results_store)가
    정하고, 이 클래스는 그 결과를 모아서 정리된 형태로 출력하는 보일러플레이트만 담당한다.

    사용 예:
        logger = ParseStatsLogger(site="cultkits", stage="PARSE")
        if logger.should_log():
            for item in raw_items:
                ok = ...
                if ok:
                    logger.mark_ok()
                else:
                    logger.add_sample("price 없음", item_title)
            logger.print_summary(total=len(raw_items))
    """

    def __init__(self, site: str, stage: str, max_samples: int = 3):
        self.site = site
        self.stage = stage  # 로그 접두어에 쓸 이름, 예: "PARSE", "FILTER"
        self.max_samples = max_samples
        self._logged = False
        self._lock = threading.Lock()
        self._samples: dict[str, list] = {}
        self._ok_count = 0

    def should_log(self) -> bool:
        """사이트당 딱 한 번만 True를 반환 (그 뒤로는 계속 False) — 매 배치/페이지마다
        중복으로 로그가 쌓이는 걸 막기 위함."""
        with self._lock:
            if self._logged:
                return False
            self._logged = True
            return True

    def mark_ok(self) -> None:
        self._ok_count += 1

    def add_sample(self, category: str, label) -> None:
        bucket = self._samples.setdefault(category, [])
        if len(bucket) < self.max_samples:
            bucket.append(label)

    def print_summary(self, total: int) -> None:
        print(f"  [{self.stage}-DEBUG][{self.site}] 원본 {total}개 중 통과 {self._ok_count}개")
        if not self._samples and self._ok_count == 0 and total > 0:
            print(f"  [{self.stage}-DEBUG][{self.site}] 이상함: 통과 0개인데 탈락 샘플도 못 모았음 "
                  f"— 분류 로직 자체를 다시 봐야 할 수 있음")
        for category, samples in self._samples.items():
            print(f"  [{self.stage}-DEBUG][{self.site}] '{category}'로 탈락한 샘플: {samples}")