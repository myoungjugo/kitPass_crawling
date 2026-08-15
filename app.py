"""
사용자가 보는 대시보드.

수집기(main.py)가 쓰는 data/results/latest.json을 읽기만 한다 — 이 앱은
크롤링을 하지 않는다. 수집(main.py, cron 등으로 주기 실행)과 화면(app.py)을
분리해뒀기 때문에, 대시보드를 아무리 새로고침해도 쇼핑몰에 추가 부하가 안 간다.

실행:
    python app.py
    http://127.0.0.1:8000  (개인용, 외부 노출 없음 — 서버_설정 문서 기준)

라우트:
    GET  /                    대시보드 화면
    GET  /api/results         현재 결과(JSON) — 프론트에서 폴링용
    GET  /api/notify-settings 알림 설정 조회
    POST /api/notify-settings 알림 on/off, 가격 상한 저장
    GET  /download?format=md|json   현재 필터 적용된 리스트 다운로드
"""
from __future__ import annotations

import json
import os
from typing import List

from flask import Flask, jsonify, render_template, request, Response

import main as collector  # main.py의 collect_once()를 재사용 (수집 로직 한 곳에만 존재)
from core.results_store import LATEST_PATH
from services.collection_runner import get_status, start_collection_if_idle
from services.notify_settings import NotifySettings, load_settings, save_settings

app = Flask(__name__)


def _read_snapshot() -> dict:
    if not os.path.exists(LATEST_PATH):
        return {"generated_at": None, "started_at": None, "sites_done": {}, "count": 0, "items": []}
    with open(LATEST_PATH, encoding="utf-8") as f:
        return json.load(f)


def _apply_filters(items: List[dict]) -> List[dict]:
    q = (request.args.get("q") or "").strip().lower()
    site = request.args.get("site") or ""
    max_price_raw = request.args.get("max_price")

    result = items
    if q:
        result = [i for i in result if q in i["title"].lower()]
    if site:
        result = [i for i in result if i["site"] == site]
    if max_price_raw:
        try:
            max_price = float(max_price_raw)
            result = [i for i in result if i["price"] <= max_price]
        except ValueError:
            pass
    return result


@app.route("/")
def dashboard():
    snapshot = _read_snapshot()
    notify = load_settings()
    sites = sorted(snapshot.get("sites_done", {}).keys())
    return render_template(
        "dashboard.html",
        items=snapshot.get("items", []),
        count=snapshot.get("count", 0),
        sites_done=snapshot.get("sites_done", {}),
        generated_at=snapshot.get("generated_at"),
        notify=notify,
        sites=sites,
        collection_status=get_status(),
    )


@app.route("/api/results")
def api_results():
    snapshot = _read_snapshot()
    snapshot["items"] = _apply_filters(snapshot["items"])
    snapshot["count"] = len(snapshot["items"])
    snapshot["collection_status"] = get_status()
    return jsonify(snapshot)


@app.route("/api/collect", methods=["POST"])
def api_collect():
    started = start_collection_if_idle(collector.collect_once)
    return jsonify({"started": started, "status": get_status()})


@app.route("/api/collect/status")
def api_collect_status():
    return jsonify(get_status())


@app.route("/api/notify-settings", methods=["GET", "POST"])
def api_notify_settings():
    if request.method == "GET":
        return jsonify(load_settings().__dict__)

    data = request.get_json(force=True, silent=True) or {}
    ceiling_raw = data.get("price_ceiling")
    try:
        ceiling = float(ceiling_raw) if ceiling_raw not in (None, "") else None
    except (TypeError, ValueError):
        ceiling = None

    settings = NotifySettings(enabled=bool(data.get("enabled")), price_ceiling=ceiling)
    save_settings(settings)
    return jsonify(settings.__dict__)


@app.route("/download")
def download():
    fmt = request.args.get("format", "md")
    snapshot = _read_snapshot()
    items = _apply_filters(snapshot["items"])

    if fmt == "json":
        body = json.dumps(items, ensure_ascii=False, indent=2)
        return Response(
            body, mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=uniforms.json"},
        )

    body = _to_markdown(items)
    return Response(
        body, mimetype="text/markdown",
        headers={"Content-Disposition": "attachment; filename=uniforms.md"},
    )


def _to_markdown(items: List[dict]) -> str:
    lines = ["| 사이트 | 상품명 | 가격 | 사이즈 | 링크 |", "|---|---|---|---|---|"]
    for it in items:
        title = it["title"].replace("|", "\\|")
        sizes = ", ".join(it.get("sizes_in_stock", []))
        lines.append(f"| {it['site']} | {title} | {it['price']} {it['currency']} | {sizes} | {it['url']} |")
    return "\n".join(lines)


if __name__ == "__main__":
    # threaded=True: '지금 수집하기' 버튼이 시작한 백그라운드 스레드가 도는 동안에도
    # 다른 요청(폴링 등)을 계속 받을 수 있어야 하므로.
    app.run(host="127.0.0.1", port=8000, debug=False, threaded=True)