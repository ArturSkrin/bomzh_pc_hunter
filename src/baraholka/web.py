"""Веб-морда: объявления, поисковые запросы, настройки и промпты LLM. Только для localhost."""

import json
import logging
from urllib.parse import urlparse

from flask import Flask, abort, g, make_response, redirect, render_template, request, url_for

from . import agent, db

app = Flask(__name__)
log = logging.getLogger("baraholka")

STATUSES = {
    "new": "новое", "scored": "оценено", "sent": "в Telegram", "negotiating": "в работе", "skipped": "пропущено",
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
    where, params = [], []
    if f["status"]:
        where.append("status = ?"); params.append(f["status"])
    else:
        where.append("status NOT IN ('rejected', 'rejected_llm', 'stale')")
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
        f"SELECT * FROM listings WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 300", params
    ).fetchall()
    lots = []
    for r in rows:
        photo = (json.loads(r["photos"]) or [""])[0]
        lots.append(dict(r, photo=photo, thumb=photo.replace("800x600", "200x150")))
    counts = dict(conn().execute("SELECT status, count(*) FROM listings GROUP BY status").fetchall())
    sources = [r[0] for r in conn().execute("SELECT DISTINCT source FROM listings")]
    resp = make_response(render_template(
        "listings.html", lots=lots, f=f, counts=counts, sources=sources, statuses=STATUSES,
        sorts=SORTS, sort=sort, desc=desc, view=view,
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
