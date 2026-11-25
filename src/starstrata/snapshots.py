"""每日快照的資料結構、路徑與 JSONL 讀寫。快照格式見 docs/DEVELOPMENT.md。"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

TRENDING_FILE = "trending.jsonl"
SEARCH_FILE = "search.jsonl"
REPOS_FILE = "repos.jsonl"
RUN_FILE = "run.json"
# 凍結後的當天警示，見 ingest.freeze_alerts。
ALERTS_FILE = "alerts.jsonl"


@dataclass(frozen=True)
class TrendingEntry:
    period: str
    # None 表示總榜。
    language: str | None
    rank: int
    full_name: str
    # 頁面上沒有顯示期間內新增 star 數時為 None。
    stars_in_period: int | None


@dataclass(frozen=True)
class SearchHit:
    rank: int
    node_id: str
    full_name: str


@dataclass(frozen=True)
class RepoMeta:
    description: str | None
    topics: tuple[str, ...]
    language: str | None
    archived: bool
    created_at: str


@dataclass(frozen=True)
class RepoSnapshot:
    node_id: str
    full_name: str
    stars: int
    forks: int
    watchers: int
    open_issues: int
    meta: RepoMeta


def node_file_stem(node_id: str) -> str:
    """以 node_id 命名檔案時用的檔名。

    node_id 區分大小寫，Windows 與 macOS 的檔案系統預設不區分，所以改用十六進位編碼。
    """
    return node_id.encode().hex()


def node_id_from_stem(stem: str) -> str | None:
    """node_file_stem 的反向轉換；檔名不是合法編碼時回傳 None。"""
    try:
        return bytes.fromhex(stem).decode()
    except ValueError:
        return None


def day_dir(data_root: Path, day: date) -> Path:
    return data_root / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}"


def trending_row(entry: TrendingEntry) -> dict[str, Any]:
    return {
        "period": entry.period,
        "language": entry.language,
        "rank": entry.rank,
        "full_name": entry.full_name,
        "stars_in_period": entry.stars_in_period,
    }


def search_row(hit: SearchHit) -> dict[str, Any]:
    return {"rank": hit.rank, "node_id": hit.node_id, "full_name": hit.full_name}


def repo_row(snapshot: RepoSnapshot, *, include_meta: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "node_id": snapshot.node_id,
        "full_name": snapshot.full_name,
        "stars": snapshot.stars,
        "forks": snapshot.forks,
        "watchers": snapshot.watchers,
        "open_issues": snapshot.open_issues,
    }
    if include_meta:
        m = snapshot.meta
        row["meta"] = {
            "description": m.description,
            "topics": list(m.topics),
            "language": m.language,
            "archived": m.archived,
            "created_at": m.created_at,
        }
    return row


def gone_row(node_id: str) -> dict[str, Any]:
    return {"node_id": node_id, "status": "gone"}


def _dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    """以原子方式覆寫整個檔案。"""
    write_text_atomic(path, "".join(_dump(row) + "\n" for row in rows))


def write_json(path: Path, obj: Any) -> None:
    write_text_atomic(path, json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


SNAPSHOT_SOURCE_FILES = (TRENDING_FILE, SEARCH_FILE, REPOS_FILE)


def list_snapshot_days(data_root: Path) -> list[date]:
    """列出 data_root 下所有含快照檔的日期目錄，由舊到新。"""
    days: list[date] = []
    for folder in data_root.glob("[0-9][0-9][0-9][0-9]/[0-9][0-9]/[0-9][0-9]"):
        if not any((folder / name).is_file() for name in SNAPSHOT_SOURCE_FILES):
            continue
        y, m, d = folder.relative_to(data_root).parts
        days.append(date(int(y), int(m), int(d)))
    return sorted(days)


def day_hash(folder: Path) -> str:
    """當天快照檔與凍結警示的雜湊，不含 run.json。用來偵測已匯入的日期是否被改寫。"""
    digest = hashlib.sha256()
    for name in (*SNAPSHOT_SOURCE_FILES, ALERTS_FILE):
        path = folder / name
        if path.is_file():
            digest.update(name.encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()
