import json

from baraholka import agent, db
from baraholka.models import Listing

LOTS = [
    Listing("olx", "1", "Dell U2722D 27 QHD", "https://x/1", 920, photos=["https://img/1;s=800x600"]),
    Listing("olx", "2", "Samsung Odyssey G5 27 curbat", "https://x/2", 800, description="Monitor gaming 27 QHD"),
    Listing("olx", "3", "Monitor 24 FHD", "https://x/3", 300),
]


def test_run_search(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(client, query, max_price=None, cheapest=False):
        calls.append((query, max_price))
        return LOTS

    answers = iter([
        {"queries": ["monitor 27 qhd", "dell u2722d"], "max_price": "1000 lei", "must": ["27", "QHD", "высота"],
         "nice": ["изогнутый"], "summary": "монитор"},
        {"matches": [
            {"id": "olx:1", "verdict": "match", "score": 9, "why": "по модели: есть регулировка высоты"},
            {"id": "olx:2", "verdict": "unclear", "score": 15, "why": "высота не указана"},
            {"id": "olx:3", "verdict": "no", "score": 1, "why": "24 дюйма"},
            {"id": "olx:999", "verdict": "match", "score": 10, "why": "выдумано"},
        ]},
    ])
    monkeypatch.setattr(agent, "llm_json", lambda *a, **kw: next(answers))
    monkeypatch.setattr(agent, "SOURCES", {"olx": fake_fetch})

    conn = db.connect(tmp_path / "a.db")
    sid = agent.run_search(conn, "монитор 27 QHD с регулировкой высоты до 1000", "m", 0)

    # «1000 lei» не число — без потолка, но поиск не падает
    assert calls == [("monitor 27 qhd", None), ("dell u2722d", None)]
    row = conn.execute("SELECT * FROM searches WHERE id = ?", (sid,)).fetchone()
    results = json.loads(row["results"])
    assert [r["url"] for r in results] == ["https://x/1", "https://x/2"]  # match выше unclear, выдуманный id отброшен
    assert results[1]["score"] == 10  # оценка зажата в 0–10
    assert results[0]["thumb"] == "https://img/1;s=200x150"
    assert row["candidates"] == 2  # «Monitor 24 FHD» не содержит слов ни одной фразы — отсечён до нейронки
    # Найденное через поиск не должно улететь в Telegram как «новое»
    assert {r[0] for r in conn.execute("SELECT status FROM listings")} == {"search"}
    items = conn.execute("SELECT verdict, score FROM search_items WHERE search_id = ? ORDER BY score DESC", (sid,)).fetchall()
    assert [tuple(i) for i in items] == [("unclear", 10), ("match", 9)]


def test_evaluate_new(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    conn = db.connect(tmp_path / "e.db")
    for ext, title, posted in [("1", "Unitate PC i5 GTX 1060", now), ("2", "Placa video RX 580", now),
                               ("3", "Ram DDR3 2GB", now), ("4", "Laptop vechi", now - timedelta(days=3)),
                               ("5", "Laptop Dell i7", now), ("6", "Laptop Lenovo T480", now), ("7", "SSD 1TB", now)]:
        db.insert_if_new(conn, Listing("olx", ext, title, f"https://x/{ext}", 300, posted_at=posted))
    ids = {r["external_id"]: str(r["id"]) for r in conn.execute("SELECT id, external_id FROM listings")}
    seen = {}

    def fake_llm(model, system, user):
        seen["system"], seen["user"] = system, json.loads(user)
        return {"items": [
            {"id": ids["1"], "kind": "pc", "score": 8, "resale_ron": 900, "comment": "офисник под апгрейд"},
            {"id": ids["2"], "kind": "part", "score": "4", "comment": "рынок"},
            {"id": ids["5"], "kind": "laptop", "score": 9, "comment": ""},
            {"id": ids["6"], "kind": "laptop", "score": 10, "comment": ""},
            {"id": ids["7"], "kind": "part", "score": 7, "resale_ron": 300, "comment": "лишний текст"},
            {"id": "99999", "score": 10, "comment": "выдумано"},
        ]}  # id 3 модель пропустила

    monkeypatch.setattr(agent, "llm_json", fake_llm)
    assert agent.evaluate_new(conn, "m", "ты перекуп", 5) == 5

    st = {r["external_id"]: (r["status"], r["llm_score"], r["llm_comment"]) for r in conn.execute("SELECT * FROM listings")}
    assert st["1"] == ("scored", 8, "~900 lei на перепродаже. офисник под апгрейд")
    assert st["2"][:2] == ("rejected_llm", 4)
    assert st["5"][0] == "rejected_llm" and st["6"][0] == "scored"  # ноутбук — только 10/10
    assert st["7"] == ("scored", 7, "")  # комплектующим — только оценка, без комментария
    assert st["3"][0] == "new"  # пропущенное ждёт следующего прохода
    assert st["4"][0] == "stale"  # старое в LLM не шлём
    assert len(seen["user"]) == 6 and "JSON" in seen["system"] and seen["system"].startswith("ты перекуп")


def test_draft_reply_retries_and_keeps_ceiling(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "d.db")
    lid = db.insert_if_new(conn, Listing("olx", "1", "Unitate PC i5", "https://x/1", 250, city="Suceava"))
    conn.execute("UPDATE listings SET max_offer_ron = 180 WHERE id = ?", (lid,))
    seen = []
    answers = iter([
        {"text": "Vă dau 200 de lei, plătesc în avans.", "note": "плохой", "offer_ron": 200},
        {"text": "Bună ziua! Ați lua 170 lei? Pot și prin curier, ramburs.", "note": "предложил 170", "offer_ron": 170},
    ])

    def fake_llm(model, system, user):
        seen.append((system, user))
        return next(answers)

    monkeypatch.setattr(agent, "llm_json", fake_llm)
    m = agent.draft_reply(conn, "m", lid, "reply", "Trimit prin Fan Courier, avans 50 lei")

    assert m["text"].startswith("Bună ziua") and json.loads(m["flags"]) == []
    assert "180 lei" in seen[0][0]  # потолок — моё «до», а не цена продавца
    assert "перепиши" in seen[1][1] and "payment" in seen[1][1]  # нарушения отданы модели на исправление
    seller = conn.execute("SELECT * FROM messages WHERE direction = 'seller'").fetchone()
    assert {f["rule"] for f in json.loads(seller["flags"])} == {"payment"}  # «avans» от продавца — красный флаг
    assert conn.execute("SELECT status FROM listings WHERE id = ?", (lid,)).fetchone()[0] == "negotiating"


def test_draft_reply_saves_flags_if_still_bad(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "d.db")
    lid = db.insert_if_new(conn, Listing("olx", "1", "RAM DDR4", "https://x/1", 100))
    monkeypatch.setattr(agent, "llm_json", lambda *a: {"text": "Ne vedem pe strada Unirii", "offer_ron": 150})
    m = agent.draft_reply(conn, "m", lid, "ask")
    assert {f["rule"] for f in json.loads(m["flags"])} == {"personal", "ceiling"}


def test_one_pending_draft_and_history_only_sent(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "d.db")
    lid = db.insert_if_new(conn, Listing("olx", "1", "Sursa 450W", "https://x/1", 110))
    users = []
    replies = iter(["Mai este?", "Merge?", "Rămân la 80 lei."])
    monkeypatch.setattr(agent, "llm_json", lambda m, s, u: users.append(json.loads(u)) or {"text": next(replies)})

    agent.draft_reply(conn, "m", lid, "ask")
    agent.draft_reply(conn, "m", lid, "ask")  # передумал — первый неотправленный заменяется
    assert [r["text"] for r in conn.execute("SELECT text FROM messages")] == ["Merge?"]
    assert users[1]["conversation"] == []  # неотправленное нейронка не видит

    agent.draft_reply(conn, "m", lid, "reply", "Da, 95 lei.")  # продавец ответил — «Merge?» он получил
    rows = conn.execute("SELECT direction, text, sent FROM messages ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("draft", "Merge?", 1), ("seller", "Da, 95 lei.", 0), ("draft", "Rămân la 80 lei.", 0)]
    assert [m["from"] for m in users[2]["conversation"]] == ["я", "продавец"]


def test_list_chat_keeps_only_known_ids(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "l.db")
    a = db.insert_if_new(conn, Listing("olx", "1", "HDD WD 500GB", "https://x/1", 40))
    b = db.insert_if_new(conn, Listing("olx", "2", "Kit placa de baza + i5", "https://x/2", 200))
    c = db.insert_if_new(conn, Listing("olx", "3", "Placa video RX 580", "https://x/3", 250))
    seen = {}

    def fake(model, system, user, **extra):
        seen.update(json.loads(user), effort=extra.get("reasoning_effort"))
        return {"ids": [c, b, 99999, b, "x"], "reply": "убрал HDD"}

    monkeypatch.setattr(agent, "llm_json", fake)
    out = agent.list_chat(conn, "m", "убери HDD", [a, b, c], [{"role": "user", "text": "привет"}])
    assert out == {"ids": [c, b], "search": None, "reply": "убрал HDD"}  # порядок модели, без чужих и повторов
    assert seen["effort"] == "low"
    assert {i["title"] for i in seen["items"]} == {"HDD WD 500GB", "Kit placa de baza + i5", "Placa video RX 580"}

    monkeypatch.setattr(agent, "llm_json", lambda *a, **kw: {"ids": None, "reply": "3 объявления"})
    assert agent.list_chat(conn, "m", "сколько?", [a, b, c], [])["ids"] is None


def test_search_links_existing_listing(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "s.db")
    old = db.insert_if_new(conn, LOTS[0])
    conn.execute("UPDATE listings SET status = 'sent' WHERE id = ?", (old,))
    answers = iter([{"queries": ["dell"]}, {"matches": [{"id": "olx:1", "verdict": "match", "score": 8, "why": "да"}]}])
    monkeypatch.setattr(agent, "llm_json", lambda *a, **kw: next(answers))
    monkeypatch.setattr(agent, "SOURCES", {"olx": lambda c, q, max_price=None, cheapest=False: LOTS[:1]})
    sid = agent.run_search(conn, "dell", "m", 0)
    # уже был в главном — там и остаётся, а в список поиска попадает тоже
    assert conn.execute("SELECT listing_id FROM search_items WHERE search_id = ?", (sid,)).fetchone()[0] == old
    assert conn.execute("SELECT status FROM listings WHERE id = ?", (old,)).fetchone()[0] == "sent"


def test_list_chat_turns_find_into_search(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "l.db")
    monkeypatch.setattr(agent, "llm_json", lambda *a, **kw: {"ids": [1], "search": "monitor 27 qhd до 1000", "reply": "ищу"})
    assert agent.list_chat(conn, "m", "найди монитор", [], []) == {"ids": None, "search": "monitor 27 qhd до 1000", "reply": "ищу"}


def test_search_candidates_shared_between_sources(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "c.db")
    many = [Listing("olx", str(i), f"GPU {i}", f"https://o/{i}", 100) for i in range(200)]
    few = [Listing("vinted", "v1", "GPU vinted", "https://v/1", 100)]  # «gpu» в заголовке у всех
    seen = {}
    answers = iter([{"queries": ["gpu"]}, {"matches": []}])

    def fake(model, system, user, **kw):
        if "matches" not in seen:
            seen["matches"] = True
        else:
            seen["brief"] = json.loads(user)
        return next(answers)

    monkeypatch.setattr(agent, "llm_json", fake)
    monkeypatch.setattr(agent, "SOURCES", {"olx": lambda c, q, max_price=None, cheapest=False: many,
                                           "vinted": lambda c, q, max_price=None, cheapest=False: few})
    agent.run_search(conn, "gpu", "m", 0)
    ids = [b["id"] for b in seen["brief"]]
    assert len(ids) == agent.MAX_CANDIDATES and "vinted:v1" in ids  # vinted не вытеснен OLX


def test_search_cheapest_n_without_price_cap(tmp_path, monkeypatch):
    conn = db.connect(tmp_path / "n.db")
    cpus = [Listing("olx", str(p), f"i9 9900K #{p}", f"https://o/{p}", p) for p in (1500, 900, 1200, 1100)]
    calls = []

    def fetch(c, q, max_price=None, cheapest=False):
        calls.append((max_price, cheapest))
        return cpus

    answers = iter([{"queries": ["i9 9900k"], "max_price": None, "cheapest": 2},
                    {"matches": [{"id": f"olx:{p}", "verdict": "unclear" if p == 900 else "match", "score": 8, "why": ""}
                                 for p in (1500, 900, 1200, 1100)]}])
    monkeypatch.setattr(agent, "llm_json", lambda *a, **kw: next(answers))
    monkeypatch.setattr(agent, "SOURCES", {"olx": fetch})
    sid = agent.run_search(conn, "20 самых дешёвых i9 9900K", "m", 0)
    assert calls == [(None, True), (None, False)]  # без потолка; и дешёвые, и свежие
    prices = [r[0] for r in conn.execute(
        "SELECT l.price_ron FROM search_items si JOIN listings l ON l.id = si.listing_id WHERE search_id = ? ORDER BY l.price_ron", (sid,))]
    assert prices == [1100, 1200]  # точно подходящие дешевле «уточнить» за 900


def test_text_matches_any_query():
    qs = ["i9 9900k", "procesor i9-9900K"]
    assert agent.text_matches("Procesor Intel Coffee Lake, Core i9 9900K 3.6GHz", qs)
    assert not agent.text_matches("Motor Scania DC9 9900 ore", qs)
    assert not agent.text_matches("Cooler Procesor Corsair Hydro", qs)
