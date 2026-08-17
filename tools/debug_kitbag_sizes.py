"""
kitbag 상세페이지 하나를 받아서, 사이즈/재고 정보가 실제로 어디에 어떤 형태로
들어있는지 찾기 위한 1회성 디버그 스크립트.

사용법:
    python -m tools.debug_kitbag_sizes "https://www4.kitbag.com/en/premier-league/manchester-united/manchester-united-adidas-home-shirt-2026-27/o-43753773+t-92427846+p-3540001273+z-8-1127698111"

(인자 없이 실행하면 맨체스터 유나이티드 홈 셔츠로 기본 테스트)

출력:
    1. 원본 HTML을 data/debug/kitbag_detail_raw.html 로 통째로 저장 (전체 구조를
       직접 열어보고 싶을 때 대비)
    2. <script> 태그들의 type/변수명만 요약 나열 (어떤 상태 임베드 패턴을 쓰는지 파악용)
    3. "size"/"stock"/"availab"/"sku"/"swatch" 키워드가 나오는 위치 앞뒤 300자씩 스니펫 출력
       (이게 핵심 — 이 스니펫들만 보면 실제 JSON 구조/키 이름을 알 수 있음)
"""
from __future__ import annotations

import os
import re
import sys

from services.http_client import make_session, get_with_retry
from core.diagnostics import Diagnostics

DEFAULT_URL = (
    "https://www4.kitbag.com/en/premier-league/manchester-united/"
    "manchester-united-adidas-home-shirt-2026-27/"
    "o-43753773+t-92427846+p-3540001273+z-8-1127698111"
)

OUT_DIR = os.path.join("data", "debug")
KEYWORDS = ["size", "stock", "availab", "\"sku\"", "swatch", "variant"]


def _summarize_scripts(html: str) -> None:
    print("\n=== <script> 태그 요약 ===")
    for m in re.finditer(r'<script([^>]*)>', html, re.IGNORECASE):
        attrs = m.group(1)
        type_match = re.search(r'type=["\']([^"\']+)["\']', attrs)
        id_match = re.search(r'id=["\']([^"\']+)["\']', attrs)
        script_type = type_match.group(1) if type_match else "(no type = 기본 JS)"
        script_id = id_match.group(1) if id_match else None
        # 이 script 태그 시작 직후 최대 120자만 미리보기로 (내용이 JSON 변수 할당인지 확인용)
        start = m.end()
        preview = html[start:start + 120].strip().replace("\n", " ")
        line = f"  type={script_type}"
        if script_id:
            line += f" id={script_id}"
        line += f" | 시작부분: {preview!r}"
        print(line)


def _print_keyword_snippets(html: str) -> None:
    print("\n=== 키워드 스니펫 (앞뒤 300자) ===")
    lower = html.lower()
    for kw in KEYWORDS:
        idx = 0
        found_count = 0
        while found_count < 3:  # 키워드당 최대 3곳까지만
            pos = lower.find(kw.lower(), idx)
            if pos == -1:
                break
            start = max(0, pos - 150)
            end = min(len(html), pos + 150)
            snippet = html[start:end].replace("\n", " ")
            print(f"\n--- '{kw}' 매치 #{found_count + 1} (문자 위치 {pos}) ---")
            print(snippet)
            idx = pos + len(kw)
            found_count += 1
        if found_count == 0:
            print(f"\n--- '{kw}': 매치 없음 ---")


def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    os.makedirs(OUT_DIR, exist_ok=True)

    session = make_session()
    diag = Diagnostics(site="kitbag-debug")
    resp = get_with_retry(session, diag, url, label="debug detail", timeout=20)
    html = resp.text

    raw_path = os.path.join(OUT_DIR, "kitbag_detail_raw.html")
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"원본 HTML 저장: {raw_path} ({len(html)/1024:.1f}KB)")

    _summarize_scripts(html)
    _print_keyword_snippets(html)


if __name__ == "__main__":
    main()