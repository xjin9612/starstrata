"""從 SQLite 產生 dashboard：複製 web/ 的靜態檔，並寫入 data/ 下的 JSON。

JSON 格式見 docs/DEVELOPMENT.md「Dashboard」。
"""

from __future__ import annotations

import itertools
import json
import re
import shutil
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from starstrata.config import Config
from starstrata.db import OVERALL
from starstrata.queries import active_count, is_first_day, top_gainers
from starstrata.snapshots import RUN_FILE, day_dir
from starstrata.translate import lookup, normalize

# 日增排行的數量與爆紅警示列出的天數。
GAINERS_LIMIT = 50
ALERT_DAYS = 30
OVERALL_KEY = "all"
# 卡片走勢線涵蓋的資料日數。
SPARK_DAYS = 7

# GitHub 的 owner 與 repo 名稱只含這些字元；用 full_name 組檔案路徑前先確認。
_SEGMENT = re.compile(r"[A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class SiteStats:
    latest: date | None
    repos: int


@dataclass(frozen=True)
class _Repo:
    node_id: str
    full_name: str
    description: str | None
    language: str | None
    created_at: str | None
    first_seen: str
    state: str
    last_snapshot: str | None
    stars: int | None
    topics: tuple[str, ...]


def _write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _latest(conn: sqlite3.Connection) -> date | None:
    row = conn.execute("SELECT max(date) AS d FROM ingested_dates").fetchone()
    return date.fromisoformat(row["d"]) if row["d"] else None


def repo_path(full_name: str) -> Path | None:
    """repo 詳細檔相對於 data/repos 的路徑；名稱不是 owner/name 格式時回傳 None。"""
    parts = full_name.split("/")
    if len(parts) != 2 or not all(_SEGMENT.fullmatch(p) and p not in (".", "..") for p in parts):
        return None
    return Path(parts[0]) / f"{parts[1]}.json"


def _description_fields(translations: Mapping[str, str], description: str | None) -> dict[str, Any]:
    """description_zh 是繁體中文譯文，沒有譯文時為 None；zh_native 表示原文就是繁體中文。"""
    zh = lookup(translations, description)
    native = zh is not None and description is not None and zh == normalize(description)
    return {"description": description, "description_zh": zh, "zh_native": native}


class _Context:
    def __init__(
        self, conn: sqlite3.Connection, latest: date | None, translations: Mapping[str, str]
    ) -> None:
        self.translations = translations
        self.repos = {
            r["node_id"]: _Repo(
                node_id=r["node_id"],
                full_name=r["full_name"],
                description=r["description"],
                language=r["language"],
                created_at=r["created_at"][:10] if r["created_at"] else None,
                first_seen=r["first_seen"],
                state=r["state"],
                last_snapshot=r["last_snapshot"],
                stars=r["stars"],
                topics=tuple(json.loads(r["topics"])) if r["topics"] else (),
            )
            for r in conn.execute(
                "SELECT r.*, s.stars FROM repos r "
                "LEFT JOIN repo_daily s ON s.node_id = r.node_id AND s.date = r.last_snapshot"
            )
        }
        self.first_day = latest is None or is_first_day(conn, latest)
        day = latest.isoformat() if latest else ""
        self.new_ids = (
            set() if self.first_day else {n for n, r in self.repos.items() if r.first_seen == day}
        )
        self.hot_ids = {
            r["node_id"] for r in conn.execute("SELECT node_id FROM alerts WHERE date = ?", (day,))
        }
        self.spark_days = [
            date.fromisoformat(r["date"])
            for r in conn.execute(
                "SELECT date FROM ingested_dates WHERE date <= ? ORDER BY date DESC LIMIT ?",
                (day, SPARK_DAYS),
            )
        ][::-1]
        self.window: dict[str, dict[date, int]] = {}
        if self.spark_days:
            start = self.spark_days[0] - timedelta(days=1)
            for r in conn.execute(
                "SELECT node_id, date, stars FROM repo_daily WHERE date >= ?", (start.isoformat(),)
            ):
                self.window.setdefault(r["node_id"], {})[date.fromisoformat(r["date"])] = r["stars"]

    def spark(self, node_id: str | None) -> list[int | None] | None:
        """最近 SPARK_DAYS 個資料日的日增 star；不在 repos 表的 repo 為 None。"""
        if node_id is None or node_id not in self.repos:
            return None
        stars = self.window.get(node_id, {})
        out: list[int | None] = []
        for d in self.spark_days:
            today, before = stars.get(d), stars.get(d - timedelta(days=1))
            out.append(today - before if today is not None and before is not None else None)
        return out

    def item(
        self, rank: int, full_name: str, node_id: str | None, period_stars: int | None
    ) -> dict[str, Any]:
        repo = self.repos.get(node_id) if node_id else None
        return {
            "rank": rank,
            "full_name": repo.full_name if repo else full_name,
            "language": repo.language if repo else None,
            "period_stars": period_stars,
            "stargazers": repo.stars if repo else None,
            **_description_fields(self.translations, repo.description if repo else None),
            "created_at": repo.created_at if repo else None,
            "is_new": node_id in self.new_ids,
            "is_hot": node_id in self.hot_ids,
            "spark": self.spark(node_id),
        }


def _run_status(data_root: Path | None, latest: date | None) -> dict[str, Any]:
    """最新資料日期的 run.json 中，收集完成時間與合併後的狀態；找不到時為 None。"""
    path = day_dir(data_root, latest) / RUN_FILE if data_root and latest else None
    if path is None or not path.is_file():
        return {"collected_at": None, "run": None}
    run = json.loads(path.read_text(encoding="utf-8"))
    merged = run.get("merged")
    return {
        "collected_at": run.get("finished_at"),
        "run": None
        if merged is None
        else {
            "trending_missing": merged["trending_missing"],
            "search_ok": merged["search_ok"],
            "repos_missing": len(merged["repos_missing"]),
        },
    }


def _meta(
    conn: sqlite3.Connection,
    cfg: Config,
    ctx: _Context,
    latest: date | None,
    data_root: Path | None,
) -> dict[str, Any]:
    alerts = cfg.alerts
    return {
        "data_date": latest.isoformat() if latest else None,
        "tracked_count": active_count(conn),
        "is_first_day": ctx.first_day,
        "new_count": None if ctx.first_day else len(ctx.new_ids),
        "hot_count": None if ctx.first_day else len(ctx.hot_ids),
        "timezone": cfg.schedule.timezone.key,
        "locale": cfg.site.locale,
        "alert_rule": {
            "baseline_days": alerts.baseline_days,
            "multiplier": alerts.multiplier,
            "min_daily_stars": alerts.min_daily_stars,
            "cooldown_days": alerts.cooldown_days,
        },
        **_run_status(data_root, latest),
    }


def _trending_prev_date(conn: sqlite3.Connection, day: date) -> str | None:
    """day 之前最近一個有 Trending 資料的日期。"""
    row = conn.execute(
        "SELECT max(date) AS d FROM trending_entries WHERE date < ?", (day.isoformat(),)
    ).fetchone()
    return str(row["d"]) if row["d"] else None


def _trending(
    conn: sqlite3.Connection, cfg: Config, ctx: _Context, day: date, prev_date: str | None
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """prev_date 不是 None 時，Item 加上 prev_rank：那天同期間、同語言的名次，沒上榜為 None。"""
    prev_ranks = {
        (r["period"], r["language"], r["node_id"]): r["rank"]
        for r in conn.execute(
            "SELECT period, language, node_id, rank FROM trending_entries "
            "WHERE date = ? AND node_id IS NOT NULL",
            (prev_date,),
        )
    }
    keys = [OVERALL_KEY, *cfg.trending.languages]
    out: dict[str, dict[str, list[dict[str, Any]]]] = {
        period: {key: [] for key in keys} for period in cfg.trending.periods
    }
    rows = conn.execute(
        "SELECT period, language, rank, full_name, node_id, stars_in_period "
        "FROM trending_entries WHERE date = ? ORDER BY period, language, rank",
        (day.isoformat(),),
    )
    for r in rows:
        key = OVERALL_KEY if r["language"] == OVERALL else r["language"]
        page = out.get(r["period"], {}).get(key)
        if page is None:
            continue
        item = ctx.item(r["rank"], r["full_name"], r["node_id"], r["stars_in_period"])
        if prev_date is not None:
            item["prev_rank"] = prev_ranks.get((r["period"], r["language"], r["node_id"]))
        page.append(item)
    return out


def _new(conn: sqlite3.Connection, ctx: _Context, day: date) -> list[dict[str, Any]]:
    """新上榜依今日 Trending 新增 star 排序；只出現在 Search 的 repo 沒有今日新增，排在後面。"""
    if ctx.first_day:
        return []
    gains = {
        r["node_id"]: r["gain"]
        for r in conn.execute(
            "SELECT node_id, max(stars_in_period) AS gain FROM trending_entries "
            "WHERE date = ? AND period = 'daily' AND node_id IS NOT NULL GROUP BY node_id",
            (day.isoformat(),),
        )
    }
    ids = sorted(
        ctx.new_ids,
        key=lambda n: (
            gains.get(n) is None,
            -(gains.get(n) or 0),
            -(ctx.repos[n].stars or 0),
            ctx.repos[n].full_name,
        ),
    )
    return [
        ctx.item(i, ctx.repos[n].full_name, n, gains.get(n)) for i, n in enumerate(ids, start=1)
    ]


def _alerts(conn: sqlite3.Connection, ctx: _Context, day: date) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT date, node_id, daily_stars, baseline FROM alerts WHERE date > ? "
        "ORDER BY date DESC, daily_stars DESC",
        ((day - timedelta(days=ALERT_DAYS)).isoformat(),),
    )
    return [
        {
            "date": r["date"],
            "full_name": ctx.repos[r["node_id"]].full_name,
            "added": r["daily_stars"],
            "baseline_7d": None if r["baseline"] is None else round(r["baseline"]),
        }
        for r in rows
        if r["node_id"] in ctx.repos
    ]


def _search_order(ctx: _Context) -> list[_Repo]:
    return sorted(ctx.repos.values(), key=lambda r: (-(r.stars or 0), r.full_name))


def _search_index(ctx: _Context) -> list[list[Any]]:
    return [[r.full_name, r.language, r.stars or 0] for r in _search_order(ctx)]


def _search_full(ctx: _Context) -> list[list[Any]]:
    """與 search-index 同順序的 [full_name, 原文說明, 譯文, topics]。

    沒有譯文，或原文已是目標語言時，譯文為 None。
    """
    rows: list[list[Any]] = []
    for r in _search_order(ctx):
        fields = _description_fields(ctx.translations, r.description)
        translated = None if fields["zh_native"] else fields["description_zh"]
        rows.append([r.full_name, r.description, translated, list(r.topics)])
    return rows


def _page_owners(ctx: _Context) -> dict[str, str]:
    """詳細檔路徑（不分大小寫）→ 使用該路徑的 node_id。

    改名或刪除後重建的 repo 可能與舊紀錄同名；優先不是 gone、最近有快照的。
    """
    owners: dict[str, str] = {}
    ranked = sorted(
        ctx.repos.values(),
        key=lambda r: (r.state == "gone", r.last_snapshot is None, r.last_snapshot or ""),
    )
    for r in reversed(ranked):
        path = repo_path(r.full_name)
        if path is not None:
            owners[str(path).lower()] = r.node_id
    return owners


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
    data_root: Path | None = None,
) -> SiteStats:
    """清空 out_dir 後重新產生整個網站。

    translations 是說明的繁體中文譯文，見 translate.load。data_root 是快照根目錄，
    用來讀最新資料日期的 run.json；為 None 時 collected_at 與 run 都是 null。
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(web_dir, out_dir)
    data = out_dir / "data"
    latest = _latest(conn)
    ctx = _Context(conn, latest, translations or {})

    meta = _meta(conn, cfg, ctx, latest, data_root) | {
        "generated_at": generated_at.isoformat(timespec="seconds")
    }
    trending: dict[str, Any] = {p: {} for p in cfg.trending.periods}
    new: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []
    prev_date = _trending_prev_date(conn, latest) if latest else None
    meta["trending_prev_date"] = prev_date
    if latest:
        trending = _trending(conn, cfg, ctx, latest, prev_date)
        new = _new(conn, ctx, latest)
        daily = [
            ctx.item(i, r.full_name, r.node_id, r.delta)
            for i, r in enumerate(top_gainers(conn, latest, GAINERS_LIMIT), start=1)
        ]
        alerts = _alerts(conn, ctx, latest)
    _write(data / "meta.json", meta)
    _write(data / "trending.json", trending)
    _write(data / "new.json", {"items": new})
    _write(data / "daily.json", {"items": daily})
    _write(data / "alerts.json", {"items": alerts})
    _write(data / "search-index.json", _search_index(ctx))
    _write(data / "search-full.json", _search_full(ctx))

    owners = _page_owners(ctx)
    snapshots = _Groups(conn, "SELECT node_id, date, stars FROM repo_daily ORDER BY node_id, date")
    backfill = _Groups(
        conn, "SELECT node_id, date, stars_added FROM star_backfill ORDER BY node_id, date"
    )
    trending_rows = _Groups(
        conn,
        "SELECT node_id, date, period, language, rank FROM trending_entries "
        "WHERE node_id IS NOT NULL ORDER BY node_id, date DESC, language, period",
    )
    count = 0
    for r in conn.execute("SELECT * FROM repos ORDER BY node_id"):
        node_id = r["node_id"]
        points, daily_series = repo_series(
            [(s["date"], s["stars"]) for s in snapshots.take(node_id)],
            [(b["date"], b["stars_added"]) for b in backfill.take(node_id)],
        )
        history = trending_rows.take(node_id)
        path = repo_path(r["full_name"])
        if path is None or owners.get(str(path).lower()) != node_id:
            continue
        repo = ctx.repos[node_id]
        _write(
            data / "repos" / path,
            {
                "full_name": r["full_name"],
                **_description_fields(ctx.translations, r["description"]),
                "topics": json.loads(r["topics"]) if r["topics"] else [],
                "language": r["language"],
                "archived": bool(r["archived"]),
                "created_at": repo.created_at,
                "first_seen": r["first_seen"],
                "state": r["state"],
                "stargazers": repo.stars,
                "points": points,
                "daily": daily_series,
                "trending": [
                    [t["date"], t["period"], t["language"] or None, t["rank"]] for t in history
                ],
            },
        )
        count += 1
    return SiteStats(latest=latest, repos=count)
