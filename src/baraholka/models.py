from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Listing:
    """Объявление в общем для всех сайтов виде."""

    source: str          # "olx", "publi24", ...
    external_id: str     # ID объявления на сайте — ключ дедупа вместе с source
    title: str
    url: str
    price_ron: float | None  # None — цена не указана или «бесплатно» без числа
    is_free: bool = False
    description: str = ""
    city: str = ""
    photos: list[str] = field(default_factory=list)
    posted_at: datetime | None = None
