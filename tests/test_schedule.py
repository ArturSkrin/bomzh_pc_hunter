from datetime import datetime, time

from baraholka import __main__ as m

TIMES = [time(9), time(14), time(20)]


def test_next_run(monkeypatch):
    monkeypatch.setattr(m.random, "uniform", lambda a, b: 0)
    assert m.next_run(TIMES, datetime(2026, 11, 1, 10, 30)) == datetime(2026, 11, 1, 14, 0)
    assert m.next_run(TIMES, datetime(2026, 11, 1, 21, 0)) == datetime(2026, 11, 2, 9, 0)  # сегодня всё прошло
    assert m.next_run(TIMES, datetime(2026, 11, 1, 14, 0)) == datetime(2026, 11, 1, 20, 0)  # ровно сейчас — не повторяем


def test_jitter_stays_within_10_min():
    for _ in range(200):
        at = m.next_run(TIMES, datetime(2026, 11, 1, 10, 30))
        assert abs((at - datetime(2026, 11, 1, 14, 0)).total_seconds()) <= 600


def test_collect_skips_blocked_source_and_keeps_pause(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from baraholka import db
    from baraholka.__main__ import collect_once
    import baraholka.__main__ as main_mod

    conn = db.connect(tmp_path / "c.db")
    calls, sleeps = [], []
    monkeypatch.setattr(main_mod.time, "sleep", lambda s: sleeps.append(s))

    def blocked(client, query, **kw):
        calls.append(("olx", query))
        raise RuntimeError("OLX ответил 403 — блок")

    def ok(client, query, **kw):
        calls.append(("vinted", query))
        return []

    monkeypatch.setattr(main_mod, "SOURCES", {"olx": blocked, "vinted": ok})
    collect_once(SimpleNamespace(request_delay_sec=3), conn)
    queries = db.enabled_queries(conn)
    assert [c for c in calls if c[0] == "olx"] == [("olx", q) for q in queries[:2]]  # после 2 ошибок — стоп
    assert len([c for c in calls if c[0] == "vinted"]) == len(queries)  # другие площадки не страдают
    assert sleeps[0] == 3  # после первой ошибки пауза, а не следующий запрос сразу
