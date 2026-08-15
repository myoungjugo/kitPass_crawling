"""
Discord 웹훅 알림.

서버 설정 문서 기준: "웹사이트에 연결하지 않고, discord에 연결해 알림 받음"
환경변수 DISCORD_WEBHOOK_URL이 없으면 그냥 스킵(에러 안 냄) — 알림 off 상태와 동일하게 취급.
"""
from __future__ import annotations

import os
from typing import Iterable, List, Optional

import requests

from core.models import Product

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
_EMBEDS_PER_MESSAGE = 10  # Discord 웹훅 한 메시지당 embed 개수 제한(안전하게 10개씩)


def send_new_items(products: Iterable[Product], price_ceiling: Optional[float] = None) -> None:
    items = [p for p in products if price_ceiling is None or p.price <= price_ceiling]
    if not items:
        return

    if not DISCORD_WEBHOOK_URL:
        print(f"[notifier] DISCORD_WEBHOOK_URL 미설정 — {len(items)}건 알림 스킵")
        return

    for chunk in _chunks(items, _EMBEDS_PER_MESSAGE):
        payload = {"embeds": [_to_embed(p) for p in chunk]}
        try:
            resp = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
            resp.raise_for_status()
        except requests.RequestException as e:
            print(f"[notifier] 전송 실패: {e}")

    print(f"[notifier] {len(items)}건 Discord로 전송 완료")


def _to_embed(p: Product) -> dict:
    embed = {
        "title": p.title[:256],
        "url": p.url,
        "description": f"{p.price} {p.currency} · 재고: {', '.join(p.sizes_in_stock) or '-'} · {p.site}",
    }
    if p.image:
        embed["image"] = {"url": p.image}
    return embed


def _chunks(items: List[Product], n: int):
    for i in range(0, len(items), n):
        yield items[i:i + n]
