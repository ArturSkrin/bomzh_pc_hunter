"""Жёсткие правила торга. Работают в коде поверх ответа LLM, а не в промпте:
что бы модель ни написала, черновик с нарушением до меня не дойдёт.
"""

import re
from dataclasses import dataclass

# Предоплата, курьеры, оплата по ссылке — на румынском (без диакритик тоже) и английском
_PAYMENT_PATTERNS = [
    r"\bavans\b",
    r"plat[aă]\s+(?:[iî]n\s+avans|online|anticipat[aă]|prin\s+link)",
    r"\blink\w*\s+(?:de\s+)?plat",
    r"\btransfer\s+bancar\b",
    r"\b(?:iban|revolut|paypal|western\s+union)\b",
    r"\bcurier\w*\b",
    r"\b(?:fan\s*courier|sameday|cargus|dpd|gls|posta\s+romana|poșta\s+română)\b",
    r"\bolx\s+livrare\b",
    r"\bprepay\w*|\bcourier\b|\bpayment\s+link\b",
]

# Личные данные и встреча — это решаю только я
_PERSONAL_PATTERNS = [
    r"(?:\+?40|\b0)\s?7\d{2}[\s.-]?\d{3}[\s.-]?\d{3}\b",  # румынский мобильный
    r"\b(?:str\.|strada|bulevardul|bd\.|bloc|apartament|ap\.)\s",
    r"\b(?:ne\s+vedem|ne\s+[iî]nt[aâ]lnim|[iî]nt[aâ]lnire)\b",
    r"\badresa\s+mea\b",
]

_PRICE_RE = re.compile(r"(\d[\d\s.]*)\s*(?:lei|ron)\b", re.IGNORECASE)

# Фразы-маркеры ссылки на рынок: допустимы, только если есть реальные похожие объявления
_MARKET_CLAIM_RE = re.compile(
    r"\b(?:am\s+v[aă]zut|se\s+g[aă]sesc?|pe\s+pia[tț][aă]|alte\s+anun[tț]uri)\b",
    re.IGNORECASE,
)


@dataclass
class Violation:
    rule: str
    detail: str


def _find(patterns: list[str], text: str) -> list[str]:
    hits = []
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            hits.append(m.group(0))
    return hits


def _prices_in(text: str) -> list[int]:
    return [int(re.sub(r"[\s.]", "", m)) for m in _PRICE_RE.findall(text)]


def check_offer(offer_ron: float, ceiling_ron: float) -> None:
    """Потолок цены. Бросает исключение, а не возвращает флаг, чтобы его нельзя было проигнорировать."""
    if offer_ron > ceiling_ron:
        raise ValueError(f"Предложение {offer_ron} RON выше потолка {ceiling_ron} RON")


def check_draft(text: str, ceiling_ron: float, has_comparables: bool) -> list[Violation]:
    """Проверяет черновик сообщения продавцу. Пустой список — можно показывать мне."""
    violations = []
    for hit in _find(_PAYMENT_PATTERNS, text):
        violations.append(Violation("payment", hit))
    for hit in _find(_PERSONAL_PATTERNS, text):
        violations.append(Violation("personal", hit))
    for price in _prices_in(text):
        if price > ceiling_ron:
            violations.append(Violation("ceiling", f"{price} lei > {ceiling_ron}"))
    if not has_comparables and _MARKET_CLAIM_RE.search(text):
        violations.append(Violation("made_up", "ссылка на рынок без реальных похожих объявлений"))
    return violations


def scan_seller_message(text: str) -> list[Violation]:
    """Красные флаги в ответе продавца: предоплата, курьер, ссылка на оплату."""
    violations = [Violation("payment", hit) for hit in _find(_PAYMENT_PATTERNS, text)]
    if re.search(r"https?://", text):
        violations.append(Violation("link", "в сообщении есть ссылка"))
    return violations
