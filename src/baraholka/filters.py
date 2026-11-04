"""Дешёвый фильтр по правилам — до LLM, чтобы не жечь токены на мусор."""

import re
import unicodedata

from .models import Listing

# Что перекупу интересно: готовые ПК, десктопные комплектующие, ноутбуки
WANT_KEYWORDS = [
    "placa video", "gpu", "rtx", "gtx", "radeon", "geforce", "quadro", "rx",
    "procesor", "cpu", "ryzen", "intel core", "xeon", "i3", "i5", "i7", "i9",
    "placa de baza", "motherboard", "kit placa de baza",
    "ram", "ddr3", "ddr4", "ddr5", "memorie", "memorii",
    "ssd", "nvme", "m.2",
    "sursa", "carcasa", "atx", "itx", "unitate pc", "unitate", "sistem pc", "desktop", "mini pc", "workstation", "server",
    "optiplex", "thinkcentre", "elitedesk", "prodesk", "pc", "calculator", "laptop", "notebook",
    # линейки ноутбуков — в заголовке часто нет слова «laptop»
    "macbook", "thinkpad", "latitude", "elitebook", "probook", "ideapad", "vivobook", "zenbook", "aspire", "pavilion",
]
# Только в заголовке: в описании рабочих лотов пишут «nu fac schimb», «fara niciun defect», «geanta cadou»
# Битое — не берём нигде, в том числе в поиске из чата
BROKEN = ["defect", "defecta", "defecte", "defecti", "pentru piese", "nefunctional", "nefunctionala", "nu porneste",
          "nu merge", "netestat", "netestata", "netestate"]
TITLE_BLOCK = BROKEN + [
    # не продажа
    "cumpar", "caut", "schimb",
    # сумки, чехлы, аксессуары
    "geanta", "genti", "rucsac", "husa", "huse", "sleeve", "taska", "ruksak", "torba", "backpack", "bag",
    "incarcator", "alimentator", "cablu", "adaptor", "mouse", "mousepad", "casti", "suport", "stand", "cooler laptop",
    # калькуляторы и игрушки
    "stiintific", "de buzunar", "casio", "citizen", "jucarie", "jucarii", "copii", "fisher", "vtech", "interactiv",
    "interaktywny", "lego",
    # ломбарды: продают по рынку, в заголовке — адрес и номер квитанции
    "amanet", "amanetul",
    # экраны, клавиатуры, веб-камеры, колонки — не наш профиль ни для ноута, ни для ПК
    "ecran", "display", "diplay", "lcd", "tastatura", "webcam", "difuzor", "difuzoare",
]
# Запчасти ноутбуков: «carcasa», «baterie» сами по себе бывают и у ПК (корпус, батарейка BIOS),
# поэтому режем, только если в заголовке ещё и ноутбук
LAPTOP_PART = [
    "carcasa", "baterie", "acumulator", "balamale", "bezel", "palmrest", "touchpad", "cooler", "ventilator",
    "placa de baza", "motherboard", "chiller", "radiator", "piese",
]
LAPTOP = [
    "laptop", "laptopuri", "notebook", "macbook", "thinkpad", "latitude", "elitebook", "probook", "ideapad",
    "vivobook", "zenbook", "aspire", "pavilion", "envy",
]
# Обычные HDD не берём — но «PC i5, HDD 500GB» это системник, его не трогаем
HDD = ["hdd", "hard disk", "harddisk", "hard extern", "hard intern", "disc dur", "hard"]
SYSTEM = ["unitate", "pc", "calculator", "sistem", "desktop", "server", "workstation", "optiplex", "thinkcentre",
          "elitedesk", "prodesk", "kit", "nas"] + LAPTOP

# В описании — только то, что однозначно значит «не работает»
TEXT_BLOCK = ["nu porneste", "nu functioneaza", "nu da imagine", "pentru piese", "nefunctional"]


def fold(s: str) -> str:
    """Нижний регистр без диакритики: «Placă» == «placa»."""
    return "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))


def _words(words: list[str]) -> re.Pattern:
    # Целые слова: иначе «ram» ловится в «program», а «pc» — где попало
    return re.compile(r"\b(?:" + "|".join(map(re.escape, words)) + r")\b")


_WANT = _words(WANT_KEYWORDS)
_TITLE_BLOCK = _words(TITLE_BLOCK)
_BROKEN = _words(BROKEN)
_TEXT_BLOCK = _words(TEXT_BLOCK)
_LAPTOP_PART = _words(LAPTOP_PART)
_LAPTOP = _words(LAPTOP)
_HDD = _words(HDD)
_SYSTEM = _words(SYSTEM)


def passes_rules(lot: Listing, max_price_ron: float) -> bool:
    title, desc = fold(lot.title), fold(lot.description)
    if _TITLE_BLOCK.search(title) or _TEXT_BLOCK.search(desc):
        return False
    if _LAPTOP_PART.search(title) and _LAPTOP.search(title):
        return False
    if _HDD.search(title) and not _SYSTEM.search(title):
        return False
    # Что это — по заголовку: в описании «laptop», «pc» встречаются у стабилизаторов и чехлов
    if not _WANT.search(title):
        return False
    if lot.is_free:
        return True
    return lot.price_ron is not None and lot.price_ron <= max_price_ron


def is_broken(lot: Listing) -> bool:
    return bool(_BROKEN.search(fold(lot.title)))
