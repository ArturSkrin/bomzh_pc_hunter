"""Поиск из чата: LLM превращает запрос в поисковые фразы, бот ищет по сайтам, LLM отбирает подходящее."""

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import litellm

from . import db, guards
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

Ответ — JSON: {"items": [{"id": "<id из входа>", "kind": "pc" | "laptop" | "part" | "other", "score": 0-10,
"resale_ron": число или null, "comment": "..."}]} — по элементу на каждое объявление из входа.
kind: pc — готовый компьютер (системник, мини-ПК, сервер, рабочая станция); laptop — ноутбук;
part — комплектующие (процессор, материнка, память, видеокарта, диск, БП, связки и лоты); other — всё остальное.
Только для pc: resale_ron и comment (по-русски, до 140 символов: конфиг, за сколько уйдёт, в чём выгода или риск).
Для остальных resale_ron = null, comment = "" — только оценка, без пояснений."""
EVAL_BATCH = 30  # объявлений в одном запросе к LLM
LAPTOP_MIN_SCORE = 10  # ноутбуками почти не занимаюсь — только если прям подарок


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
            kind = item.get("kind")
            comment, threshold = "", min_score
            if kind == "pc":
                comment = str(item.get("comment") or "")[:300]
                resale = item.get("resale_ron")
                if isinstance(resale, (int, float)) and resale > 0:
                    comment = f"~{resale:.0f} lei на перепродаже. {comment}"
            elif kind == "laptop":
                comment, threshold = f"ноутбук — беру только от {LAPTOP_MIN_SCORE}/10", max(min_score, LAPTOP_MIN_SCORE)
            conn.execute(
                "UPDATE listings SET llm_score = ?, llm_comment = ?, status = ? WHERE id = ?",
                (score, comment, "scored" if score >= threshold else "rejected_llm", r["id"]),
            )
            done += 1
        conn.commit()
    return done


# Жёсткая часть промпта торга: в коде, правка в вебе её не трогает. Сверху — редактируемый стиль (llm_bargain_prompt)
DRAFT_RULES = """

Ты пишешь от моего имени (я покупатель) сообщение продавцу, я сам его отправлю.
Задача: {goal}
1–2 коротких предложения. Ничего не добавляй сверх задачи и не спрашивай то, что есть в описании.
Цена — не выше {ceiling} lei. Курьер — только с оплатой при получении (ramburs).
Нельзя: предоплата и оплата по ссылке; мой телефон, адрес, встреча; ссылки на «рынок»; выдуманные факты.
JSON: {{"text": "на румынском", "note": "перевод на русский", "offer_ron": цена из сообщения числом или null}}"""

GOALS = {
    "ask": "спроси только то, чего в описании не хватает для решения. Если всего хватает — спроси, актуально ли.",
    "bargain": "предложи цену ниже запрошенной с одним коротким честным аргументом.",
    "reply": "ответь на последнее сообщение продавца. На его цену сам не соглашайся — держи мою последнюю цену, "
             "соглашаюсь я сам через указание.",
    "say": "переведи моё указание (my_instruction) на румынский как сообщение продавцу: ровно мой смысл и мои цифры, "
           "без своих вопросов и условий.",
}


def lot_ceiling(conn: sqlite3.Connection, row: sqlite3.Row) -> float:
    """Потолок для лота: мой «торгуюсь до», цена продавца и общий потолок — берём меньшее."""
    caps = [float(db.get_setting(conn, "max_price_ron"))]
    if row["max_offer_ron"] is not None:
        caps.append(row["max_offer_ron"])
    if row["price_ron"] is not None and not row["is_free"]:
        caps.append(row["price_ron"])
    return min(caps)


def draft_reply(conn: sqlite3.Connection, model: str, lid: int, goal: str, text: str | None = None) -> sqlite3.Row:
    """Пишет следующий черновик и прогоняет его через guards. text — для reply ответ продавца (сохраняется
    в переписку), для say — моё указание, что написать (в переписку не идёт).
    Нарушение — одна попытка исправить; не вышло — черновик сохраняется с флагами, чтобы я их видел."""
    row = conn.execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    seller_text = text if goal == "reply" else None
    if seller_text:
        # Продавец ответил — значит, мой последний черновик он получил
        conn.execute("""UPDATE messages SET sent = 1 WHERE id = (
                          SELECT max(id) FROM messages WHERE listing_id = ? AND direction = 'draft')""", (lid,))
        flags = [v.__dict__ for v in guards.scan_seller_message(seller_text)]
        conn.execute("INSERT INTO messages (listing_id, direction, text, flags) VALUES (?, 'seller', ?, ?)",
                     (lid, seller_text, json.dumps(flags, ensure_ascii=False)))
    ceiling = lot_ceiling(conn, row)
    history = [{"from": "я" if m["direction"] == "draft" else "продавец", "text": m["text"]}
               for m in conn.execute("SELECT * FROM messages WHERE listing_id = ? AND (direction = 'seller' OR sent = 1) "
                                     "ORDER BY id", (lid,))]
    lot = {"title": row["title"], "price_lei": row["price_ron"], "free": bool(row["is_free"]), "city": row["city"],
           "site": row["source"], "description": row["description"][:1500]}
    system = db.get_setting(conn, "llm_bargain_prompt") + DRAFT_RULES.format(goal=GOALS[goal], ceiling=f"{ceiling:.0f}")
    payload = {"listing": lot, "conversation": history}
    instruction = text if goal == "say" else None
    if instruction:
        payload["my_instruction"] = instruction
    user = json.dumps(payload, ensure_ascii=False)

    for attempt in range(2):
        answer = llm_json(model, system, user)
        draft, note = str(answer.get("text") or "").strip(), str(answer.get("note") or "")[:500]
        violations = guards.check_draft(draft, ceiling, has_comparables=False)
        offer = answer.get("offer_ron")
        if isinstance(offer, (int, float)):
            try:
                guards.check_offer(offer, ceiling)
            except ValueError as e:
                violations.append(guards.Violation("ceiling", str(e)))
        if not violations:
            break
        user += "\n\nТвой прошлый черновик нарушил правила, перепиши: " + "; ".join(
            f"{v.rule}: {v.detail}" for v in violations)

    asked = {"ask": "уточнить", "bargain": "предложить цену", "say": instruction}.get(goal, "")
    cur = conn.execute(
        "INSERT INTO messages (listing_id, direction, text, note, flags, prompt) VALUES (?, 'draft', ?, ?, ?, ?)",
        (lid, draft, note, json.dumps([v.__dict__ for v in violations], ensure_ascii=False), asked or ""),
    )
    # Черновик всегда один: новый заменяет неотправленный старый
    conn.execute("DELETE FROM messages WHERE listing_id = ? AND direction = 'draft' AND sent = 0 AND id != ?",
                 (lid, cur.lastrowid))
    conn.execute("UPDATE listings SET status = 'negotiating' WHERE id = ?", (lid,))
    conn.commit()
    return conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()
