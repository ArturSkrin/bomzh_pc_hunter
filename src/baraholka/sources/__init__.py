"""Источники объявлений. Каждый сайт — модуль с функцией fetch(client, query, max_price=None) -> list[Listing]."""

from . import lajumate, olx, vinted

# Новый сайт = новый модуль + строка здесь
SOURCES = {
    "olx": olx.fetch,
    "lajumate": lajumate.fetch,
    "vinted": vinted.fetch,
}
