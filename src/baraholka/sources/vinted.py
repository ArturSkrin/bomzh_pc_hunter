"""Vinted.ro: старого /api/v2/catalog/items больше нет (404), товары сервер кладёт в HTML страницы каталога
как поток Next.js RSC (self.__next_f.push). Голый httpx пускает.

Разведка 2026-11-01: в каталоге есть название, цена, бренд, состояние, фото — нет города, даты и описания.
Покупка на Vinted идёт через их оплату и доставку курьером — с нашими правилами это решает человек, не бот.
"""

import json
import re
from datetime import datetime, timezone

import httpx

from ..models import Listing

BASE = "https://www.vinted.ro"
NEWEST = 24  # даты нет — «свежесть» = первая встреча, поэтому берём только верх выдачи по новизне

_http = httpx.Client(timeout=30, follow_redirects=True, headers={
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "ro-RO,ro;q=0.9",
})


def fetch(client: httpx.Client, query: str, max_price: float | None = None, cheapest: bool = False) -> list[Listing]:
    params = {"search_text": query, "order": "price_low_to_high" if cheapest else "newest_first"}
    if max_price is not None:
        params["price_to"] = int(max_price)
    r = _http.get(f"{BASE}/catalog", params=params)
    r.raise_for_status()
    lots = parse(r.text)[:NEWEST]
    # price_to на их стороне не проверен до конца — дублируем фильтр сами
    return [l for l in lots if max_price is None or (l.price_ron is not None and l.price_ron <= max_price)]


def parse(html: str) -> list[Listing]:
    flow = "".join(json.loads(f'"{s}"') for s in re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html))
    i = flow.find('"items":{"items":[')
    if i < 0:
        raise RuntimeError("vinted: нет items в странице каталога — сменили вёрстку")
    items, _ = json.JSONDecoder().raw_decode(flow, i + len('"items":{"items":'))
    seen_now = datetime.now(timezone.utc)
    lots = []
    for it in items:
        p = it["productItem"]
        price = float(p["price"]["amount"]) if p["price"]["currencyCode"] == "RON" else None
        box = p.get("itemBox") or {}
        total = (p.get("totalItemPrice") or {}).get("amount")
        lots.append(Listing(
            source="vinted",
            external_id=str(p["id"]),
            title=p["title"],
            url=BASE + p["url"],
            price_ron=price,
            is_free=price == 0,
            description=f"{box.get('firstLine') or ''}, stare: {box.get('secondLine') or '?'}"
                        + (f". С комиссией Vinted: {total} lei" if total else ""),
            photos=[ph["url"] for ph in p.get("photos", [])[:1]],
            posted_at=seen_now,  # shortcut: даты в каталоге нет, считаем по первой встрече
        ))
    return lots
