"""OLX.ro публикует данные объявлений в HTML поисковой выдачи."""

import json
import re
from datetime import datetime
from urllib.parse import quote

import httpx

from ..models import Listing


def fetch(client: httpx.Client, query: str, max_price: float | None = None, cheapest: bool = False) -> list[Listing]:
    slug = quote("-".join(query.split()), safe="")
    params = {"search[order]": "filter_float_price:asc" if cheapest else "created_at:desc"}
    if max_price is not None:
        params["search[filter_float_price:to]"] = str(max_price)
    response = client.get(f"https://www.olx.ro/oferte/q-{slug}/", params=params, follow_redirects=True)
    response.raise_for_status()
    lots = parse(response.text)
    return [lot for lot in lots if max_price is None or (lot.price_ron is not None and lot.price_ron <= max_price)]


def parse(html: str) -> list[Listing]:
    marker = re.search(r"window\.__PRERENDERED_STATE__\s*=\s*", html)
    if not marker:
        raise ValueError("OLX: в HTML нет состояния каталога")
    encoded, _ = json.JSONDecoder().raw_decode(html, marker.end())
    ads = json.loads(encoded)["listing"]["listing"]["ads"]
    lots = []
    for ad in ads:
        price = ad.get("price") or {}
        regular = price.get("regularPrice") or {}
        free = bool(price.get("free"))
        amount = regular.get("value") if regular.get("currencyCode") == "RON" else None
        lots.append(Listing(
            source="olx", external_id=str(ad["id"]), title=ad["title"], url=ad["url"],
            price_ron=0 if free else amount, is_free=free,
            description=re.sub(r"<[^>]+>", "", ad.get("description") or ""),
            city=(ad.get("location") or {}).get("cityName") or "",
            photos=ad.get("photos") or [], posted_at=datetime.fromisoformat(ad["createdTime"]),
        ))
    return lots
