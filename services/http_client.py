"""
모든 fetcher가 공유하는 HTTP 요청 + 재시도 로직.

기존 cultkits.py에 있던 버그를 여기서 고쳤다:
    - 예전: diag.timed_get()이 던지는 네트워크 예외(타임아웃, 커넥션 에러 등)를
      잡아주는 코드가 없어서, 429 말고는 사실상 재시도가 안 됐음.
      그 결과 순간적인 네트워크 오류가 fetch() 쪽에서 "빈 페이지(=카탈로그 끝)"로
      오인되어 그 뒤 상품을 통째로 못 가져올 수 있었음.
    - 지금: get_with_retry()가 모든 예외/429/5xx를 재시도하고,
      재시도를 다 소진하면 FetchFailed를 던진다.
      "요청 실패"와 "정상적으로 빈 응답(진짜 끝)"은 항상 구분해서 다뤄야 한다 —
      호출부(fetcher)는 FetchFailed를 잡아서 "이 페이지만 스킵"으로 처리하고,
      절대 "카탈로그가 끝났다"는 신호로 착각하면 안 된다.
"""
from __future__ import annotations

import time
from typing import Optional

import requests

from core.diagnostics import Diagnostics

DEFAULT_USER_AGENT = "uniform-tracker/0.1 (personal use, respects robots.txt)"


def make_session(user_agent: str = DEFAULT_USER_AGENT) -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    return session


class FetchFailed(Exception):
    """max_retries를 다 소진하고도 실패했을 때. '진짜 빈 응답'과 구분하기 위한 전용 예외."""


def get_with_retry(
    session: requests.Session,
    diag: Diagnostics,
    url: str,
    *,
    label: str,
    max_retries: int = 3,
    timeout: int = 20,
    **kwargs,
) -> requests.Response:
    """
    - 429: Retry-After 헤더만큼 대기 후 재시도
    - 5xx / 커넥션 에러 / 타임아웃: 지수 백오프(1, 2, 4초...) 후 재시도
    - 4xx(429 제외): 재시도 없이 즉시 FetchFailed
    - max_retries 다 소진: FetchFailed(호출부가 "실패"와 "끝"을 구분할 수 있게)
    """
    last_error: Optional[str] = None

    for attempt in range(max_retries):
        attempt_label = label if attempt == 0 else f"{label} (재시도 {attempt + 1})"
        try:
            resp = diag.timed_get(session, url, timeout=timeout, label=attempt_label, **kwargs)
        except requests.RequestException as e:
            last_error = f"{type(e).__name__}: {e}"
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 429:
            wait = float(resp.headers.get("Retry-After", 2 ** attempt))
            time.sleep(wait)
            continue

        if 500 <= resp.status_code < 600:
            last_error = f"HTTP {resp.status_code}"
            time.sleep(2 ** attempt)
            continue

        if resp.status_code >= 400:
            # 4xx(429 제외)는 재시도해도 대부분 안 바뀌므로 바로 실패 처리
            raise FetchFailed(f"{label}: HTTP {resp.status_code} (재시도 안 함)")

        return resp

    raise FetchFailed(f"{label}: {max_retries}회 재시도 실패 ({last_error})")
