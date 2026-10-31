import pytest

from baraholka.guards import check_draft, check_offer, scan_seller_message


def rules(text, ceiling=1000, has_comparables=False):
    return {v.rule for v in check_draft(text, ceiling, has_comparables)}


def test_clean_draft_passes():
    assert rules("Bună ziua! Placa video mai este disponibilă? Funcționează fără probleme?") == set()


def test_offer_over_ceiling_raises():
    check_offer(1000, 1000)
    with pytest.raises(ValueError):
        check_offer(1001, 1000)


def test_price_in_text_over_ceiling():
    assert "ceiling" in rules("Vă pot oferi 1.200 lei pe ea.")
    assert "ceiling" not in rules("Vă pot oferi 800 lei pe ea.")


@pytest.mark.parametrize("text", [
    "Pot plăti în avans dacă vreți",
    "Trimiteți prin curier?",
    "Merge și cu Fan Courier",
    "Îmi dați un link de plată?",
    "Fac transfer bancar",
])
def test_payment_blocked(text):
    assert "payment" in rules(text)


@pytest.mark.parametrize("text", [
    "Numărul meu e 0722 123 456",
    "Sunt pe strada Mihai Eminescu",
    "Ne vedem mâine la 6?",
])
def test_personal_blocked(text):
    assert "personal" in rules(text)


def test_market_claim_needs_comparables():
    text = "Am văzut altele la 500 lei."
    assert "made_up" in rules(text, has_comparables=False)
    assert "made_up" not in rules(text, has_comparables=True)


def test_seller_scam_flags():
    flags = {v.rule for v in scan_seller_message("Trimit cu curier, plătiți aici: https://olx-plata.xyz")}
    assert flags == {"payment", "link"}
