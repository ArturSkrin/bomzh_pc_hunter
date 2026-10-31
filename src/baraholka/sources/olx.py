"""OLX.ro через внутренний JSON API (тот же, что дёргает их фронт).

Разведка 2026-10-31: GET /api/v1/offers/ — query, category_id (2100 = вся «Componente laptop-PC»
с подкатегориями, 2130 видеокарты, 1886 ноутбуки), sort_by=created_at:desc, limit до 40,
filter_float_price:to=0 — только «Gratuit». Первыми идут платные топ-объявления, их отсекает дедуп.

Голый httpx CloudFront режет 403, поэтому запросы идут из настоящего Chromium (Playwright)
со страницы olx.ro — как у их фронта. Без логина, без маскировки: при блоке/капче просто падаем.
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from urllib.parse import urlencode

import httpx
from playwright.sync_api import sync_playwright

from ..models import Listing

API = "https://www.olx.ro/api/v1/offers/"

_page = None
# Playwright sync привязан к потоку, а зовут нас и сборщик, и веб — весь браузер живёт в одном потоке
_browser_thread = ThreadPoolExecutor(max_workers=1)


def _browser_page():
    global _page
    if _page is None:
        browser = sync_playwright().start().chromium.launch()
        _page = browser.new_page(locale="ro-RO")
        _page.goto("https://www.olx.ro/", wait_until="domcontentloaded")
    return _page


def fetch(client: httpx.Client, query: str, max_price: float | None = None) -> list[Listing]:
    return _browser_thread.submit(_fetch, query, max_price).result(timeout=120)


def _fetch(query: str, max_price: float | None) -> list[Listing]:
    # shortcut: только первая страница (40 свежих), хватает при опросе раз в 10–15 мин
    params = {"query": query, "sort_by": "created_at:desc", "limit": 40}
    if max_price is not None:
        params["filter_float_price:to"] = int(max_price)
    url = API + "?" + urlencode(params)
    res = _browser_page().evaluate(
        "async (url) => { const r = await fetch(url, {headers: {Accept: 'application/json'}});"
        " return {status: r.status, body: await r.text()}; }",
        url,
    )
    if res["status"] != 200:
        raise RuntimeError(f"OLX ответил {res['status']} — блок или капча, не пробиваемся: {res['body'][:200]}")
    return parse(json.loads(res["body"]))


def parse(payload: dict) -> list[Listing]:
    lots = []
    for o in payload["data"]:
        price = next((p["value"] for p in o["params"] if p["key"] == "price"), None)
        is_free = bool(price) and price["type"] == "free"
        lots.append(Listing(
            source="olx",
            external_id=str(o["id"]),
            title=o["title"],
            url=o["url"],
            # «exchange» и объявления без цены — None
            price_ron=price["value"] if price and price["type"] in ("price", "arranged", "free") else None,
            is_free=is_free,
            description=re.sub(r"<[^>]+>", "", o["description"]),
            city=o["location"]["city"]["name"],
            photos=[ph["link"].replace("{width}x{height}", "800x600") for ph in o["photos"]],
            posted_at=datetime.fromisoformat(o["created_time"]),
        ))
    return lots
