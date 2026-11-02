from datetime import datetime, timedelta, timezone

from baraholka import db
from baraholka.__main__ import send_new
from baraholka.models import Listing
from baraholka.tg import card, handle


class FakeBot:
    def __init__(self):
        self.sent = []

    def send_card(self, row):
        self.sent.append(row["external_id"])


def make_db(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    now = datetime.now(timezone.utc)
    db.insert_if_new(conn, Listing("olx", "fresh", "RTX <3060>", "https://x/?a=1&b=2", 900, posted_at=now))
    db.insert_if_new(conn, Listing("olx", "old", "GTX 1060", "https://x/old", 300, posted_at=now - timedelta(days=3)))
    db.insert_if_new(conn, Listing("olx", "free", "Calculator vechi", "https://x/free", 0, is_free=True, posted_at=now))
    # В Telegram уходит только оценённое нейронкой
    conn.execute("UPDATE listings SET status = 'scored', llm_score = 7, llm_comment = '~1200 lei, <маржа>'")
    conn.commit()
    return conn


def status(conn, ext):
    return conn.execute("SELECT status FROM listings WHERE external_id = ?", (ext,)).fetchone()[0]


def test_card_escapes_html_and_has_buttons(tmp_path):
    conn = make_db(tmp_path)
    row = conn.execute("SELECT * FROM listings WHERE external_id = 'fresh'").fetchone()
    text, markup = card(row)
    assert "&lt;3060&gt;" in text and "a=1&amp;b=2" in text
    assert "900 lei" in text
    assert "7/10" in text and "&lt;маржа&gt;" in text  # оценка и экранированный комментарий
    assert [b["callback_data"] for b in markup["inline_keyboard"][0]] == [f"ask:{row['id']}", f"bargain:{row['id']}", f"skip:{row['id']}"]

    free = conn.execute("SELECT * FROM listings WHERE external_id = 'free'").fetchone()
    assert "БЕСПЛАТНО" in card(free)[0]


def test_send_new_skips_stale(tmp_path, monkeypatch):
    monkeypatch.setattr("baraholka.__main__.time.sleep", lambda s: None)
    conn = make_db(tmp_path)
    bot = FakeBot()
    send_new(bot, conn)
    assert sorted(bot.sent) == ["free", "fresh"]
    assert status(conn, "old") == "stale"
    assert status(conn, "fresh") == "sent"


def test_skip_button(tmp_path):
    conn = make_db(tmp_path)
    lid = conn.execute("SELECT id FROM listings WHERE external_id = 'fresh'").fetchone()[0]
    assert handle(conn, f"skip:{lid}") == "Пропущено"
    assert status(conn, "fresh") == "skipped"


def test_ask_and_bargain_move_to_work(tmp_path):
    conn = make_db(tmp_path)
    lid = conn.execute("SELECT id FROM listings WHERE external_id = 'fresh'").fetchone()[0]
    for action in ("ask", "bargain"):
        conn.execute("UPDATE listings SET status = 'sent' WHERE id = ?", (lid,))
        assert "в работу" in handle(conn, f"{action}:{lid}").lower()
        assert status(conn, "fresh") == "negotiating"


class FakeApi:
    """Bot с подменённым call: отдаёт заготовленные апдейты и записывает отправленное."""

    def __init__(self, updates):
        from baraholka.tg import Bot
        self.bot = Bot("t", "42")
        self.out = []
        self.bot.call = lambda method, **p: updates if method == "getUpdates" else self.out.append((method, p))


def test_buttons_and_replies_make_drafts(tmp_path):
    import json
    conn = make_db(tmp_path)
    lid = conn.execute("SELECT id FROM listings WHERE external_id = 'fresh'").fetchone()[0]
    calls = []

    def drafter(l, goal, seller):
        calls.append((l, goal, seller))
        return {"text": "Bună <ziua>", "note": "уточнение", "flags": json.dumps([{"rule": "personal", "detail": "x"}])}

    chat = {"id": 42}
    api = FakeApi([
        {"update_id": 1, "callback_query": {"id": "q", "data": f"ask:{lid}", "message": {"chat": chat, "message_id": 5}}},
        {"update_id": 2, "message": {"chat": chat, "text": "Da, merge",
                                     "reply_to_message": {"caption": f"... #lot{lid}"}}},
        {"update_id": 3, "message": {"chat": chat, "text": "без reply"}},
        {"update_id": 4, "message": {"chat": {"id": 666}, "text": "чужой", "reply_to_message": {"text": f"#lot{lid}"}}},
    ])
    api.bot.poll(conn, 0, drafter=drafter)

    assert calls == [(lid, "ask", None), (lid, "reply", "Da, merge")]
    texts = [p["text"] for m, p in api.out if m == "sendMessage"]
    assert "<code>Bună &lt;ziua&gt;</code>" in texts[0] and f"#lot{lid}" in texts[0] and "⚠️ personal" in texts[0]
    assert "reply" in texts[2] and len(texts) == 3  # без reply — подсказка; чужой чат — тишина
    assert status(conn, "fresh") == "negotiating"
