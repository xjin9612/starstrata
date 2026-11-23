"""爆紅判定。規則見 docs/DESIGN.md「爆紅判定」。"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta

from starstrata.config import AlertsConfig


@dataclass(frozen=True)
class Alert:
    node_id: str
    daily_stars: int
    baseline: float | None


def _delta(series: Mapping[date, int], day: date) -> int | None:
    before = day - timedelta(days=1)
    if day in series and before in series:
        return series[day] - series[before]
    return None


def evaluate(
    cfg: AlertsConfig, day: date, series: Mapping[date, int]
) -> tuple[int, float | None] | None:
    """符合警示條件時回傳 (日增 star, 基準)，基準資料不足時基準為 None。

    series 是單一 repo 的 {日期: star 數}，至少涵蓋 day 往前 baseline_days + 1 天。
    """
    today = _delta(series, day)
    if today is None or today < cfg.min_daily_stars:
        return None
    history = [_delta(series, day - timedelta(days=k)) for k in range(1, cfg.baseline_days + 1)]
    known = [h for h in history if h is not None]
    baseline = sum(known) / len(known) if len(known) == cfg.baseline_days else None
    if baseline is not None and today < cfg.multiplier * baseline:
        return None
    return today, baseline


def detect(
    conn: sqlite3.Connection,
    cfg: AlertsConfig,
    day: date,
    stars: Mapping[str, Mapping[date, int]],
) -> list[Alert]:
    """判定並寫入 day 的警示。冷卻期內已有警示的 repo 略過。"""
    d = day.isoformat()
    cooldown_start = (day - timedelta(days=cfg.cooldown_days - 1)).isoformat()
    alerts: list[Alert] = []
    for node_id, series in stars.items():
        result = evaluate(cfg, day, series)
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
            "INSERT OR REPLACE INTO alerts (node_id, date, daily_stars, baseline) "
            "VALUES (?, ?, ?, ?)",
            (node_id, d, alert.daily_stars, alert.baseline),
        )
        alerts.append(alert)
    return alerts
