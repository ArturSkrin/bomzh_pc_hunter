from baraholka.filters import passes_rules
from baraholka.models import Listing


def lot(title, desc="", price=100.0, free=False):
    return Listing("olx", "1", title, "u", price, is_free=free, description=desc)


def test_whole_words_only():
    assert not passes_rules(lot("Lacat otel Federal", "trimitem prin program"), 1500)  # «ram» в «program»
    assert passes_rules(lot("Memorie RAM DDR4 16GB"), 1500)
    assert passes_rules(lot("Vand placa video GTX 1060"), 1500)


def test_block_price_and_free():
    assert not passes_rules(lot("Placa video defect"), 1500)
    assert not passes_rules(lot("Laptop Dell", price=2000), 1500)
    assert passes_rules(lot("Calculator vechi", price=0, free=True), 1500)
