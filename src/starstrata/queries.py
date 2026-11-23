"""dashboard 與通知共用的 SQLite 查詢。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, timedelta


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
