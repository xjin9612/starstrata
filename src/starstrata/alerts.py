"""爆紅判定。規則見 docs/DEVELOPMENT.md「爆紅判定」。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from starstrata.config import AlertsConfig

# 回補資料只用首筆快照前 BACKFILL_LAG 天以前的日子。這些 UTC 日在任何時區的首筆快照當天都已結束。
BACKFILL_LAG = 2


@dataclass(frozen=True)
class Alert:
    node_id: str
    daily_stars: int
    baseline: float | None


@dataclass(frozen=True)
class History:
    """單一 repo 在快照之外的資料：首筆快照日期，以及回補的每日新增 star。

    backfill 為 None 表示還沒有回補；有回補時，沒有列出的日子是 0。
    """

    first_snapshot: date
    backfill: Mapping[date, int] | None


def _delta(series: Mapping[date, int], day: date) -> int | None:
    before = day - timedelta(days=1)
    if day in series and before in series:
        return series[day] - series[before]
    return None


def baseline(
    cfg: AlertsConfig, day: date, series: Mapping[date, int], history: History | None
) -> float | None:
    """day 前 baseline_days 天的平均日增 star；資料不足時為 None。

    series 的範圍由呼叫端決定，ingest 傳入 day 往前 2 × baseline_days + 1 天。
    期間開頭缺快照時，取 series 中期間開頭以前最近的一筆。
    """
    last = day - timedelta(days=1)
    start = last - timedelta(days=cfg.baseline_days)
    if last not in series:
        return None
    earlier = [d for d in series if d <= start]
    if earlier:
        anchor = max(earlier)
        return (series[last] - series[anchor]) / (last - anchor).days
    if history is None or history.backfill is None:
        return None
    anchor = min(series)
    if anchor != history.first_snapshot:
        return None
    span = (last - anchor).days
    cutoff = anchor - timedelta(days=BACKFILL_LAG)
    filled = sum(
        history.backfill.get(cutoff - timedelta(days=k), 0) for k in range(cfg.baseline_days - span)
    )
    return (series[last] - series[anchor] + filled) / cfg.baseline_days


def evaluate(
    cfg: AlertsConfig, day: date, series: Mapping[date, int], history: History | None = None
) -> tuple[int, float | None] | None:
    """符合警示條件時回傳 (日增 star, 基準)，基準資料不足時基準為 None。"""
    today = _delta(series, day)
    if today is None or today < cfg.min_daily_stars:
        return None
    base = baseline(cfg, day, series, history)
    if base is not None and today < cfg.multiplier * base:
        return None
    return today, base


def load_history(conn: sqlite3.Connection, node_ids: Iterable[str]) -> dict[str, History]:
    """node_ids 的首筆快照日期與回補資料。"""
    result: dict[str, History] = {}
    for node_id in node_ids:
        first = conn.execute(
            "SELECT MIN(date) FROM repo_daily WHERE node_id = ?", (node_id,)
        ).fetchone()[0]
        if first is None:
            continue
        done = conn.execute("SELECT 1 FROM backfilled WHERE node_id = ?", (node_id,)).fetchone()
        backfill = (
            {
                date.fromisoformat(r["date"]): int(r["stars_added"])
                for r in conn.execute(
                    "SELECT date, stars_added FROM star_backfill WHERE node_id = ?", (node_id,)
                )
            }
            if done
            else None
        )
        result[node_id] = History(date.fromisoformat(first), backfill)
    return result


def detect(
    conn: sqlite3.Connection,
    cfg: AlertsConfig,
    day: date,
    stars: Mapping[str, Mapping[date, int]],
) -> list[Alert]:
    """重新判定並寫入 day 的警示，取代 day 原有的警示。冷卻期內已有警示的 repo 略過。"""
    d = day.isoformat()
    candidates = [
        n
        for n, series in stars.items()
        if (delta := _delta(series, day)) is not None and delta >= cfg.min_daily_stars
    ]
    known = load_history(conn, candidates)
    cooldown_start = (day - timedelta(days=cfg.cooldown_days - 1)).isoformat()
    conn.execute("DELETE FROM alerts WHERE date = ?", (d,))
    alerts: list[Alert] = []
    for node_id in candidates:
        result = evaluate(cfg, day, stars[node_id], known.get(node_id))
        if result is None:
            continue
        if cfg.cooldown_days > 0:
            recent = conn.execute(
                "SELECT 1 FROM alerts WHERE node_id = ? AND date >= ? AND date < ?",
                (node_id, cooldown_start, d),
            ).fetchone()
            if recent is not None:
                continue
        alert = Alert(node_id=node_id, daily_stars=result[0], baseline=result[1])
        conn.execute(
            "INSERT INTO alerts (node_id, date, daily_stars, baseline) VALUES (?, ?, ?, ?)",
            (node_id, d, alert.daily_stars, alert.baseline),
        )
        alerts.append(alert)
    return alerts
