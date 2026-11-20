import json

from baraholka.sources.olx import parse


def test_parse_public_search():
    ad = dict(id=123, title="Procesor", url="https://www.olx.ro/d/oferta/cpu",
              description="CPU<br/>", location={"cityName": "Suceava"},
              photos=["photo"], createdTime="2026-11-20T10:00:00+02:00")
    prices = [{"regularPrice": {"value": 150, "currencyCode": "RON"}}, {"free": True}, {"exchange": True}]
    state = {"listing": {"listing": {"ads": [dict(ad, price=p) for p in prices]}}}
    html = "<script>window.__PRERENDERED_STATE__= " + json.dumps(json.dumps(state)) + ";</script>"
    paid, free, exchange = parse(html)
    assert paid.price_ron == 150 and paid.external_id == "123"
    assert paid.city == "Suceava" and paid.description == "CPU" and paid.photos == ["photo"]
    assert free.is_free and free.price_ron == 0
    assert exchange.price_ron is None
