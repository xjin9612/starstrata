"""追蹤狀態轉換與每日需要快照的 repo。規則見 docs/DESIGN.md「追蹤狀態」。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from datetime import date, timedelta

from starstrata.config import TrackingConfig
from starstrata.db import ACTIVE, DORMANT, GONE


def appear(conn: sqlite3.Connection, day: date, appeared: Mapping[str, str]) -> None:
    """出現的 repo 一律轉為 active。

    appeared 是當天出現在 Trending 或 Search 的 node_id → full_name。
    """
    d = day.isoformat()
    for node_id, full_name in appeared.items():
        conn.execute(
            "INSERT INTO repos (node_id, full_name, first_seen, state, state_since) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT (node_id) DO NOTHING",
            (node_id, full_name, d, ACTIVE, d),
        )
        conn.execute(
            "UPDATE repos SET state = ?, state_since = ? WHERE node_id = ? AND state != ?",
            (ACTIVE, d, node_id, ACTIVE),
        )


def mark_gone(conn: sqlite3.Connection, day: date, node_ids: Iterable[str]) -> None:
    d = day.isoformat()
    for node_id in node_ids:
        conn.execute(
            "UPDATE repos SET state = ?, state_since = ? WHERE node_id = ? AND state != ?",
            (GONE, d, node_id, GONE),
        )


def apply_transitions(
    conn: sqlite3.Connection,
    cfg: TrackingConfig,
    day: date,
    stars: Mapping[str, Mapping[date, int]],
    appeared: Iterable[str],
) -> None:
    """依當天快照更新 active 與 dormant 之間的轉換。

    stars 是當天有快照的 repo → {日期: star 數}，至少涵蓋 day 往前 dormant_after_days 天。
    當天出現在 Trending 或 Search 的 repo 不做降頻判斷。
    """
    d = day.isoformat()
    skip = set(appeared)
    states = {
        r["node_id"]: r["state"]
        for r in conn.execute(
            "SELECT r.node_id, r.state FROM repos r "
            "JOIN repo_daily s ON s.node_id = r.node_id AND s.date = ?",
            (d,),
        )
    }
    for node_id, state in states.items():
        if node_id in skip:
            continue
        series = stars.get(node_id, {})
        if state == ACTIVE and _should_go_dormant(cfg, day, series):
            conn.execute(
                "UPDATE repos SET state = ?, state_since = ? WHERE node_id = ?",
                (DORMANT, d, node_id),
            )
        elif state == DORMANT and _should_reactivate(conn, cfg, day, node_id, series):
            conn.execute(
                "UPDATE repos SET state = ?, state_since = ? WHERE node_id = ?",
                (ACTIVE, d, node_id),
            )


def _should_go_dormant(cfg: TrackingConfig, day: date, series: Mapping[date, int]) -> bool:
    """最近 dormant_after_days 個日增量都存在（快照逐日相鄰），且都低於門檻。"""
    for k in range(cfg.dormant_after_days):
        today, before = day - timedelta(days=k), day - timedelta(days=k + 1)
        if today not in series or before not in series:
            return False
        if series[today] - series[before] >= cfg.dormant_daily_stars_below:
            return False
    return True


def _should_reactivate(
    conn: sqlite3.Connection,
    cfg: TrackingConfig,
    day: date,
    node_id: str,
    series: Mapping[date, int],
) -> bool:
    row = conn.execute(
        "SELECT stars FROM repo_daily WHERE node_id = ? AND date < ? ORDER BY date DESC LIMIT 1",
        (node_id, day.isoformat()),
    ).fetchone()
    if row is None or day not in series:
        return False
    return series[day] - int(row["stars"]) >= cfg.reactivate_weekly_stars


def due_ids(conn: sqlite3.Connection, cfg: TrackingConfig, day: date) -> list[str]:
    """day 需要快照的 repo。

    包含全部 active，以及距上次快照達 dormant_snapshot_interval_days 天的 dormant。
    """
    cutoff = (day - timedelta(days=cfg.dormant_snapshot_interval_days)).isoformat()
    rows = conn.execute(
        "SELECT node_id FROM repos WHERE state = ? "
        "OR (state = ? AND (last_snapshot IS NULL OR last_snapshot <= ?)) ORDER BY node_id",
        (ACTIVE, DORMANT, cutoff),
    )
    return [r["node_id"] for r in rows]
