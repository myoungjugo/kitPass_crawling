"""
사용자가 보는 대시보드 (kitPass).

수집기(main.py)가 쓰는 data/results/latest.json을 읽기만 한다 — 이 앱은
크롤링을 하지 않는다. 수집(main.py, cron 등으로 주기 실행)과 화면(app.py)을
분리해뒀기 때문에, 대시보드를 아무리 새로고침해도 쇼핑몰에 추가 부하가 안 간다.

실행:
    python app.py
    http://127.0.0.1:8000

인증:
    ADMIN_PASSWORD 환경변수가 설정되어 있으면 모든 페이지 접근 전에 비밀번호를
    요구한다 (services/ 쪽이 아니라 여기서 before_request로 막음 — 간단한
    개인용 게이트이지 정식 사용자 관리는 아님). 설정 안 하면 인증 없이 열림
    (로컬 개발 편의용 — 배포 전에 반드시 .env에 ADMIN_PASSWORD를 넣을 것).

라우트:
    GET  /                    대시보드 화면
    GET  /login, POST /login  비밀번호 로그인
    GET  /logout              로그아웃
    GET  /api/results         현재 결과(JSON, 원화 환산 포함) — 프론트 폴링용
    GET  /api/collect/status  수집 진행 상태
    POST /api/collect         '지금 수집하기' 트리거 (백그라운드 스레드)
    GET  /api/notify-settings, POST /api/notify-settings  알림 on/off, 가격상한
    GET  /download?format=md|json   현재 필터 적용된 리스트 다운로드
    GET  /calculator           유니폼 원가 계산기 페이지
    GET  /api/fx-rates         계산기가 쓰는 환율 테이블 (services/fx.py 캐시 그대로 반환)
"""
from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()  # ADMIN_PASSWORD 등 .env 값을 실제로 로드 (2026-08-17 발견: 지금까지
                # 어디서도 호출 안 해서 .env가 통째로 무시되고 있었음 — 인증 안 먹던 원인)

import json
import os
from typing import List, Optional

from flask import Flask, jsonify, redirect, render_template, request, session, url_for, Response

import main as collector # main.py의 collect_once()를 재사용 (수집 로직 한 곳에만 존재)
from core.results_store import LATEST_PATH
from services.collection_runner import get_status, start_collection_if_idle
from services.fx import get_rates_table, to_krw
from services.notify_settings import NotifySettings, load_settings, save_settings

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-only-insecure-key-change-me")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
AUTH_ENABLED = bool(ADMIN_PASSWORD)

if not AUTH_ENABLED:
    print("[app] 경고: ADMIN_PASSWORD가 설정 안 되어 있어 인증 없이 열려 있습니다. "
          "배포 전에 .env에 ADMIN_PASSWORD를 설정하세요.")


@app.before_request
def require_login():
    if not AUTH_ENABLED:
        return
    if request.endpoint in ("login", "static"):
        return
    if not session.get("authed"):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if password and password == ADMIN_PASSWORD:
            session["authed"] = True
            return redirect(url_for("dashboard"))
        error = "비밀번호가 올바르지 않습니다."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def _read_snapshot() -> dict:
    if not os.path.exists(LATEST_PATH):
        data = {"generated_at": None, "started_at": None, "sites_done": {}, "count": 0, "items": []}
    else:
        with open(LATEST_PATH, encoding="utf-8") as f:
            data = json.load(f)

    # 원화 환산은 여기서 한 번만 rates 테이블을 받아서 전체 아이템에 적용한다
    # (아이템마다 get_rates_table()을 부르면 그때마다 캐시 확인 오버헤드가 반복됨).
    rates = get_rates_table()
    for item in data.get("items", []):
        item["price_krw"] = to_krw(item["price"], item["currency"], rates=rates)
        item["gender"] = _classify_gender(item["title"])
        item["sleeve"] = _classify_sleeve(item["title"])
    return data

_WOMEN_HINTS = ("womens", "women's", "ladies", "girls", "girl's")


def _classify_gender(title: str) -> str:
    t = title.lower()
    return "women" if any(h in t for h in _WOMEN_HINTS) else "men"


def _classify_sleeve(title: str) -> str:
    return "long" if "long sleeve" in title.lower() else "short"




def _apply_filters(items: List[dict]) -> List[dict]:
    q = (request.args.get("q") or "").strip().lower()
    site = request.args.get("site") or ""
    max_price_raw = request.args.get("max_price")
    gender = request.args.get("gender") or ""   # "", "men", "women"
    sleeve = request.args.get("sleeve") or ""    # "", "short", "long"

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
    if gender:
        result = [i for i in result if i.get("gender") == gender]
    if sleeve:
        result = [i for i in result if i.get("sleeve") == sleeve]
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
        auth_enabled=AUTH_ENABLED,
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


@app.route("/calculator")
def calculator():
    """유니폼 원가 계산기 페이지. 서버는 빈 화면만 내려주고, 상품 검색/선택/계산은
    전부 프론트(static/calculator.js)가 /api/results, /api/fx-rates를 불러서 처리한다."""
    return render_template("calculator.html", auth_enabled=AUTH_ENABLED)


@app.route("/api/fx-rates")
def api_fx_rates():
    """계산기 페이지가 상품가/배송비 통화를 원화로 환산할 때 쓰는 환율 테이블.
    services/fx.py가 이미 캐싱해둔 것을 그대로 반환한다 (여기서 새로 API를 부르지 않음)."""
    rates = get_rates_table()
    return jsonify({"rates": rates or {}})


@app.route("/download")
def download():
    fmt = request.args.get("format", "md")
    snapshot = _read_snapshot()
    items = _apply_filters(snapshot["items"])

    if fmt == "json":
        body = json.dumps(items, ensure_ascii=False, indent=2)
        return Response(
            body, mimetype="application/json",
            headers={"Content-Disposition": "attachment; filename=kitpass-uniforms.json"},
        )

    body = _to_markdown(items)
    return Response(
        body, mimetype="text/markdown",
        headers={"Content-Disposition": "attachment; filename=kitpass-uniforms.md"},
    )


def _to_markdown(items: List[dict]) -> str:
    lines = ["| 사이트 | 상품명 | 가격 | 원화 환산 | 사이즈 | 링크 |", "|---|---|---|---|---|---|"]
    for it in items:
        title = it["title"].replace("|", "\\|")
        sizes = ", ".join(it.get("sizes_in_stock", []))
        krw = f"₩{it['price_krw']:,}" if it.get("price_krw") is not None else "-"
        lines.append(f"| {it['site']} | {title} | {it['price']} {it['currency']} | {krw} | {sizes} | {it['url']} |")
    return "\n".join(lines)


if __name__ == "__main__":
    # threaded=True: '지금 수집하기' 버튼이 시작한 백그라운드 스레드가 도는 동안에도
    # 다른 요청(폴링 등)을 계속 받을 수 있어야 하므로.
    app.run(host="127.0.0.1", port=8000, debug=False, threaded=True)