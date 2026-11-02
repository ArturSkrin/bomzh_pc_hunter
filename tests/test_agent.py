import json

from baraholka import agent, db
from baraholka.models import Listing

LOTS = [
    Listing("olx", "1", "Dell U2722D 27 QHD", "https://x/1", 920, photos=["https://img/1;s=800x600"]),
    Listing("olx", "2", "Samsung Odyssey G5 27 curbat", "https://x/2", 800),
    Listing("olx", "3", "Monitor 24 FHD", "https://x/3", 300),
]


def test_run_search(tmp_path, monkeypatch):
    calls = []

    def fake_fetch(client, query, max_price=None):
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
    monkeypatch.setattr(agent, "llm_json", lambda *a: next(answers))
    monkeypatch.setattr(agent, "SOURCES", {"olx": fake_fetch})

    conn = db.connect(tmp_path / "a.db")
    sid = agent.run_search(conn, "монитор 27 QHD с регулировкой высоты до 1000", "m", 0, 200)

    # «1000 lei» не число — берём потолок из настроек, но поиск не падает
    assert calls == [("monitor 27 qhd", 200), ("dell u2722d", 200)]
    row = conn.execute("SELECT * FROM searches WHERE id = ?", (sid,)).fetchone()
    results = json.loads(row["results"])
    assert [r["url"] for r in results] == ["https://x/1", "https://x/2"]  # match выше unclear, выдуманный id отброшен
    assert results[1]["score"] == 10  # оценка зажата в 0–10
    assert results[0]["thumb"] == "https://img/1;s=200x150"
    assert row["candidates"] == 3
    # Найденное через поиск не должно улететь в Telegram как «новое»
    assert {r[0] for r in conn.execute("SELECT status FROM listings")} == {"search"}


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
