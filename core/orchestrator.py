"""
사이트별 fetcher를 병렬로 실행하면서, 배치가 들어오는 대로 ResultsStore에 반영한다.

이전에는 사이트를 순차로 돌렸기 때문에(Cult Kits 다 끝나야 CFS 시작...) 총 대기시간이
"각 사이트 시간의 합"이었다. 여기서는 사이트가 서로 독립적인 I/O 작업이라는 점을 이용해
동시에 돌려서, 총 대기시간을 "가장 느린 사이트 하나"로 줄인다.

사이트 하나가 실패해도 다른 사이트는 계속 진행된다 (여기서 예외를 잡아서 로그만 남김).
"""
from __future__ import annotations

import concurrent.futures as cf
from typing import List

from core.results_store import ResultsStore
from fetchers.base import BaseFetcher


def run_all(fetchers: List[BaseFetcher]) -> ResultsStore:
    store = ResultsStore(sites=[f.site_name for f in fetchers])

    def _run_one(fetcher: BaseFetcher) -> None:
        site = fetcher.site_name
        try:
            fetcher.fetch(on_batch=lambda batch: store.add_batch(site, batch))
        except Exception as e:
            print(f"[orchestrator] {site} 실패: {type(e).__name__}: {e}")
        finally:
            store.mark_site_done(site)

    with cf.ThreadPoolExecutor(max_workers=max(len(fetchers), 1)) as executor:
        futures = [executor.submit(_run_one, f) for f in fetchers]
        for future in cf.as_completed(futures):
            future.result()  # _run_one 내부에서 이미 예외를 삼켰으므로 여기선 대기 용도

    return store
