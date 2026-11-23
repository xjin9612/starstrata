"""SQLite schema 與連線。DB 內容全部由快照推導，可隨時刪除重建。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from starstrata.config import Config
from starstrata.snapshots import RepoMeta

# schema 或匯入邏輯改變時遞增；版本不符的 DB 會被重建。
SCHEMA_VERSION = 2

ACTIVE = "active"
DORMANT = "dormant"
GONE = "gone"

# trending_entries.language 以空字串代表總榜，因為主鍵欄位不能用 NULL 區分。
OVERALL = ""

_SCHEMA = """
CREATE TABLE meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE repos (
  node_id TEXT PRIMARY KEY,
  full_name TEXT NOT NULL,
  -- 以下 meta 欄位在第一次記錄 meta 之前為 NULL；topics 為 JSON 陣列。
  description TEXT,
  topics TEXT,
  language TEXT,
  archived INTEGER,
  created_at TEXT,
  first_seen TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('active', 'dormant', 'gone')),
  state_since TEXT NOT NULL,
  last_snapshot TEXT
);
CREATE INDEX repos_full_name ON repos (full_name COLLATE NOCASE);

CREATE TABLE repo_daily (
  node_id TEXT NOT NULL,
  date TEXT NOT NULL,
  stars INTEGER NOT NULL,
  forks INTEGER NOT NULL,
  watchers INTEGER NOT NULL,
  open_issues INTEGER NOT NULL,
  PRIMARY KEY (node_id, date)
) WITHOUT ROWID;
CREATE INDEX repo_daily_date ON repo_daily (date);

CREATE TABLE trending_entries (
  date TEXT NOT NULL,
  period TEXT NOT NULL,
  language TEXT NOT NULL,
  rank INTEGER NOT NULL,
  full_name TEXT NOT NULL,
  -- 當天查不到 repo 時為 NULL。
  node_id TEXT,
  stars_in_period INTEGER,
  PRIMARY KEY (date, period, language, rank)
);

CREATE TABLE search_hits (
  date TEXT NOT NULL,
  rank INTEGER NOT NULL,
  node_id TEXT NOT NULL,
  full_name TEXT NOT NULL,
  PRIMARY KEY (date, rank)
);

CREATE TABLE star_backfill (
  node_id TEXT NOT NULL,
  date TEXT NOT NULL,
  stars_added INTEGER NOT NULL,
  PRIMARY KEY (node_id, date)
) WITHOUT ROWID;

CREATE TABLE backfilled (
  node_id TEXT PRIMARY KEY,
  file_hash TEXT NOT NULL
);

CREATE TABLE alerts (
  node_id TEXT NOT NULL,
  date TEXT NOT NULL,
  daily_stars INTEGER NOT NULL,
  -- 基準資料不足時為 NULL，此時只依絕對下限判定。
  baseline REAL,
  PRIMARY KEY (node_id, date)
);
CREATE INDEX alerts_date ON alerts (date);

CREATE TABLE ingested_dates (
  date TEXT PRIMARY KEY,
  files_hash TEXT NOT NULL
);
"""


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection, cfg: Config) -> None:
    conn.executescript(_SCHEMA)
    set_meta(conn, "schema_version", str(SCHEMA_VERSION))
    set_meta(conn, "derivation_fingerprint", derivation_fingerprint(cfg))


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row["value"])


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def derivation_fingerprint(cfg: Config) -> str:
    """影響追蹤狀態與爆紅判定的設定。這些設定改變時，DB 必須重建。"""
    payload = json.dumps(
        {"tracking": asdict(cfg.tracking), "alerts": asdict(cfg.alerts)}, sort_keys=True
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def known_meta(conn: sqlite3.Connection) -> dict[str, RepoMeta]:
    rows = conn.execute(
        "SELECT node_id, description, topics, language, archived, created_at "
        "FROM repos WHERE topics IS NOT NULL"
    )
    return {
        r["node_id"]: RepoMeta(
            description=r["description"],
            topics=tuple(json.loads(r["topics"])),
            language=r["language"],
            archived=bool(r["archived"]),
            created_at=r["created_at"],
        )
        for r in rows
    }
