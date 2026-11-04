"""Рынок по нашей же базе: модель из заголовка (кодом, без LLM) и медиана цен похожих объявлений.

Ключ модели — только для однозначных лотов: одна деталь («gpu:rx 580 8gb», «cpu:i5-4570») или известный
офисный ПК («pc:optiplex 7010»). Комплекты, ПК с видеокартой, ноутбуки и битое — без ключа: их цена
испортила бы медиану деталей.
"""

import re
import sqlite3
from statistics import median

from .filters import fold, is_broken
from .models import Listing

WINDOW_DAYS = 60
MIN_SAMPLES = 3  # меньше — не статистика, а случайность

_LAPTOP = re.compile(r"\b(laptop|notebook|so-?dimm|macbook|thinkpad|latitude|elitebook|probook|ideapad|vivobook|"
                     r"zenbook|aspire|pavilion|envy|notebooka?|laptopa?)\b")
# Целый компьютер или набор деталей — у деталей внутри ключа нет
# «+» — набор, только если с пробелом или перед деталью: «Nitro+», «80+» — это названия
_SYSTEM = re.compile(r"\b(unitate|sistem|desktop|kit pc|kit complet|zestaw|komputer|computer|calculator|workstation|"
                     r"mini pc|pc complet|pc gaming|server|bundle|set)\b|^\s*pc\b|\s\+|"
                     r"\+\s*(cpu|procesor|placa|ram|cooler|ssd|hdd|gpu|mb|carcasa|sursa|memori)")

_PC = [
    (re.compile(r"\boptiplex\s?(\d{3,4})\b"), "optiplex {}"),
    (re.compile(r"\bthinkcentre\s?(m\d{2,3}[a-z]?)\b"), "thinkcentre {}"),
    (re.compile(r"\belitedesk\s?(\d{3})\s?(g\d)\b"), "elitedesk {} {}"),
    (re.compile(r"\bprodesk\s?(\d{3})\s?(g\d)\b"), "prodesk {} {}"),
]
_GPU = re.compile(r"\b(rtx|gtx|gt|rx|r9|r7)\s?(\d{3,4})\s?(ti super|ti|super|xtx|xt)?\b")
_INTEL = re.compile(r"\bi([3579])[\s-]?(\d{4,5})([a-z]{0,2})\b")
_RYZEN = re.compile(r"\bryzen\s?([3579])\s?(\d{4})(x3d|xt|x|ge|g)?\b")
_XEON = re.compile(r"\bxeon\s?(e[357])[\s-]?(\d{4})\s?(v\d)?\b")
_BOARD = re.compile(r"\b(placa de baza|motherboard|alaplap|plyta glowna)\b")
_CHIPSET = re.compile(r"\b([abhqxz]\d{2,3})m?\b")
_DDR = re.compile(r"\bddr([2345])\b")
_RAM_WORD = re.compile(r"\b(ram|memorie|memorii|dimm|ddr[2345])\b")
_KIT_GB = re.compile(r"\b(\d)\s?x\s?(\d{1,2})\s?gb\b")
_GB = re.compile(r"\b(\d{1,4})\s?gb\b")
_TB = re.compile(r"\b(\d)\s?tb\b")
_SSD = re.compile(r"\b(ssd|nvme)\b")
_SSD_SIZES = [128, 256, 512, 1000, 2000, 4000]  # 120/240/480 продают как те же классы
# Диск или память в заголовке устройства («Mac mini … 2 TB SSD», «Acer Spin / SSD 256GB») — это не цена диска
_DEVICE = re.compile(r"\b(mac|imac|windows|win\s?1[01]|touch|fhd|inch|monitor|tablet|telefon|phone|ps[45]|xbox|"
                     r"console|consola|steam deck|hdd|hdd-?uri|ssd-?uri|procesor|intel|lot)\b|\d\s?(\"|''|inch)")


def _cpu(t: str) -> str | None:
    if m := _INTEL.search(t):
        return f"cpu:i{m[1]}-{m[2]}{m[3]}"
    if m := _RYZEN.search(t):
        return f"cpu:ryzen {m[1]} {m[2]}{m[3] or ''}"
    if m := _XEON.search(t):
        return f"cpu:xeon {m[1]}-{m[2]}" + (f" {m[3]}" if m[3] else "")
    return None


def _gpu(t: str) -> str | None:
    if not (m := _GPU.search(t)):
        return None
    key = f"gpu:{m[1]} {m[2]}" + (f" {m[3]}" if m[3] else "")
    vram = _GB.search(t[m.end():]) or _GB.search(t)  # память карты обычно сразу после модели
    return key + (f" {vram[1]}gb" if vram and int(vram[1]) <= 24 else "")


def _ram(t: str) -> str | None:
    if not ((ddr := _DDR.search(t)) and _RAM_WORD.search(t)):
        return None
    if kit := _KIT_GB.search(t):
        gb = int(kit[1]) * int(kit[2])
    elif m := _GB.search(t):
        gb = int(m[1])
    else:
        return None
    return f"ram:ddr{ddr[1]} {gb}gb" if gb <= 256 else None


def _ssd(t: str) -> str | None:
    if not _SSD.search(t):
        return None
    if m := _TB.search(t):
        gb = int(m[1]) * 1000
    elif m := _GB.search(t):
        gb = int(m[1])
    else:
        return None
    size = min(_SSD_SIZES, key=lambda s: abs(s - gb))
    if abs(size - gb) > size * 0.15:
        return None
    kind = "nvme" if re.search(r"\b(nvme|m\.?2)\b", t) and "sata" not in t else "sata"
    return f"ssd:{size}gb {kind}"


def _board(t: str) -> str | None:
    if _BOARD.search(t) and (m := _CHIPSET.search(t)):
        return f"mb:{m[1]}"
    return None


def model_key(title: str) -> str | None:
    """Ключ модели для сравнения цен или None, если лот не однозначный."""
    lot = Listing("", "", title, "", None)
    t = fold(title)
    if is_broken(lot) or _LAPTOP.search(t):
        return None
    for pattern, fmt in _PC:
        if m := pattern.search(t):
            return "pc:" + fmt.format(*m.groups())
    if _SYSTEM.search(t):
        return None
    # Деталь — только если она одна: «материнка + проц» или «проц и видеокарта» — это набор
    found = [k for k in (_gpu(t), _cpu(t), _board(t)) if k]
    if len(found) > 1:
        return None
    if found:
        return found[0]
    if _DEVICE.search(t):
        return None
    ram, ssd = _ram(t), _ssd(t)
    return None if ram and ssd else ram or ssd


def _summary(prices: list[float]) -> dict | None:
    if len(prices) < MIN_SAMPLES:
        return None
    return {"median": round(median(prices)), "min": round(min(prices)), "max": round(max(prices)), "n": len(prices)}


def _samples(conn: sqlite3.Connection, keys) -> dict[str, list[tuple[int, float, str]]]:
    """{ключ: [(id, цена, заголовок)]} за окно; перевыложенное (тот же заголовок и цена) — один раз."""
    keys = sorted({k for k in keys if k})
    if not keys:
        return {}
    marks = ",".join("?" * len(keys))
    out: dict[str, list] = {}
    seen = set()
    for key, lid, price, title in conn.execute(
        f"""SELECT model, id, price_ron, title FROM listings
            WHERE model IN ({marks}) AND is_free = 0 AND price_ron BETWEEN 10 AND 100000
              AND seen_at >= datetime('now', ?) ORDER BY id""",
        [*keys, f"-{WINDOW_DAYS} days"],
    ):
        dup = (key, fold(title).strip(), price)
        if dup not in seen:
            seen.add(dup)
            out.setdefault(key, []).append((lid, price, dup))
    return out


def stats(conn: sqlite3.Connection, keys, exclude_id: int | None = None) -> dict[str, dict]:
    """{ключ: {"median", "min", "max", "n"}} по ценам за последние WINDOW_DAYS; ключи с n < MIN_SAMPLES пропускаем."""
    out = {}
    for key, rows in _samples(conn, keys).items():
        if s := _summary([p for lid, p, _ in rows if lid != exclude_id]):
            out[key] = s
    return out


def for_listings(conn: sqlite3.Connection, rows) -> dict[int, dict]:
    """{id лота: статистика его модели без него самого и без его перевыложенных копий} — для списков и пачек."""
    rows = [r for r in rows if r["model"]]
    samples = _samples(conn, (r["model"] for r in rows))
    out = {}
    for r in rows:
        me = (r["model"], fold(r["title"]).strip(), r["price_ron"])
        if s := _summary([p for lid, p, dup in samples.get(r["model"], []) if lid != r["id"] and dup != me]):
            out[r["id"]] = s
    return out


def vs_median(price: float | None, s: dict | None) -> int | None:
    """На сколько процентов цена лота ниже (−) или выше (+) медианы."""
    if price is None or not s or not s["median"]:
        return None
    return round((price - s["median"]) / s["median"] * 100)
