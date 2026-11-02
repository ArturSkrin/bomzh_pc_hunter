"""Жёсткие правила торга. Работают в коде поверх ответа LLM, а не в промпте:
что бы модель ни написала, черновик с нарушением до меня не дойдёт.
"""

import re
from dataclasses import dataclass

# Предоплата и оплата по ссылке — на румынском (без диакритик тоже) и английском
_PAYMENT_PATTERNS = [
    r"\bavans\b",
    r"plat[aă]\s+(?:[iî]n\s+avans|online|anticipat[aă]|prin\s+link)",
    r"\blink\w*\s+(?:de\s+)?plat",
    r"\btransfer\s+bancar\b",
    r"\b(?:iban|revolut|paypal|western\s+union)\b",
    r"\bolx\s+livrare\b",  # там платишь картой онлайн до получения
    r"\bprepay\w*|\bpayment\s+link\b",
]

# Курьер можно, но только с оплатой при получении: деньги — после того, как посылка у меня в руках
_COURIER_PATTERNS = [
    r"\bcurier\w*\b",
    r"\b(?:fan\s*courier|sameday|cargus|dpd|gls|posta\s+romana|poșta\s+română)\b",
    r"\bcourier\b",
]
_COD_RE = re.compile(r"\bramburs\w*|\bplat[aă]\s+la\s+(?:livrare|primire)|\bcash\s+on\s+delivery\b", re.IGNORECASE)

# Личные данные и встреча — это решаю только я
_PERSONAL_PATTERNS = [
    r"(?:\+?40|\b0)\s?7\d{2}[\s.-]?\d{3}[\s.-]?\d{3}\b",  # румынский мобильный
    r"\b(?:str\.|strada|bulevardul|bd\.|bloc|apartament|ap\.)\s",
    r"\b(?:ne\s+vedem|ne\s+[iî]nt[aâ]lnim|[iî]nt[aâ]lnire)\b",
    r"\badresa\s+mea\b",
]

_PRICE_RE = re.compile(r"(\d[\d\s.]*)\s*(?:de\s+)?(?:lei|ron)\b", re.IGNORECASE)

# Фразы-маркеры ссылки на рынок: допустимы, только если есть реальные похожие объявления
_MARKET_CLAIM_RE = re.compile(
    r"\b(?:am\s+v[aă]zut|se\s+g[aă]sesc?|pe\s+pia[tț][aă]|alte\s+anun[tț]uri)\b",
    re.IGNORECASE,
)


@dataclass
class Violation:
    rule: str
    detail: str


# «nu pot plăti în avans», «fără avans» — это отказ, а не предложение
_NEGATION_RE = re.compile(r"\b(?:nu|f[aă]r[aă])\b[^.,;!?\n]{0,30}$", re.IGNORECASE)


def _find(patterns: list[str], text: str, allow_negated: bool = False) -> list[str]:
    hits = []
    for p in patterns:
        for m in re.finditer(p, text, re.IGNORECASE):
            if allow_negated and _NEGATION_RE.search(text[:m.start()]):
                continue
            hits.append(m.group(0))
            break
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
    for hit in _find(_PAYMENT_PATTERNS, text, allow_negated=True):
        violations.append(Violation("payment", hit))
    if not _COD_RE.search(text):
        for hit in _find(_COURIER_PATTERNS, text):
            violations.append(Violation("courier", f"{hit} без ramburs"))
    for hit in _find(_PERSONAL_PATTERNS, text):
        violations.append(Violation("personal", hit))
    for price in _prices_in(text):
        if price > ceiling_ron:
            violations.append(Violation("ceiling", f"{price} lei > {ceiling_ron}"))
    if not has_comparables and _MARKET_CLAIM_RE.search(text):
        violations.append(Violation("made_up", "ссылка на рынок без реальных похожих объявлений"))
    return violations


def scan_seller_message(text: str) -> list[Violation]:
    """Красные флаги в ответе продавца: предоплата, ссылка на оплату. Курьер сам по себе — норм."""
    violations = [Violation("payment", hit) for hit in _find(_PAYMENT_PATTERNS, text)]
    if re.search(r"https?://", text):
        violations.append(Violation("link", "в сообщении есть ссылка"))
    return violations
