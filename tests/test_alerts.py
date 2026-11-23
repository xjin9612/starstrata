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


def test_gap_in_baseline_uses_minimum_only() -> None:
    series = _series(500, 500, 500, 150)
    del series[DAY - timedelta(days=2)]
    assert alerts.evaluate(CFG, DAY, series) == (150, None)


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


def test_detect_records_alert(conn: sqlite3.Connection) -> None:
    alerts.detect(conn, CFG, DAY, {"R_a": _series(10, 10, 10, 500), "R_b": _series(1, 1, 1, 1)})
    rows = [tuple(r) for r in conn.execute("SELECT * FROM alerts")]
    assert rows == [("R_a", DAY.isoformat(), 500, 10.0)]
