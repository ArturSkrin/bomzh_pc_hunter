import json
from pathlib import Path

from baraholka.sources import lajumate

PROPS = json.loads((Path(__file__).parent / "fixtures/lajumate_page.json").read_text())


def test_parse():
    pack, gtx = lajumate.parse(PROPS)
    assert pack.url == "https://lajumate.ro/ad/pachet-lan-gpu-diverse-17096644"
    assert pack.price_ron == 600 and not pack.is_free
    assert pack.city == "Alexandria"
    assert pack.photos[0].startswith("https://api-preprod.lajumate.ro/opt-image/media/")
    assert pack.posted_at.utcoffset() is not None  # без зоны в исходнике -> Europe/Bucharest
    assert gtx.is_free and "<br>" not in gtx.description


def test_fetch_filters_words_diacritics_and_price(monkeypatch):
    monkeypatch.setattr(lajumate, "_latest", lambda: lajumate.parse(PROPS))
    titles = lambda q, **kw: [l.title for l in lajumate.fetch(None, q, **kw)]
    assert titles("placă video") == [lajumate.parse(PROPS)[1].title]  # диакритика не мешает, «pachet» без слова «placa»
    assert titles("gpu") == ["Pachet LAN + GPU + Diverse"]
    assert titles("gpu", max_price=500) == []  # 600 lei дороже
    assert len(titles("video", max_price=500)) == 1  # бесплатная проходит любой потолок
