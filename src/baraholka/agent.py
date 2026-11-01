"""Поиск из чата: LLM превращает запрос в поисковые фразы, бот ищет по сайтам, LLM отбирает подходящее."""

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

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


# Формат ответа — в коде, а не в редактируемом промпте: правка промпта в вебе не должна ломать разбор
EVAL_FORMAT = """

Ответ — JSON: {"items": [{"id": "<id из входа>", "score": 0-10, "resale_ron": число или null,
"comment": "по-русски, до 140 символов: что это, за сколько уйдёт, в чём выгода или риск"}]}
— по элементу на каждое объявление из входа."""
EVAL_BATCH = 30  # объявлений в одном запросе к LLM


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


def evaluate_new(conn: sqlite3.Connection, model: str, prompt: str, min_score: float) -> int:
    """Оценивает лоты 'new' глазами перекупа: >= min_score -> 'scored' (уйдут в Telegram), ниже -> 'rejected_llm'.
    Старше суток — 'stale' без LLM. Возвращает, сколько оценено; пропущенные моделью ждут следующего прохода."""
    day_ago = datetime.now(timezone.utc) - timedelta(days=1)
    fresh = []
    for r in conn.execute("SELECT * FROM listings WHERE status = 'new' ORDER BY id DESC").fetchall():
        if r["posted_at"] and datetime.fromisoformat(r["posted_at"]) >= day_ago:
            fresh.append(r)
        else:
            conn.execute("UPDATE listings SET status = 'stale' WHERE id = ?", (r["id"],))
    conn.commit()

    done = 0
    for i in range(0, len(fresh), EVAL_BATCH):
        batch = {str(r["id"]): r for r in fresh[i:i + EVAL_BATCH]}
        brief = [
            {"id": k, "title": r["title"], "price_lei": r["price_ron"], "free": bool(r["is_free"]),
             "city": r["city"] or "?", "source": r["source"], "description": r["description"][:400]}
            for k, r in batch.items()
        ]
        answer = llm_json(model, prompt + EVAL_FORMAT, json.dumps(brief, ensure_ascii=False))
        for item in answer.get("items", []):
            r = batch.get(str(item.get("id")))  # id только из нашей пачки
            try:
                score = max(0.0, min(10.0, float(item["score"])))
            except (KeyError, TypeError, ValueError):
                continue
            if r is None:
                continue
            comment = str(item.get("comment") or "")[:300]
            resale = item.get("resale_ron")
            if isinstance(resale, (int, float)) and resale > 0:
                comment = f"~{resale:.0f} lei на перепродаже. {comment}"
            conn.execute(
                "UPDATE listings SET llm_score = ?, llm_comment = ?, status = ? WHERE id = ?",
                (score, comment, "scored" if score >= min_score else "rejected_llm", r["id"]),
            )
            done += 1
        conn.commit()
    return done
