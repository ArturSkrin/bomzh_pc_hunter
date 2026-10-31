"""Дешёвый фильтр по правилам — до LLM, чтобы не жечь токены на мусор."""

import re

from .models import Listing

# TODO: уточнить списки под то, что реально ищем
WANT_KEYWORDS = [
    "placa video", "gpu", "rtx", "gtx", "radeon",
    "procesor", "cpu", "ryzen", "intel core",
    "ram", "ddr4", "ddr5", "ssd", "nvme",
    "placa de baza", "sursa", "pc", "calculator", "laptop", "server",
]
BLOCK_KEYWORDS = ["defect", "pentru piese", "nu porneste", "cumpar", "schimb"]


def _words(words: list[str]) -> re.Pattern:
    # Целые слова: иначе «ram» ловится в «program», а «pc» — где попало
    return re.compile(r"\b(?:" + "|".join(map(re.escape, words)) + r")\b")


_WANT = _words(WANT_KEYWORDS)
_BLOCK = _words(BLOCK_KEYWORDS)


def passes_rules(lot: Listing, max_price_ron: float) -> bool:
    text = f"{lot.title} {lot.description}".lower()
    if _BLOCK.search(text) or not _WANT.search(text):
        return False
    if lot.is_free:
        return True
    return lot.price_ron is not None and lot.price_ron <= max_price_ron
    # TODO: гео — когда решим город/радиус
