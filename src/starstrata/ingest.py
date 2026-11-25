"""把快照匯入 SQLite，並推導追蹤狀態與爆紅警示。"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from starstrata import alerts, tracking
from starstrata.backfill import BACKFILL_DIR
from starstrata.config import Config
from starstrata.db import (
    OVERALL,
    SCHEMA_VERSION,
    connect,
    create_schema,
    derivation_fingerprint,
    get_meta,
    set_meta,
)
from starstrata.github.star_history import Week, expand
from starstrata.snapshots import (
    ALERTS_FILE,
    REPOS_FILE,
    SEARCH_FILE,
    TRENDING_FILE,
    day_dir,
    day_hash,
    list_snapshot_days,
    node_id_from_stem,
    read_jsonl,
    write_jsonl,
)

log = logging.getLogger(__name__)

# meta 的鍵：警示已處理到哪一天（含）。中斷後從這一天重新處理。
ALERTS_THROUGH = "alerts_through"


@dataclass
class SyncResult:
    rebuilt: bool = False
    rebuild_reason: str | None = None
    ingested: list[date] = field(default_factory=list)


def sync(db_path: Path, cfg: Config, data_root: Path, *, force_rebuild: bool = False) -> SyncResult:
    """讓 DB 與快照一致：匯入尚未匯入的日期；DB 與快照不一致時整個重建。

    重建時先寫到暫存檔，完成後才取代 db_path，中途失敗不會留下半成品。
    """
    days = list_snapshot_days(data_root)
    hashes = {d: day_hash(day_dir(data_root, d)) for d in days}
    reason = "指定重建" if force_rebuild else _rebuild_reason(db_path, cfg, hashes)
    if reason is not None:
        log.info("重建 DB：%s", reason)
        tmp = db_path.with_name(db_path.name + ".rebuild")
        tmp.unlink(missing_ok=True)
        conn = connect(tmp)
        try:
            with conn:
                create_schema(conn, cfg)
            for d in days:
                _ingest_day(conn, cfg, data_root, d, hashes[d])
            _sync_backfill(conn, data_root)
            _settle_alerts(conn, cfg, data_root, days)
        finally:
            conn.close()
        os.replace(tmp, db_path)
        return SyncResult(rebuilt=True, rebuild_reason=reason, ingested=days)

    conn = connect(db_path)
    try:
        done = _ingested(conn)
        pending = [d for d in days if d.isoformat() not in done]
        for d in pending:
            _ingest_day(conn, cfg, data_root, d, hashes[d])
        _sync_backfill(conn, data_root)
        through = get_meta(conn, ALERTS_THROUGH)
        ingested = sorted(date.fromisoformat(d) for d in _ingested(conn))
        _settle_alerts(
            conn,
            cfg,
            data_root,
            [d for d in ingested if through is None or d.isoformat() >= through],
        )
    finally:
        conn.close()
    return SyncResult(ingested=pending)


def _sync_backfill(conn: sqlite3.Connection, data_root: Path) -> None:
    """讓 star_backfill 與 data/backfill/ 的檔案一致：新增或改變的檔案重新匯入，刪除的檔案移除。"""
    folder = data_root / BACKFILL_DIR
    files: dict[str, Path] = {}
    for path in sorted(folder.glob("*.jsonl")) if folder.is_dir() else []:
        node_id = node_id_from_stem(path.stem)
        if node_id is None:
            log.warning("略過檔名不合法的回補檔：%s", path.name)
            continue
        files[node_id] = path
    known = {r["node_id"]: r["file_hash"] for r in conn.execute("SELECT * FROM backfilled")}
    with conn:
        for node_id in known.keys() - files.keys():
            conn.execute("DELETE FROM star_backfill WHERE node_id = ?", (node_id,))
            conn.execute("DELETE FROM backfilled WHERE node_id = ?", (node_id,))
        for node_id, path in files.items():
            content = path.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if known.get(node_id) == digest:
                continue
            conn.execute("DELETE FROM star_backfill WHERE node_id = ?", (node_id,))
            for row in read_jsonl(path):
                week = Week(start=date.fromisoformat(row["week"]), days=tuple(row["days"]))
                conn.executemany(
                    "INSERT INTO star_backfill (node_id, date, stars_added) VALUES (?, ?, ?)",
                    ((node_id, d.isoformat(), n) for d, n in expand(week)),
                )
            conn.execute(
                "INSERT OR REPLACE INTO backfilled (node_id, file_hash) VALUES (?, ?)",
                (node_id, digest),
            )


def _settle_alerts(
    conn: sqlite3.Connection, cfg: Config, data_root: Path, days: list[date]
) -> None:
    """依序處理 days 的警示：已凍結的日子讀 alerts.jsonl，其他日子重新判定。

    days 的最後一天是最新資料日，判定後不凍結，之後匯入的回補資料還能用在它的基準；
    其餘日子判定後凍結。要在回補資料匯入之後執行。
    """
    # 基準期間開頭缺快照時，往前再找 baseline_days 天內最近的快照。
    window = 2 * cfg.alerts.baseline_days + 1
    for i, day in enumerate(days):
        path = day_dir(data_root, day) / ALERTS_FILE
        with conn:
            if path.is_file():
                _load_alerts(conn, day, read_jsonl(path))
            else:
                alerts.detect(conn, cfg.alerts, day, _recent_stars(conn, day, window))
                if i < len(days) - 1:
                    freeze_alerts(conn, data_root, day)
    if days:
        with conn:
            set_meta(conn, ALERTS_THROUGH, days[-1].isoformat())


def _load_alerts(conn: sqlite3.Connection, day: date, rows: list[dict[str, object]]) -> None:
    d = day.isoformat()
    conn.execute("DELETE FROM alerts WHERE date = ?", (d,))
    conn.executemany(
        "INSERT INTO alerts (node_id, date, daily_stars, baseline) VALUES (?, ?, ?, ?)",
        ((r["node_id"], d, r["daily_stars"], r["baseline"]) for r in rows),
    )


def freeze_alerts(conn: sqlite3.Connection, data_root: Path, day: date) -> None:
    """把 DB 中 day 的警示寫成 alerts.jsonl；檔案已存在或 day 尚未匯入時不做事。

    凍結後匯入與重建都讀這個檔，不再重新判定，已發出的通知與 dashboard、報告保持一致。
    呼叫端負責 commit。
    """
    folder = day_dir(data_root, day)
    path = folder / ALERTS_FILE
    d = day.isoformat()
    if path.is_file() or d not in _ingested(conn):
        return
    rows = conn.execute(
        "SELECT node_id, daily_stars, baseline FROM alerts WHERE date = ? ORDER BY node_id", (d,)
    )
    write_jsonl(path, [dict(r) for r in rows])
    conn.execute("UPDATE ingested_dates SET files_hash = ? WHERE date = ?", (day_hash(folder), d))


def _ingested(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["date"]: r["files_hash"] for r in conn.execute("SELECT * FROM ingested_dates")}


def _rebuild_reason(db_path: Path, cfg: Config, hashes: dict[date, str]) -> str | None:
    if not db_path.exists():
        return "DB 不存在"
    try:
        conn = connect(db_path)
        try:
            if get_meta(conn, "schema_version") != str(SCHEMA_VERSION):
                return "schema 版本不符"
            if get_meta(conn, "derivation_fingerprint") != derivation_fingerprint(cfg):
                return "追蹤或爆紅設定已變更"
            done = _ingested(conn)
        finally:
            conn.close()
    except sqlite3.DatabaseError as e:
        return f"DB 無法讀取：{e}"
    by_iso = {d.isoformat(): h for d, h in hashes.items()}
    for d, h in done.items():
        if by_iso.get(d) != h:
            return f"{d} 的快照已改變"
    if done and any(d not in done and d < max(done) for d in by_iso):
        return "出現比已匯入日期更早的新快照"
    return None


def _ingest_day(
    conn: sqlite3.Connection, cfg: Config, data_root: Path, day: date, files_hash: str
) -> None:
    folder = day_dir(data_root, day)
    d = day.isoformat()
    with conn:
        names: dict[str, str] = {}
        gone: list[str] = []
        if (folder / REPOS_FILE).is_file():
            for row in read_jsonl(folder / REPOS_FILE):
                if row.get("status") == "gone":
                    gone.append(row["node_id"])
                    continue
                _upsert_snapshot(conn, d, row)
                names[row["full_name"]] = row["node_id"]

        appeared: dict[str, str] = {}
        if (folder / TRENDING_FILE).is_file():
            for row in read_jsonl(folder / TRENDING_FILE):
                node_id = names.get(row["full_name"]) or _lookup(conn, row["full_name"])
                conn.execute(
                    "INSERT OR REPLACE INTO trending_entries "
                    "(date, period, language, rank, full_name, node_id, stars_in_period) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        d,
                        row["period"],
                        row["language"] or OVERALL,
                        row["rank"],
                        row["full_name"],
                        node_id,
                        row["stars_in_period"],
                    ),
                )
                if node_id:
                    appeared[node_id] = row["full_name"]
        if (folder / SEARCH_FILE).is_file():
            for row in read_jsonl(folder / SEARCH_FILE):
                conn.execute(
                    "INSERT OR REPLACE INTO search_hits (date, rank, node_id, full_name) "
                    "VALUES (?, ?, ?, ?)",
                    (d, row["rank"], row["node_id"], row["full_name"]),
                )
                appeared.setdefault(row["node_id"], row["full_name"])

        # Search 或 Trending 與 GraphQL 不一致時以 GraphQL 為準：當天 gone 的 repo 不轉回 active。
        for node_id in gone:
            appeared.pop(node_id, None)
        tracking.mark_gone(conn, day, gone)
        tracking.appear(conn, day, appeared)

        stars = _recent_stars(conn, day, cfg.tracking.dormant_after_days)
        tracking.apply_transitions(conn, cfg.tracking, day, stars, appeared)

        conn.execute(
            "INSERT OR REPLACE INTO ingested_dates (date, files_hash) VALUES (?, ?)",
            (d, files_hash),
        )


def _upsert_snapshot(conn: sqlite3.Connection, d: str, row: dict[str, object]) -> None:
    node_id = row["node_id"]
    conn.execute(
        "INSERT INTO repos (node_id, full_name, first_seen, state, state_since, last_snapshot) "
        "VALUES (?, ?, ?, 'active', ?, ?) ON CONFLICT (node_id) DO UPDATE SET "
        "full_name = excluded.full_name, last_snapshot = excluded.last_snapshot",
        (node_id, row["full_name"], d, d, d),
    )
    meta = row.get("meta")
    if isinstance(meta, dict):
        conn.execute(
            "UPDATE repos SET description = ?, topics = ?, language = ?, archived = ?, "
            "created_at = ? WHERE node_id = ?",
            (
                meta["description"],
                json.dumps(meta["topics"], ensure_ascii=False),
                meta["language"],
                int(bool(meta["archived"])),
                meta["created_at"],
                node_id,
            ),
        )
    conn.execute(
        "INSERT OR REPLACE INTO repo_daily (node_id, date, stars, forks, watchers, open_issues) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (node_id, d, row["stars"], row["forks"], row["watchers"], row["open_issues"]),
    )


def _lookup(conn: sqlite3.Connection, full_name: str) -> str | None:
    """以名稱找 node_id。同名的 repo 有多個時，優先選不是 gone、最近有快照的。"""
    row = conn.execute(
        "SELECT node_id FROM repos WHERE full_name = ? COLLATE NOCASE "
        "ORDER BY state = 'gone', last_snapshot IS NULL, last_snapshot DESC, node_id LIMIT 1",
        (full_name,),
    ).fetchone()
    return None if row is None else str(row["node_id"])


def _recent_stars(conn: sqlite3.Connection, day: date, window: int) -> dict[str, dict[date, int]]:
    """當天有快照的 repo，day 往前 window 天內的 star 數。"""
    start = (day - timedelta(days=window)).isoformat()
    rows = conn.execute(
        "SELECT s.node_id, s.date, s.stars FROM repo_daily s "
        "WHERE s.date BETWEEN ? AND ? AND s.node_id IN "
        "(SELECT node_id FROM repo_daily WHERE date = ?)",
        (start, day.isoformat(), day.isoformat()),
    )
    result: dict[str, dict[date, int]] = defaultdict(dict)
    for r in rows:
        result[r["node_id"]][date.fromisoformat(r["date"])] = int(r["stars"])
    return dict(result)
