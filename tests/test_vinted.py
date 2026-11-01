from pathlib import Path

from baraholka.sources import vinted

HTML = (Path(__file__).parent / "fixtures/vinted_catalog.html").read_text()


def test_parse():
    laptop, gpu = vinted.parse(HTML)
    assert laptop.url == "https://www.vinted.ro/items/10617452362-laptop-alienware-m18x"
    assert laptop.price_ron == 763 and laptop.source == "vinted"
    assert "Alienware" in laptop.description and "комиссией" in laptop.description
    assert laptop.photos[0].startswith("https://images1.vinted.net/")
    assert laptop.posted_at is not None  # даты нет — ставим момент первой встречи
    assert "RX 480" in gpu.title.upper()
