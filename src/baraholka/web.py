"""Веб-морда: объявления, поисковые запросы, настройки и промпты LLM. Только для localhost."""

import json
import logging
from urllib.parse import urlparse

from flask import Flask, abort, g, jsonify, make_response, redirect, render_template, request, url_for

from . import agent, db

app = Flask(__name__)
log = logging.getLogger("baraholka")

STATUSES = {
    "new": "ждёт оценки", "scored": "оценено", "sent": "в Telegram", "negotiating": "в работе", "skipped": "пропущено",
    "search": "из поиска", "rejected": "отсеяно фильтром", "rejected_llm": "отсеяно нейронкой", "stale": "старое", "send_failed": "не отправилось",
}
# Нижняя граница — чтобы не долбить сайты чаще, чем раз в 5 минут
INT_SETTINGS = {"max_price_ron": 1, "llm_min_score": 0}


def conn():
    if "conn" not in g:
        g.conn = db.connect(app.config["DB_PATH"])
    return g.conn


@app.teardown_appcontext
def close_conn(_exc):
    if "conn" in g:
        g.conn.close()


@app.before_request
def only_local():
    # Чужой Host — DNS-rebinding; POST с чужого Origin — CSRF с любой открытой вкладки
    if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
        abort(403)
    if request.method == "POST":
        origin = request.headers.get("Origin")
        if origin and urlparse(origin).netloc != request.host:
            abort(403)
        if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
            abort(403)


# Колонка в URL -> выражение SQL; только из этого списка, в ORDER BY пользовательский текст не попадает
SORTS = {
    "found": ("id", "найдено"),
    "posted": ("julianday(posted_at)", "дата объявления"),
    "score": ("llm_score", "оценка"),
    "price": ("price_ron", "цена"),
    "title": ("title COLLATE NOCASE", "название"),
    "city": ("city COLLATE NOCASE", "город"),
    "source": ("source", "сайт"),
    "status": ("status", "статус"),
}


def listings_source(search) -> tuple[str, list]:
    """Откуда брать лоты: вся таблица или список поиска, где оценка и комментарий — от поиска."""
    if not search:
        return "listings", []
    cols = [r[1] for r in conn().execute("PRAGMA table_info(listings)") if r[1] not in ("llm_score", "llm_comment")]
    return (f"""(SELECT {', '.join('l.' + c for c in cols)}, si.score AS llm_score,
                 (CASE si.verdict WHEN 'match' THEN '✅ подходит' ELSE '❔ уточнить' END) || ' — ' || si.why AS llm_comment
                 FROM listings l JOIN search_items si ON si.listing_id = l.id WHERE si.search_id = ?)""", [search["id"]])


@app.template_global()
def listings_url(**changes) -> str:
    """Та же страница объявлений с текущими фильтрами, поменяв только переданное."""
    return url_for("listings", **{**request.args.to_dict(), **changes})


@app.get("/")
def listings():
    f = {k: request.args.get(k, "") for k in ("status", "source", "q", "max_price")}
    sort = request.args.get("sort") if request.args.get("sort") in SORTS else "found"
    desc = request.args.get("dir", "desc") != "asc"
    view = request.args.get("view") or request.cookies.get("view")
    view = view if view in ("table", "grid") else "table"
    search = None
    if request.args.get("search", "").isdigit():
        search = conn().execute("SELECT * FROM searches WHERE id = ?", (int(request.args["search"]),)).fetchone()
        if not search:
            abort(404)
        if "sort" not in request.args:
            sort = "score"  # в списке поиска сначала то, что лучше подходит
    table, params = listings_source(search)
    where = ["1"]
    if f["status"]:
        where.append("status = ?"); params.append(f["status"])
    elif not search:
        # «new» ещё не оценены нейронкой — там мусор, который она сейчас отсеет; «search» живёт в своих списках
        where.append("status NOT IN ('new', 'rejected', 'rejected_llm', 'stale', 'search')")
    if f["source"]:
        where.append("source = ?"); params.append(f["source"])
    if f["q"]:
        where.append("(title LIKE ? OR description LIKE ?)"); params += [f"%{f['q']}%"] * 2
    if f["max_price"].replace(".", "", 1).isdigit():
        where.append("(is_free = 1 OR price_ron <= ?)"); params.append(float(f["max_price"]))
    col = SORTS[sort][0]
    # Пустые (без цены/даты) всегда в конце, в какую сторону ни сортируй
    order = f"{col} IS NULL, {col} {'DESC' if desc else 'ASC'}, id DESC"
    # shortcut: первые 300 без пагинации, хватит пока лотов немного
    rows = conn().execute(
        f"SELECT * FROM {table} WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 300", params
    ).fetchall()
    lots = []
    for r in rows:
        photo = (json.loads(r["photos"]) or [""])[0]
        lots.append(dict(r, photo=photo, thumb=photo.replace("800x600", "200x150")))
    base_params = [search["id"]] if search else []
    counts = dict(conn().execute(f"SELECT status, count(*) FROM {table} GROUP BY status", base_params).fetchall())
    sources = [r[0] for r in conn().execute(f"SELECT DISTINCT source FROM {table}", base_params)]
    lists = []
    for r in conn().execute("""SELECT s.id, s.request, count(si.listing_id) AS n FROM searches s
                               LEFT JOIN search_items si ON si.search_id = s.id
                               GROUP BY s.id ORDER BY s.id DESC LIMIT 8"""):
        label = r["request"]  # мой запрос короче и различимее, чем пересказ нейронки
        lists.append({"id": r["id"], "label": label[:40] + ("…" if len(label) > 40 else ""), "n": r["n"]})
    resp = make_response(render_template(
        "listings.html", lots=lots, f=f, counts=counts, sources=sources, statuses=STATUSES,
        sorts=SORTS, sort=sort, desc=desc, view=view, search=search, lists=lists,
    ))
    if request.args.get("view") == view:
        resp.set_cookie("view", view, max_age=365 * 24 * 3600, samesite="Lax")
    return resp


@app.post("/listing/<int:lid>/status")
def set_status(lid):
    status = request.form["status"]
    if status not in STATUSES:
        abort(400)
    conn().execute("UPDATE listings SET status = ? WHERE id = ?", (status, lid))
    conn().commit()
    return redirect(request.referrer or url_for("listings"))


@app.post("/listings/chat")
def listings_chat():
    data = request.get_json(silent=True) or {}
    text = str(data.get("request", "")).strip()
    if not text or not isinstance(data.get("ids"), list):
        abort(400)
    try:
        return jsonify(agent.list_chat(conn(), app.config["SETTINGS"].llm_model, text, data["ids"],
                                       data.get("history") or []))
    except Exception as e:
        log.exception("Чат по списку упал")
        return jsonify({"ids": None, "reply": f"Не получилось ({type(e).__name__}), подробности в логе бота."}), 502


@app.post("/listings/search")
def listings_search():
    # Поиск из чата: 1–2 минуты, страница ждёт с индикатором; результат — отдельный список
    text = str((request.get_json(silent=True) or {}).get("request", "")).strip()
    if not text:
        abort(400)
    s = app.config["SETTINGS"]
    try:
        sid = agent.run_search(conn(), text, s.llm_model, s.request_delay_sec,
                               float(db.get_setting(conn(), "max_price_ron")))
    except Exception as e:
        log.exception("Поиск из чата упал")
        return jsonify({"error": type(e).__name__}), 502
    count = conn().execute("SELECT count(*) FROM search_items WHERE search_id = ?", (sid,)).fetchone()[0]
    return jsonify({"id": sid, "count": count, "url": url_for("listings", search=sid)})


@app.post("/listings/search/<int:sid>/delete")
def delete_search(sid):
    c = conn()
    c.execute("DELETE FROM search_items WHERE search_id = ?", (sid,))
    c.execute("DELETE FROM searches WHERE id = ?", (sid,))
    # Найденное только этим поиском и без переписки больше нигде не видно — чистим
    c.execute("""DELETE FROM listings WHERE status = 'search'
                 AND id NOT IN (SELECT listing_id FROM search_items)
                 AND id NOT IN (SELECT listing_id FROM messages)""")
    c.commit()
    return redirect(url_for("listings"))


@app.post("/listings/bulk-status")
def bulk_status():
    # Массово — только «пропустить»: остальное делаю по одному, глядя на лот
    ids = [int(i) for i in (request.get_json(silent=True) or {}).get("ids", [])]
    marks = ",".join("?" * len(ids)) or "NULL"
    n = conn().execute(f"UPDATE listings SET status = 'skipped' WHERE id IN ({marks})", ids).rowcount
    conn().commit()
    return jsonify({"updated": n})


@app.get("/listing/<int:lid>")
def listing(lid):
    row = conn().execute("SELECT * FROM listings WHERE id = ?", (lid,)).fetchone()
    if not row:
        abort(404)
    msgs = [dict(m, flags=json.loads(m["flags"]))
            for m in conn().execute("SELECT * FROM messages WHERE listing_id = ? ORDER BY id", (lid,))]
    return render_template(
        "listing.html", l=dict(row, photos=json.loads(row["photos"])), msgs=msgs, statuses=STATUSES,
        ceiling=agent.lot_ceiling(conn(), row), error=request.args.get("error"),
    )


@app.post("/listing/<int:lid>/offer")
def set_max_offer(lid):
    value = request.form.get("max_offer", "").strip()
    conn().execute("UPDATE listings SET max_offer_ron = ? WHERE id = ?",
                   (float(value) if value.replace(".", "", 1).isdigit() else None, lid))
    conn().commit()
    return redirect(url_for("listing", lid=lid))


@app.post("/listing/<int:lid>/draft")
def make_draft(lid):
    goal = request.form.get("goal", "reply")
    text = request.form.get("text", "").strip() or None
    if goal not in agent.GOALS:
        abort(400)
    try:
        # shortcut: синхронно, ~10 секунд на ответ модели
        agent.draft_reply(conn(), app.config["SETTINGS"].llm_model, lid, goal, text)
    except Exception as e:
        log.exception("Черновик не написался")
        return redirect(url_for("listing", lid=lid, error=type(e).__name__))
    return redirect(url_for("listing", lid=lid) + "#end")


@app.post("/listing/<int:lid>/message/<int:mid>/sent")
def mark_sent(lid, mid):
    conn().execute("UPDATE messages SET sent = 1 WHERE id = ? AND listing_id = ?", (mid, lid))
    conn().commit()
    return "", 204


@app.post("/listing/<int:lid>/message/<int:mid>/delete")
def delete_message(lid, mid):
    # Ошибочное сообщение (не туда вставил) путает нейронку в следующих черновиках
    conn().execute("DELETE FROM messages WHERE id = ? AND listing_id = ?", (mid, lid))
    conn().commit()
    return redirect(url_for("listing", lid=lid) + "#end")


@app.get("/search")
def search():
    rows = conn().execute("SELECT * FROM searches ORDER BY id DESC LIMIT 20").fetchall()
    items = [dict(r, plan=json.loads(r["plan"]), results=json.loads(r["results"])) for r in rows]
    return render_template("search.html", searches=items, error=request.args.get("error"))


@app.post("/search")
def do_search():
    text = request.form.get("request", "").strip()
    if not text:
        return redirect(url_for("search"))
    s = app.config["SETTINGS"]
    try:
        # shortcut: синхронно, страница ждёт ~1 минуту; фон + опрос — если станет мешать
        sid = agent.run_search(conn(), text, s.llm_model, s.request_delay_sec,
                               float(db.get_setting(conn(), "max_price_ron")))
    except Exception as e:
        log.exception("Поиск упал")
        # Текст ошибки в URL не кладём — у LLM-библиотек туда может попасть кусок ключа
        return redirect(url_for("search", error=type(e).__name__))
    return redirect(url_for("search") + f"#s{sid}")


@app.get("/queries")
def queries():
    rows = conn().execute("SELECT * FROM queries ORDER BY id").fetchall()
    return render_template("queries.html", queries=rows)


@app.post("/queries")
def add_query():
    text = request.form["text"].strip().lower()
    if text:
        conn().execute("INSERT OR IGNORE INTO queries (text) VALUES (?)", (text,))
        conn().commit()
    return redirect(url_for("queries"))


@app.post("/queries/<int:qid>/toggle")
def toggle_query(qid):
    conn().execute("UPDATE queries SET enabled = 1 - enabled WHERE id = ?", (qid,))
    conn().commit()
    return redirect(url_for("queries"))


@app.post("/queries/<int:qid>/delete")
def delete_query(qid):
    conn().execute("DELETE FROM queries WHERE id = ?", (qid,))
    conn().commit()
    return redirect(url_for("queries"))


@app.get("/settings")
def settings():
    values = dict(conn().execute("SELECT key, value FROM settings").fetchall())
    return render_template("settings.html", s=values, saved=request.args.get("saved"), error=request.args.get("error"))


@app.post("/settings")
def save_settings():
    values = {k: request.form.get(k, "").strip() for k in db.DEFAULT_SETTINGS}
    for key, minimum in INT_SETTINGS.items():
        if not values[key].isdigit() or int(values[key]) < minimum:
            return redirect(url_for("settings", error=f"{key}: целое число не меньше {minimum}"))
    try:
        values["collect_times"] = ", ".join(t.strftime("%H:%M") for t in db.parse_times(values["collect_times"]))
    except ValueError:
        return redirect(url_for("settings", error="Время сбора: формат ЧЧ:ММ через запятую, например 09:00, 14:00, 20:00"))
    conn().executemany("UPDATE settings SET value = ? WHERE key = ?", [(v, k) for k, v in values.items()])
    conn().commit()
    return redirect(url_for("settings", saved=1))


def serve(settings) -> None:
    app.config["DB_PATH"] = settings.db_path
    app.config["SETTINGS"] = settings
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    # shortcut: встроенный сервер Flask — норм для одного человека на localhost, не для интернета
    app.run(host="127.0.0.1", port=settings.web_port, use_reloader=False)
