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
    return conn


def status(conn, ext):
    return conn.execute("SELECT status FROM listings WHERE external_id = ?", (ext,)).fetchone()[0]


def test_card_escapes_html_and_has_buttons(tmp_path):
    conn = make_db(tmp_path)
    row = conn.execute("SELECT * FROM listings WHERE external_id = 'fresh'").fetchone()
    text, markup = card(row)
    assert "&lt;3060&gt;" in text and "a=1&amp;b=2" in text
    assert "900 lei" in text
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
