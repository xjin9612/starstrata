"""dashboard 與通知共用的 SQLite 查詢。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta

from starstrata.db import OVERALL


@dataclass(frozen=True)
class RepoLine:
    node_id: str
    full_name: str
    description: str | None
    language: str | None
    stars: int | None
    delta: int | None = None


@dataclass(frozen=True)
class AlertLine:
    repo: RepoLine
    daily_stars: int
    baseline: float | None


def _repo(row: sqlite3.Row, *, delta: int | None = None) -> RepoLine:
    return RepoLine(
        node_id=row["node_id"],
        full_name=row["full_name"],
        description=row["description"],
        language=row["language"],
        stars=row["stars"],
        delta=delta,
    )


def new_repos(conn: sqlite3.Connection, day: date) -> list[RepoLine]:
    rows = conn.execute(
        "SELECT r.node_id, r.full_name, r.description, r.language, s.stars FROM repos r "
        "LEFT JOIN repo_daily s ON s.node_id = r.node_id AND s.date = r.first_seen "
        "WHERE r.first_seen = ? ORDER BY coalesce(s.stars, 0) DESC, r.full_name",
        (day.isoformat(),),
    )
    return [_repo(r) for r in rows]


def top_gainers(conn: sqlite3.Connection, day: date, limit: int) -> list[RepoLine]:
    rows = conn.execute(
        "SELECT r.node_id, r.full_name, r.description, r.language, t.stars, "
        "t.stars - y.stars AS delta FROM repo_daily t "
        "JOIN repo_daily y ON y.node_id = t.node_id AND y.date = ? "
        "JOIN repos r ON r.node_id = t.node_id "
        "WHERE t.date = ? AND t.stars > y.stars ORDER BY delta DESC, r.full_name LIMIT ?",
        ((day - timedelta(days=1)).isoformat(), day.isoformat(), limit),
    )
    return [_repo(r, delta=int(r["delta"])) for r in rows]


@dataclass(frozen=True)
class TrendingLine:
    rank: int
    full_name: str
    # 當天查不到 repo 時為 None，此時 description、language、stars 也是 None。
    node_id: str | None
    gain: int | None
    stars: int | None
    language: str | None
    description: str | None


def trending_top(conn: sqlite3.Connection, day: date, limit: int) -> list[TrendingLine]:
    """當天總榜今日 Trending，依今日新增 star 由多到少；沒有新增數的排最後。"""
    rows = conn.execute(
        "SELECT t.rank, t.full_name, t.node_id, t.stars_in_period, s.stars, r.language, "
        "r.description FROM trending_entries t "
        "LEFT JOIN repos r ON r.node_id = t.node_id "
        "LEFT JOIN repo_daily s ON s.node_id = t.node_id AND s.date = t.date "
        "WHERE t.date = ? AND t.period = 'daily' AND t.language = ? "
        "ORDER BY t.stars_in_period IS NULL, t.stars_in_period DESC, t.rank LIMIT ?",
        (day.isoformat(), OVERALL, limit),
    )
    return [
        TrendingLine(
            rank=r["rank"],
            full_name=r["full_name"],
            node_id=r["node_id"],
            gain=r["stars_in_period"],
            stars=r["stars"],
            language=r["language"],
            description=r["description"],
        )
        for r in rows
    ]


def active_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT count(*) FROM repos WHERE state = 'active'").fetchone()[0])


def is_first_day(conn: sqlite3.Connection, day: date) -> bool:
    """day 之前沒有任何已匯入的日期。"""
    row = conn.execute("SELECT 1 FROM ingested_dates WHERE date < ? LIMIT 1", (day.isoformat(),))
    return row.fetchone() is None


def day_alerts(conn: sqlite3.Connection, day: date) -> list[AlertLine]:
    rows = conn.execute(
        "SELECT a.node_id, a.daily_stars, a.baseline, r.full_name, r.description, r.language, "
        "s.stars FROM alerts a JOIN repos r ON r.node_id = a.node_id "
        "LEFT JOIN repo_daily s ON s.node_id = a.node_id AND s.date = a.date "
        "WHERE a.date = ? ORDER BY a.daily_stars DESC, r.full_name",
        (day.isoformat(),),
    )
    return [AlertLine(_repo(r), int(r["daily_stars"]), r["baseline"]) for r in rows]


def is_ingested(conn: sqlite3.Connection, day: date) -> bool:
    row = conn.execute("SELECT 1 FROM ingested_dates WHERE date = ?", (day.isoformat(),))
    return row.fetchone() is not None
