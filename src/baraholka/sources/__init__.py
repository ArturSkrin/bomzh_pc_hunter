"""Источники объявлений. Каждый сайт — модуль с функцией fetch(client, query) -> list[Listing]."""

from . import olx

# Новый сайт = новый модуль + строка здесь
SOURCES = {
    "olx": olx.fetch,
}
