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


def test_pawnshops_laptop_parts_and_desc_only_blocked():
    for title in ("NDP Amanet NON-STOP Bld.Iuliu Maniu 69 Unitate PC i5 (41593)",
                  "Ecran/ diplay Touch laptop HP Envy 13 ba0505sa", "Tastatura laptop Lenovo", "Baterie laptop Dell",
                  "Carcasa completa Laptop Acer Aspire V3-772G", "Placa de baza laptop Asus"):
        assert not passes_rules(lot(title), 1500), title
    assert not passes_rules(lot("Stabilizator Insta360 Flow", "merge cu telefon, laptop sau pc"), 1500)
    # те же слова у ПК — норм
    for title in ("Carcasa Lian Li O11 MINI / Kit ITX", "Kit complet intel + placa video + baterie bios", "Placa de baza AM4"):
        assert passes_rules(lot(title), 1500), title


def test_plain_hdd_blocked_but_systems_with_hdd_pass():
    for title in ("Hard disk Seagate Barracuda 500 GB SATA", "WD HDD 500gb", "Vând Hard extern Seagate 2TB",
                  "HDD 2.5\" HGST 500GB 7200 rpm SATA"):
        assert not passes_rules(lot(title), 1500), title
    for title in ("PC Desktop i5-750, 16 gb RAM, HDD 1tb", "SSD Kingston A400 480GB", "Unitate PC i5 hard disk 500"):
        assert passes_rules(lot(title), 1500), title
