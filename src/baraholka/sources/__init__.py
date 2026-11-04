"""Источники объявлений. Каждый сайт — модуль с функцией fetch(client, query, max_price=None, cheapest=False)
-> list[Listing]; cheapest — сначала дешёвые вместо свежих (для поиска «N самых дешёвых»)."""

from . import lajumate, olx, vinted

# Новый сайт = новый модуль + строка здесь
SOURCES = {
    "olx": olx.fetch,
    "lajumate": lajumate.fetch,
    "vinted": vinted.fetch,
}
