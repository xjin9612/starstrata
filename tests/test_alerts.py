from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from starstrata import alerts
from starstrata.config import AlertsConfig, load_config
from starstrata.db import create_schema

CFG = AlertsConfig(baseline_days=3, multiplier=2.0, min_daily_stars=100, cooldown_days=3)
DAY = date(2026, 11, 22)
REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


def _series(*deltas: int, start: int = 1000) -> dict[date, int]:
    """deltas 依時間順序排列，最後一個是 DAY 的日增量。"""
    series = {DAY - timedelta(days=len(deltas)): start}
    stars = start
    for i, delta in enumerate(deltas):
        stars += delta
        series[DAY - timedelta(days=len(deltas) - 1 - i)] = stars
    return series


def test_alert_when_both_thresholds_met() -> None:
    assert alerts.evaluate(CFG, DAY, _series(40, 50, 60, 100)) == (100, 50.0)


def test_absolute_minimum_boundary() -> None:
    assert alerts.evaluate(CFG, DAY, _series(10, 10, 10, 100)) is not None
    assert alerts.evaluate(CFG, DAY, _series(10, 10, 10, 99)) is None


def test_multiplier_boundary() -> None:
    assert alerts.evaluate(CFG, DAY, _series(100, 100, 100, 200)) == (200, 100.0)
    assert alerts.evaluate(CFG, DAY, _series(100, 100, 100, 199)) is None


def test_insufficient_baseline_uses_minimum_only() -> None:
    assert alerts.evaluate(CFG, DAY, _series(500, 100)) == (100, None)


def test_gap_in_baseline_is_spread_evenly() -> None:
    series = _series(10, 10, 10, 100)
    del series[DAY - timedelta(days=2)]
    assert alerts.evaluate(CFG, DAY, series) == (100, 10.0)
    series = _series(500, 500, 500, 150)
    del series[DAY - timedelta(days=2)]
    assert alerts.evaluate(CFG, DAY, series) is None


def test_baseline_starts_from_latest_snapshot_at_period_start() -> None:
    # 期間開頭（DAY-4）有快照時從它算起，不用更早的 DAY-5。
    assert alerts.evaluate(CFG, DAY, _series(100, 10, 10, 10, 170)) == (170, 10.0)


def test_gap_at_baseline_start_uses_earlier_snapshot() -> None:
    series = _series(30, 10, 10, 10, 100)
    del series[DAY - timedelta(days=4)]
    # 期間開頭（DAY-4）沒有快照，改從 DAY-5 算起：(10 + 30 + 10 + 10) / 4。
    assert alerts.evaluate(CFG, DAY, series) == (100, 15.0)


def _history(first: date, backfill: dict[date, int] | None) -> alerts.History:
    return alerts.History(first_snapshot=first, backfill=backfill)


def test_backfill_fills_days_before_first_snapshot() -> None:
    series = _series(40, 100)
    first = DAY - timedelta(days=2)
    backfill = {
        first - timedelta(days=1): 1000,
        first - timedelta(days=2): 20,
        first - timedelta(days=3): 30,
        first - timedelta(days=4): 1000,
    }
    # 快照 1 天（40）＋首筆快照前 2、3 天的回補（20、30）。
    # 前 1 天可能還沒結束，前 4 天超出期間，都不用。
    got = alerts.evaluate(CFG, DAY, series, _history(first, backfill))
    assert got == (100, 30.0)


def test_backfill_days_without_rows_count_as_zero() -> None:
    first = DAY - timedelta(days=1)
    series = {first: 1000, DAY: 1100}
    assert alerts.evaluate(CFG, DAY, series, _history(first, {})) == (100, 0.0)


def test_without_backfill_baseline_is_unknown() -> None:
    first = DAY - timedelta(days=2)
    assert alerts.evaluate(CFG, DAY, _series(40, 100), _history(first, None)) == (100, None)


def test_backfill_not_used_when_series_misses_first_snapshot() -> None:
    first = DAY - timedelta(days=30)
    got = alerts.evaluate(CFG, DAY, _series(40, 100), _history(first, {first: 0}))
    assert got == (100, None)


def test_missing_previous_day_gives_no_alert() -> None:
    series = _series(10, 10, 10, 500)
    del series[DAY - timedelta(days=1)]
    assert alerts.evaluate(CFG, DAY, series) is None


@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    create_schema(c, load_config(REPO_CONFIG))
    return c


@pytest.mark.parametrize(("days_since", "expected"), [(2, False), (3, True)])
def test_cooldown_boundary(conn: sqlite3.Connection, days_since: int, expected: bool) -> None:
    previous = (DAY - timedelta(days=days_since)).isoformat()
    conn.execute("INSERT INTO alerts VALUES ('R_a', ?, 500, NULL)", (previous,))
    found = alerts.detect(conn, CFG, DAY, {"R_a": _series(10, 10, 10, 500)})
    assert bool(found) is expected


def test_zero_cooldown_allows_consecutive_alerts(conn: sqlite3.Connection) -> None:
    cfg = AlertsConfig(baseline_days=3, multiplier=2.0, min_daily_stars=100, cooldown_days=0)
    conn.execute(
        "INSERT INTO alerts VALUES ('R_a', ?, 500, NULL)", ((DAY - timedelta(days=1)).isoformat(),)
    )
    assert alerts.detect(conn, cfg, DAY, {"R_a": _series(10, 10, 10, 500)})


def test_detect_replaces_alerts_of_the_day(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO alerts VALUES ('R_old', ?, 500, NULL)", (DAY.isoformat(),))
    alerts.detect(conn, CFG, DAY, {"R_a": _series(10, 10, 10, 500)})
    assert [r["node_id"] for r in conn.execute("SELECT node_id FROM alerts")] == ["R_a"]


def test_detect_reads_backfill_from_db(conn: sqlite3.Connection) -> None:
    first = DAY - timedelta(days=1)
    conn.execute("INSERT INTO repo_daily VALUES ('R_a', ?, 1000, 0, 0, 0)", (first.isoformat(),))
    conn.execute("INSERT INTO backfilled VALUES ('R_a', 'h')")
    for k in range(2, 5):
        conn.execute(
            "INSERT INTO star_backfill VALUES ('R_a', ?, 100)",
            ((first - timedelta(days=k)).isoformat(),),
        )
    assert alerts.detect(conn, CFG, DAY, {"R_a": {first: 1000, DAY: 1150}}) == []


def test_detect_records_alert(conn: sqlite3.Connection) -> None:
    alerts.detect(conn, CFG, DAY, {"R_a": _series(10, 10, 10, 500), "R_b": _series(1, 1, 1, 1)})
    rows = [tuple(r) for r in conn.execute("SELECT * FROM alerts")]
    assert rows == [("R_a", DAY.isoformat(), 500, 10.0)]
