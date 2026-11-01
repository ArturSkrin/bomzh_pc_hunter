from baraholka.filters import passes_rules
from baraholka.models import Listing


def lot(title, desc="", price=100.0, free=False):
    return Listing("olx", "1", title, "u", price, is_free=free, description=desc)


def test_whole_words_only():
    assert not passes_rules(lot("Lacat otel Federal", "trimitem prin program"), 1500)  # «ram» в «program»
    assert passes_rules(lot("Memorie RAM DDR4 16GB"), 1500)
    assert passes_rules(lot("Vand placă video GTX 1060"), 1500)  # диакритика


def test_junk_from_screenshot_blocked():
    for title in ("Samsonite laptop táska Iron blue", "Poslovni ruksak za 13\" laptop",
                  "Fisher Price laptop interaktywny", "Calculator stiintific Casio fx-991",
                  "Geanta laptop 15.6", "Incarcator laptop Dell 65W", "Cumpar placa video"):
        assert not passes_rules(lot(title), 1500), title


def test_description_words_dont_block_working_items():
    assert passes_rules(lot("Placa video RX 580 8GB", "Functioneaza perfect, fara niciun defect. Nu fac schimb."), 1500)
    assert passes_rules(lot("Laptop Lenovo i5", "Ofer si geanta cadou"), 1500)
    assert not passes_rules(lot("Placa video RX 580", "nu da imagine"), 1500)


def test_block_price_and_free():
    assert not passes_rules(lot("Placa video defecta"), 1500)
    assert not passes_rules(lot("Laptop Dell", price=2000), 1500)
    assert passes_rules(lot("Unitate PC i5", price=0, free=True), 1500)
