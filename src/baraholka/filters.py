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
    "ssd", "nvme", "m.2", "hdd", "hard disk", "harddisk",
    "sursa", "unitate pc", "unitate", "sistem pc", "desktop", "mini pc", "workstation", "server",
    "optiplex", "thinkcentre", "elitedesk", "prodesk", "pc", "calculator", "laptop", "notebook",
    # линейки ноутбуков — в заголовке часто нет слова «laptop»
    "macbook", "thinkpad", "latitude", "elitebook", "probook", "ideapad", "vivobook", "zenbook", "aspire", "pavilion",
]
# Только в заголовке: в описании рабочих лотов пишут «nu fac schimb», «fara niciun defect», «geanta cadou»
TITLE_BLOCK = [
    # битое (решили не брать)
    "defect", "defecta", "defecte", "pentru piese", "nefunctional", "nefunctionala", "nu porneste", "nu merge",
    # не продажа
    "cumpar", "caut", "schimb",
    # сумки, чехлы, аксессуары
    "geanta", "genti", "rucsac", "husa", "huse", "sleeve", "taska", "ruksak", "torba", "backpack", "bag",
    "incarcator", "alimentator", "cablu", "adaptor", "mouse", "mousepad", "casti", "suport", "stand", "cooler laptop",
    # калькуляторы и игрушки
    "stiintific", "de buzunar", "casio", "citizen", "jucarie", "jucarii", "copii", "fisher", "vtech", "interactiv",
    "interaktywny", "lego",
]
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
_TEXT_BLOCK = _words(TEXT_BLOCK)


def passes_rules(lot: Listing, max_price_ron: float) -> bool:
    title, desc = fold(lot.title), fold(lot.description)
    if _TITLE_BLOCK.search(title) or _TEXT_BLOCK.search(desc):
        return False
    if not _WANT.search(f"{title} {desc}"):
        return False
    if lot.is_free:
        return True
    return lot.price_ron is not None and lot.price_ron <= max_price_ron
