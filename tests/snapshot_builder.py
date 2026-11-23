"""在測試用的資料目錄寫入快照檔。"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from starstrata.snapshots import day_dir, write_jsonl


def meta(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "description": "desc",
        "topics": ["t"],
        "language": "Python",
        "archived": False,
        "created_at": "2026-01-01T00:00:00Z",
    }
    return base | overrides


def repo(node_id: str, stars: int, *, full_name: str | None = None, **extra: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "node_id": node_id,
        "full_name": full_name or f"o/{node_id.lower()}",
        "stars": stars,
        "forks": 0,
        "watchers": 0,
        "open_issues": 0,
    }
    return row | extra


def gone(node_id: str) -> dict[str, Any]:
    return {"node_id": node_id, "status": "gone"}


def trending(full_name: str, rank: int = 1, *, language: str | None = None) -> dict[str, Any]:
    return {
        "period": "daily",
        "language": language,
        "rank": rank,
        "full_name": full_name,
        "stars_in_period": 10,
    }


def search(node_id: str, full_name: str, rank: int = 1) -> dict[str, Any]:
    return {"rank": rank, "node_id": node_id, "full_name": full_name}


def write_day(
    root: Path,
    day: date,
    *,
    repos: Sequence[dict[str, Any]] | None = None,
    trending_rows: Sequence[dict[str, Any]] | None = None,
    search_rows: Sequence[dict[str, Any]] | None = None,
) -> None:
    folder = day_dir(root, day)
    if repos is not None:
        write_jsonl(folder / "repos.jsonl", repos)
    if trending_rows is not None:
        write_jsonl(folder / "trending.jsonl", trending_rows)
    if search_rows is not None:
        write_jsonl(folder / "search.jsonl", search_rows)
