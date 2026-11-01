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
