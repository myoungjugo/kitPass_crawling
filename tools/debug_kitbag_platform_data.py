"""
__platform_data__ JSON blob 안에서 사이즈/재고 관련 필드를 찾기 위한 2차 디버그 스크립트.

이전 debug_kitbag_sizes.py로 __platform_data__ 존재를 확인했으니, 이번엔 그 blob만
따로 파싱해서 (a) 통째로 파일 저장 (b) 사이즈/재고 관련 키워드가 들어간 키의
"경로"를 재귀적으로 찾아서 출력한다. __platform_data__ 전체를 그대로 보는 대신
관련 부분만 콕 집어서 보기 위함 (사이즈 안 정보를 실제로 확인할 수 있음).

사용법:
    python -m tools.debug_kitbag_platform_data
    python -m tools.debug_kitbag_platform_data "<상세페이지 URL>"
"""
from __future__ import annotations

import json
import os
import re
import sys
from typing import Any

from services.http_client import make_session, get_with_retry
from core.diagnostics import Diagnostics

DEFAULT_URL = (
    "https://www4.kitbag.com/en/premier-league/manchester-united/"
    "manchester-united-adidas-home-shirt-2026-27/"
    "o-43753773+t-92427846+p-3540001273+z-8-1127698111"
)

OUT_DIR = os.path.join("data", "debug")
INTERESTING_KEY_HINTS = ["size", "sku", "availab", "stock", "quantity", "variant", "inventory"]


def _extract_platform_data(html: str) -> str:
    """'var __platform_data__=' 뒤에서 시작해서 중괄호 균형을 맞춰가며
    JSON 객체 리터럴 부분만 잘라낸다 (JS 변수 할당문이라 뒤에 다른 코드가
    이어질 수 있어서, 단순 정규식보다 중괄호 카운팅이 안전함)."""
    marker = "var __platform_data__="
    start = html.find(marker)
    if start == -1:
        raise ValueError("__platform_data__ 마커를 못 찾음")
    start += len(marker)
    # 앞쪽 공백 스킵, 첫 '{' 위치 찾기
    while html[start].isspace():
        start += 1
    if html[start] != "{":
        raise ValueError(f"'{{' 로 시작 안 함, 실제 시작 문자: {html[start:start+30]!r}")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(html)):
        ch = html[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return html[start:i + 1]
    raise ValueError("중괄호 균형이 안 맞음 (끝을 못 찾음)")


def _find_interesting_paths(node: Any, path: str, results: list, depth: int = 0) -> None:
    if depth > 20:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            key_lower = str(k).lower()
            new_path = f"{path}.{k}" if path else k
            if any(hint in key_lower for hint in INTERESTING_KEY_HINTS):
                preview = json.dumps(v, ensure_ascii=False)
                if len(preview) > 200:
                    preview = preview[:200] + "...(생략)"
                results.append((new_path, preview))
            _find_interesting_paths(v, new_path, results, depth + 1)
    elif isinstance(node, list):
        for i, item in enumerate(node[:5]):  # 리스트는 앞 5개만 (너무 커지는 것 방지)
            _find_interesting_paths(item, f"{path}[{i}]", results, depth + 1)


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    os.makedirs(OUT_DIR, exist_ok=True)

    session = make_session()
    diag = Diagnostics(site="kitbag-debug")
    resp = get_with_retry(session, diag, url, label="debug detail", timeout=20)
    html = resp.text

    raw = _extract_platform_data(html)
    data = json.loads(raw)

    out_path = os.path.join(OUT_DIR, "kitbag_platform_data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"__platform_data__ 저장: {out_path} ({len(raw)/1024:.1f}KB, 최상위 키: {list(data.keys())})")

    results: list = []
    _find_interesting_paths(data, "", results)

    print(f"\n=== 사이즈/재고 관련 키 경로 ({len(results)}개, 최대 40개만 출력) ===")
    for path, preview in results[:40]:
        print(f"  {path} = {preview}")

    if not results:
        print("  (관련 키를 하나도 못 찾음 — 최상위 키 목록을 보고 어느 섹션에 상품 정보가 있는지 확인 필요)")
        print(f"  최상위 키: {list(data.keys())}")


if __name__ == "__main__":
    main()