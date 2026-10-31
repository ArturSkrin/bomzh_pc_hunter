"""Поиск из чата: LLM превращает запрос в поисковые фразы, бот ищет по сайтам, LLM отбирает подходящее."""

import json
import logging
import sqlite3
import time

import litellm

from . import db
from .sources import SOURCES

log = logging.getLogger("baraholka")

MAX_CANDIDATES = 80  # больше в один запрос к LLM не шлём — дорого и модель начинает путаться

PLAN_PROMPT = """Ты помогаешь искать б/у товары на румынских барахолках (OLX.ro и подобных).
По запросу пользователя верни JSON:
{"queries": [...], "max_price": число в лей или null, "must": [...], "nice": [...], "summary": "..."}
- queries: 4–8 коротких поисковых фраз по 1–4 слова. Сайт ищет по совпадению слов, длинные фразы ничего не находят.
  Смешай общие фразы на румынском (как пишут продавцы) и названия конкретных моделей, которые точно подходят
  под обязательные требования.
- must: обязательные требования, коротко. nice: желательные. summary: одной строкой по-русски, что ищем."""

JUDGE_PROMPT = """Проверь объявления с барахолки на соответствие запросу.
Обязательные требования: {must}
Желательные: {nice}
Отбрось всё, что явно не подходит по обязательным. Если в объявлении данных нет, но модель названа и ты знаешь
её характеристики — опирайся на них и так и напиши («по модели: ...»). Если проверить нельзя — verdict "unclear".
Верни JSON: {{"matches": [{{"id": "...", "verdict": "match" | "unclear", "score": 0-10, "why": "коротко по-русски"}}]}}
score учитывает и желательные требования, и цену."""


def llm_json(model: str, system: str, user: str) -> dict:
    resp = litellm.completion(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
    )
    return json.loads(resp.choices[0].message.content)


def run_search(conn: sqlite3.Connection, request: str, model: str, delay_sec: float, default_max_price: float) -> int:
    """Выполняет поиск, сохраняет его в историю, возвращает id поиска."""
    plan = llm_json(model, PLAN_PROMPT, request)
    queries = [q for q in plan.get("queries", []) if isinstance(q, str) and q.strip()][:8]
    try:
        max_price = float(plan["max_price"])
    except (KeyError, TypeError, ValueError):
        max_price = default_max_price
    log.info("Поиск %r: %s, до %s lei", request, queries, max_price)

    candidates = {}
    for name, fetch in SOURCES.items():
        for q in queries:
            try:
                for lot in fetch(None, q, max_price=max_price):
                    candidates.setdefault(f"{lot.source}:{lot.external_id}", lot)
            except Exception:
                log.exception("%s: %r упал", name, q)
            time.sleep(delay_sec)
    lots = dict(list(candidates.items())[:MAX_CANDIDATES])

    results = []
    if lots:
        brief = [
            {"id": key, "title": l.title, "price_lei": l.price_ron, "city": l.city, "description": l.description[:500]}
            for key, l in lots.items()
        ]
        judged = llm_json(
            model,
            JUDGE_PROMPT.format(must="; ".join(plan.get("must", [])), nice="; ".join(plan.get("nice", []))),
            json.dumps(brief, ensure_ascii=False),
        )
        for m in judged.get("matches", []):
            lot = lots.get(m.get("id"))  # id только из наших кандидатов — LLM может выдумать
            if not lot or m.get("verdict") not in ("match", "unclear"):
                continue
            lid = db.insert_if_new(conn, lot)
            if lid:  # чтобы сборщик не отправил его в Telegram как новое
                conn.execute("UPDATE listings SET status = 'search' WHERE id = ?", (lid,))
            results.append({
                "title": lot.title, "url": lot.url, "price_ron": lot.price_ron, "is_free": lot.is_free,
                "city": lot.city, "thumb": (lot.photos or [""])[0].replace("800x600", "200x150"),
                "verdict": m["verdict"], "score": max(0, min(10, int(m.get("score") or 0))), "why": str(m.get("why", "")),
            })
    results.sort(key=lambda r: (r["verdict"] == "match", r["score"]), reverse=True)

    cur = conn.execute(
        "INSERT INTO searches (request, plan, results, candidates) VALUES (?, ?, ?, ?)",
        (request, json.dumps(plan, ensure_ascii=False), json.dumps(results, ensure_ascii=False), len(lots)),
    )
    conn.commit()
    return cur.lastrowid
