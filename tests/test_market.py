import pytest

from baraholka import db
from baraholka.market import for_listings, model_key, stats, vs_median
from baraholka.models import Listing


@pytest.mark.parametrize("title, key", [
    ("Placa video Sapphire Nitro+ Radeon RX 580 8GB GDDR5", "gpu:rx 580 8gb"),
    ("GTX 1070 OC Edition 8GB VRAM 256-bit", "gpu:gtx 1070 8gb"),
    ("Placa video ASUS RTX 3060 Ti 8GB", "gpu:rtx 3060 ti 8gb"),
    ("Procesor Intel Coffee Lake, Core i9 9900K 3.6GHz Socket 1151", "cpu:i9-9900k"),
    ("Intel i5-4570 LGA1150", "cpu:i5-4570"),
    ("Vând procesor ryzen 9 7900x3d", "cpu:ryzen 9 7900x3d"),
    ("Procesor Xeon E3-1230 v2", "cpu:xeon e3-1230 v2"),
    ("Placa de baza MSI PRO B760M-P DDR4 LGA1700", "mb:b760"),
    ("Płyta główna gigabyte B250M-DS3H", "mb:b250"),
    ("Memorie RAM Corsair Vengeance LPX 16GB (2x8GB) DDR4 3600", "ram:ddr4 16gb"),
    ("Kit memorii 2 x 4GB DDR3 1600", "ram:ddr3 8gb"),
    ("crucial ssd 240gb", "ssd:256gb sata"),
    ("SSD Western Digital Black 1TB NVMe", "ssd:1000gb nvme"),
    ("Dell Optiplex 7010 (i5-3470, 8gb RAM DDR3, DVD-rom)", "pc:optiplex 7010"),
    ("Unitate Calculator Lenovo ThinkCentre m920", "pc:thinkcentre m920"),
    ("HP Prodesk 600 G2 SFF, i5, 8GB", "pc:prodesk 600 g2"),
])
def test_model_key(title, key):
    assert model_key(title) == key


@pytest.mark.parametrize("title", [
    "PC Gaming procesor Ryzen 5 3600, Ram 16 Gb ddr4, placa video GTX 1070 /8GB",  # ПК, а не видеокарта
    "Kit placa de baza + procesor i7 6700",
    "Placa de baza Asus H81 cu procesor i5-4570",  # две детали — набор
    "Placa video gtx 1070 defecta",
    "Laptop Dell Latitude 5511 Intel i7 10850H",
    "RAM DDR4 SO-DIMM 32 GB (2x16 GB) G.Skill",
    "Apple Mac mini M2 (2023) — 24 GB RAM / 2 TB SSD",
    "Acer Spin 3 SP314-21N 14\" FHD Touch / SSD 256GB",
    "hdd uri 500gb , 1 TB . ssd uri stare perfecta",
    "Motor Scania DC9 320CP",
])
def test_no_key_for_bundles_devices_and_junk(title):
    assert model_key(title) is None


def test_stats_median_min_n_and_window(tmp_path):
    conn = db.connect(tmp_path / "m.db")
    for i, price in enumerate([240, 300, 330, 350, 5]):  # 5 lei — заглушка, не цена
        db.insert_if_new(conn, Listing("olx", str(i), "Placa video RX 580 8GB", f"https://x/{i}", price))
    db.insert_if_new(conn, Listing("olx", "old", "RX 580 8GB", "https://x/old", 999))
    conn.execute("UPDATE listings SET seen_at = datetime('now', '-90 days') WHERE external_id = 'old'")
    db.insert_if_new(conn, Listing("olx", "few", "GTX 1080 8GB", "https://x/few", 900))
    s = stats(conn, ["gpu:rx 580 8gb", "gpu:gtx 1080 8gb"])
    assert s == {"gpu:rx 580 8gb": {"median": 315, "min": 240, "max": 350, "n": 4}}  # одна цена — не статистика
    assert vs_median(250, s["gpu:rx 580 8gb"]) == -21


def test_for_listings_excludes_self_and_reposts(tmp_path):
    conn = db.connect(tmp_path / "f.db")
    ids = [db.insert_if_new(conn, Listing("olx", str(i), t, f"https://x/{i}", p)) for i, (t, p) in enumerate([
        ("Placa de baza MSI B250M Bazooka", 150), ("Placa de baza Msi B250M BAZOOKA", 150),  # перевыложил
        ("Placa de baza Gigabyte B250", 200), ("Placa de baza Asus B250", 220), ("B250 placa de baza", 260)])]
    rows = conn.execute("SELECT * FROM listings ORDER BY id").fetchall()
    m = for_listings(conn, rows)
    assert m[ids[0]] == {"median": 220, "min": 200, "max": 260, "n": 3}  # ни себя, ни своей копии
    assert stats(conn, ["mb:b250"])["mb:b250"]["n"] == 4  # копия — один раз
