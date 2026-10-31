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
