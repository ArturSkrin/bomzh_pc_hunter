"""Lajumate.ro: Next.js, данные лежат в <script id="__NEXT_DATA__"> на странице категории.

Разведка 2026-11-01: текстовый поиск у них сломан (склеивает слова: «placa video» -> «placavideo», даже
«monitor» даёт 0), поэтому берём первую страницу (28 свежих) компьютерных категорий и фильтруем по словам
запроса сами. Объём маленький: ~240 объявлений во всей «Componente laptop-PC». Голый httpx пускает.
"""

import json
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from ..filters import fold as _fold
from ..models import Listing

BASE = "https://lajumate.ro"
IMG = "https://api-preprod.lajumate.ro/opt-image/"  # так фото грузит их же сайт
CATEGORIES = [
    "electronice-si-electrocasnice/componente-laptop-pc",
    "electronice-si-electrocasnice/laptop-calculator-gaming",
    "electronice-si-electrocasnice/periferice-accesorii-laptop-pc-gaming",
    "electronice-si-electrocasnice/retelistica-servere",
]
CACHE_SEC = 600  # сборщик зовёт fetch на каждый запрос — категории качаем раз за проход

_http = httpx.Client(timeout=20, follow_redirects=True, headers={
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
})
_cache: tuple[float, list[Listing]] = (0.0, [])


def fetch(client: httpx.Client, query: str, max_price: float | None = None) -> list[Listing]:
    words = _fold(query).split()
    return [
        lot for lot in _latest()
        if all(re.search(rf"\b{re.escape(w)}", _fold(f"{lot.title} {lot.description}")) for w in words)
        and (max_price is None or lot.is_free or (lot.price_ron is not None and lot.price_ron <= max_price))
    ]


def _latest() -> list[Listing]:
    global _cache
    if time.monotonic() - _cache[0] > CACHE_SEC:
        lots = []
        for i, cat in enumerate(CATEGORIES):
            if i:
                time.sleep(2)
            r = _http.get(f"{BASE}/anunturi/{cat}")
            r.raise_for_status()
            lots += parse(page_props(r.text))
        _cache = (time.monotonic(), lots)
    return _cache[1]


def page_props(html: str) -> dict:
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m:
        raise RuntimeError("lajumate: нет __NEXT_DATA__ — сменили вёрстку")
    return json.loads(m.group(1))["props"]["pageProps"]


def parse(props: dict) -> list[Listing]:
    lots = []
    for a in props["adsServer"]:
        price = float(a["price"]) if a.get("price") and a.get("currency") == "lei" else None
        image = a.get("mainImage") or (a.get("images") or [None])[0]
        lots.append(Listing(
            source="lajumate",
            external_id=str(a["id"]),
            title=a["title"],
            url=f"{BASE}/ad/{a['slug']}-{a['id']}",
            price_ron=price,
            is_free=price == 0,
            description=re.sub(r"<[^>]+>", " ", a.get("description") or ""),
            city=(a.get("city") or {}).get("name", ""),
            photos=[IMG + image["path"]] if image else [],
            posted_at=_dt(a["listed_at"]),
        ))
    return lots


def _dt(s: str) -> datetime:
    # Бывает и «2026-10-25 12:12:31» без зоны (местное), и «…Z»
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=ZoneInfo("Europe/Bucharest"))
