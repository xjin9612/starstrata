"""從 SQLite 產生 dashboard：複製 web/ 的靜態檔，並寫入 data/ 下的 JSON。"""

from __future__ import annotations

import itertools
import json
import shutil
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from starstrata.config import Config
from starstrata.db import OVERALL
from starstrata.queries import RepoLine, new_repos, top_gainers
from starstrata.snapshots import node_file_stem
from starstrata.translate import lookup, normalize

# 首頁列出的日增排行數量與爆紅警示天數。
GAINERS_LIMIT = 50
ALERT_DAYS = 30


@dataclass(frozen=True)
class SiteStats:
    latest: date | None
    repos: int


def _write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _latest(conn: sqlite3.Connection) -> date | None:
    row = conn.execute("SELECT max(date) AS d FROM ingested_dates").fetchone()
    return date.fromisoformat(row["d"]) if row["d"] else None


def _zh(translations: Mapping[str, str], description: str | None) -> str | None:
    """說明的繁體中文譯文；沒有譯文或譯文與原文相同時回傳 None，前端直接顯示原文。"""
    text = lookup(translations, description)
    if text is None or description is None or text == normalize(description):
        return None
    return text


def _repo_line(r: RepoLine, translations: Mapping[str, str]) -> dict[str, Any]:
    return {
        "node_id": r.node_id,
        "full_name": r.full_name,
        "description": r.description,
        "description_zh": _zh(translations, r.description),
        "language": r.language,
        "stars": r.stars,
        "delta": r.delta,
    }


def _trending(
    conn: sqlite3.Connection, cfg: Config, day: date, translations: Mapping[str, str]
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT t.period, t.language, t.rank, t.full_name, t.node_id, t.stars_in_period, "
        "r.description, r.language AS repo_language, s.stars FROM trending_entries t "
        "LEFT JOIN repos r ON r.node_id = t.node_id "
        "LEFT JOIN repo_daily s ON s.node_id = t.node_id AND s.date = t.date "
        "WHERE t.date = ? ORDER BY t.language, t.period, t.rank",
        (day.isoformat(),),
    )
    pages: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in rows:
        pages.setdefault((r["language"], r["period"]), []).append(
            {
                "rank": r["rank"],
                "full_name": r["full_name"],
                "node_id": r["node_id"],
                "stars_in_period": r["stars_in_period"],
                "stars": r["stars"],
                "description": r["description"],
                "description_zh": _zh(translations, r["description"]),
                "language": r["repo_language"],
            }
        )
    order = [OVERALL, *cfg.trending.languages]
    keys = sorted(
        pages,
        key=lambda k: (
            order.index(k[0]) if k[0] in order else len(order),
            cfg.trending.periods.index(k[1]) if k[1] in cfg.trending.periods else 99,
        ),
    )
    return [
        {"language": lang or None, "period": period, "entries": pages[(lang, period)]}
        for lang, period in keys
    ]


def _alerts(
    conn: sqlite3.Connection, day: date, translations: Mapping[str, str]
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT a.date, a.node_id, a.daily_stars, a.baseline, r.full_name, r.description "
        "FROM alerts a JOIN repos r ON r.node_id = a.node_id "
        "WHERE a.date > ? ORDER BY a.date DESC, a.daily_stars DESC",
        ((day - timedelta(days=ALERT_DAYS)).isoformat(),),
    )
    return [dict(r) | {"description_zh": _zh(translations, r["description"])} for r in rows]


def _index(conn: sqlite3.Connection) -> list[list[Any]]:
    rows = conn.execute(
        "SELECT r.node_id, r.full_name, r.language, r.state, s.stars FROM repos r "
        "LEFT JOIN repo_daily s ON s.node_id = r.node_id AND s.date = r.last_snapshot "
        "ORDER BY coalesce(s.stars, 0) DESC, r.full_name"
    )
    return [[r["node_id"], r["full_name"], r["language"], r["state"], r["stars"]] for r in rows]


class _Groups:
    """依 node_id 排序的查詢結果，按 node_id 遞增順序逐個取出，不一次讀進記憶體。"""

    def __init__(self, conn: sqlite3.Connection, sql: str) -> None:
        self._groups = itertools.groupby(conn.execute(sql), key=lambda r: str(r["node_id"]))
        self._current = next(self._groups, None)

    def take(self, node_id: str) -> list[sqlite3.Row]:
        """node_id 必須以遞增順序傳入。"""
        while self._current is not None and self._current[0] < node_id:
            self._current = next(self._groups, None)
        if self._current is None or self._current[0] != node_id:
            return []
        rows = list(self._current[1])
        self._current = next(self._groups, None)
        return rows


def repo_series(
    snapshots: list[tuple[str, int]], backfill: list[tuple[str, int]]
) -> tuple[list[list[Any]], list[list[Any]]]:
    """合併回補與快照，回傳 (star 累計點, 每日新增 star)。

    第一筆快照之前用回補資料，之後用快照。每個點附來源：b 為回補，s 為快照。
    每日新增只在快照前一天也有快照時才計算。
    """
    first_snapshot = snapshots[0][0] if snapshots else None
    points: list[list[Any]] = []
    daily: list[list[Any]] = []
    total = 0
    for d, added in backfill:
        total += added
        if first_snapshot is None or d < first_snapshot:
            points.append([d, total, "b"])
            daily.append([d, added, "b"])
    previous: tuple[date, int] | None = None
    for d, stars in snapshots:
        points.append([d, stars, "s"])
        current = date.fromisoformat(d)
        if previous is not None and current - previous[0] == timedelta(days=1):
            daily.append([d, stars - previous[1], "s"])
        previous = (current, stars)
    return points, daily


def build_site(
    conn: sqlite3.Connection,
    cfg: Config,
    out_dir: Path,
    web_dir: Path,
    generated_at: datetime,
    translations: Mapping[str, str] | None = None,
) -> SiteStats:
    """清空 out_dir 後重新產生整個網站。translations 是說明的繁體中文譯文，見 translate.load。"""
    translations = translations or {}
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(web_dir, out_dir)
    data = out_dir / "data"
    latest = _latest(conn)

    summary: dict[str, Any] = {
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "date": latest.isoformat() if latest else None,
        "languages": list(cfg.trending.languages),
        "periods": list(cfg.trending.periods),
        "trending": [],
        "new_repos": [],
        "gainers": [],
        "alerts": [],
    }
    if latest:
        summary["trending"] = _trending(conn, cfg, latest, translations)
        summary["new_repos"] = [_repo_line(r, translations) for r in new_repos(conn, latest)]
        summary["gainers"] = [
            _repo_line(r, translations) for r in top_gainers(conn, latest, GAINERS_LIMIT)
        ]
        summary["alerts"] = _alerts(conn, latest, translations)
    _write(data / "latest.json", summary)
    _write(data / "index.json", _index(conn))

    snapshots = _Groups(conn, "SELECT node_id, date, stars FROM repo_daily ORDER BY node_id, date")
    backfill = _Groups(
        conn, "SELECT node_id, date, stars_added FROM star_backfill ORDER BY node_id, date"
    )
    trending = _Groups(
        conn,
        "SELECT node_id, date, period, language, rank FROM trending_entries "
        "WHERE node_id IS NOT NULL ORDER BY node_id, date DESC, language, period",
    )
    count = 0
    for r in conn.execute("SELECT * FROM repos ORDER BY node_id"):
        node_id = r["node_id"]
        points, daily = repo_series(
            [(s["date"], s["stars"]) for s in snapshots.take(node_id)],
            [(b["date"], b["stars_added"]) for b in backfill.take(node_id)],
        )
        _write(
            data / "repos" / f"{node_file_stem(node_id)}.json",
            {
                "node_id": node_id,
                "full_name": r["full_name"],
                "description": r["description"],
                "description_zh": _zh(translations, r["description"]),
                "topics": json.loads(r["topics"]) if r["topics"] else [],
                "language": r["language"],
                "archived": bool(r["archived"]),
                "created_at": r["created_at"],
                "first_seen": r["first_seen"],
                "state": r["state"],
                "points": points,
                "daily": daily,
                "trending": [
                    [t["date"], t["period"], t["language"] or None, t["rank"]]
                    for t in trending.take(node_id)
                ],
            },
        )
        count += 1
    return SiteStats(latest=latest, repos=count)
