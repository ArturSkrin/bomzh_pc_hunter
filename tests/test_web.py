from datetime import datetime, timezone

import pytest

from baraholka import db
from baraholka.models import Listing
from baraholka.web import app


@pytest.fixture
def client(tmp_path):
    path = tmp_path / "w.db"
    conn = db.connect(path)
    db.insert_if_new(conn, Listing("olx", "1", "RTX 3060 <b>", "https://x/1", 900, posted_at=datetime.now(timezone.utc)))
    conn.execute("UPDATE listings SET status = 'scored'")
    conn.commit()
    conn.close()
    app.config["DB_PATH"] = path
    return app.test_client()


def test_listings_page_escapes(client):
    html = client.get("/").get_data(as_text=True)
    assert "RTX 3060 &lt;b&gt;" in html and "900 lei" in html


def test_queries_crud(client):
    client.post("/queries", data={"text": "  Placa De Baza AM4 "})
    assert "placa de baza am4" in client.get("/queries").get_data(as_text=True)
    conn = db.connect(app.config["DB_PATH"])
    qid = conn.execute("SELECT id FROM queries WHERE text = 'placa de baza am4'").fetchone()[0]
    client.post(f"/queries/{qid}/toggle")
    assert "placa de baza am4" not in db.enabled_queries(conn)
    client.post(f"/queries/{qid}/delete")
    assert conn.execute("SELECT count(*) FROM queries WHERE id = ?", (qid,)).fetchone()[0] == 0


def test_settings_validation(client):
    form = {k: v for k, v in db.DEFAULT_SETTINGS.items()}
    for bad in ({"max_price_ron": "0"}, {"collect_times": "9 утра"}, {"collect_times": ""}):
        assert "error" in client.post("/settings", data={**form, **bad}).headers["Location"]
    client.post("/settings", data={**form, "collect_times": "20:00,9:05"})
    assert db.get_setting(db.connect(app.config["DB_PATH"]), "collect_times") == "09:05, 20:00"
    client.post("/settings", data={**form, "max_price_ron": "300", "llm_eval_prompt": "новый промпт"})
    conn = db.connect(app.config["DB_PATH"])
    assert db.get_setting(conn, "max_price_ron") == "300"
    assert db.get_setting(conn, "llm_eval_prompt") == "новый промпт"


def test_cross_site_post_and_foreign_host_blocked(client):
    assert client.post("/queries", data={"text": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/queries", data={"text": "x"}, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 403


def test_search_page_renders(client):
    html = client.get("/search").get_data(as_text=True)
    assert "Искать" in html and "Пока пусто" in html


def titles_in_order(html, names):
    return sorted(names, key=html.index)


def test_sort_by_price_nulls_last(client):
    conn = db.connect(app.config["DB_PATH"])
    now = datetime.now(timezone.utc)
    db.insert_if_new(conn, Listing("olx", "2", "Ieftin GTX", "https://x/2", 100, posted_at=now))
    db.insert_if_new(conn, Listing("olx", "3", "Fara pret SSD", "https://x/3", None, posted_at=now))
    conn.execute("UPDATE listings SET status = 'sent'")
    conn.commit()
    names = ["Ieftin GTX", "RTX 3060", "Fara pret SSD"]
    asc = client.get("/?sort=price&dir=asc").get_data(as_text=True)
    assert titles_in_order(asc, names) == ["Ieftin GTX", "RTX 3060", "Fara pret SSD"]
    desc = client.get("/?sort=price&dir=desc").get_data(as_text=True)
    assert titles_in_order(desc, names) == ["RTX 3060", "Ieftin GTX", "Fara pret SSD"]


def test_unknown_sort_is_ignored(client):
    assert client.get("/?sort=id;DROP TABLE listings").status_code == 200
    assert client.get("/?sort=price&dir=asc;--").status_code == 200


def test_view_toggle_remembered(client):
    r = client.get("/?view=grid")
    assert 'class="grid"' in r.get_data(as_text=True)
    assert "view=grid" in r.headers["Set-Cookie"]
    assert 'class="grid"' in client.get("/").get_data(as_text=True)  # из cookie
    assert 'class="grid"' not in client.get("/?view=table").get_data(as_text=True)


def test_listing_page_and_draft(client, monkeypatch):
    from baraholka import agent
    from types import SimpleNamespace
    app.config["SETTINGS"] = SimpleNamespace(llm_model="m")
    monkeypatch.setattr(agent, "llm_json", lambda *a: {"text": "Bună ziua, mai este?", "note": "уточнение"})
    conn = db.connect(app.config["DB_PATH"])
    conn.execute("UPDATE settings SET value = '500' WHERE key = 'max_price_ron'")
    conn.commit()
    assert client.post("/listing/1/offer", data={"max_offer": "700"}).status_code == 302
    html = client.get("/listing/1").get_data(as_text=True)
    assert "RTX 3060 &lt;b&gt;" in html and "потолок сейчас: 500 lei" in html  # общий 500 меньше моих 700 и цены 900
    client.post("/listing/1/draft", data={"goal": "reply", "seller": "Da, <merge>"})
    html = client.get("/listing/1").get_data(as_text=True)
    assert "Bună ziua, mai este?" in html and "Da, &lt;merge&gt;" in html
    assert client.post("/listing/1/draft", data={"goal": "hack"}).status_code == 400


def test_unscored_hidden_by_default(client):
    conn = db.connect(app.config["DB_PATH"])
    db.insert_if_new(conn, Listing("olx", "9", "Stabilizator neevaluat", "https://x/9", 100))
    assert "neevaluat" not in client.get("/").get_data(as_text=True)
    assert "neevaluat" in client.get("/?status=new").get_data(as_text=True)
