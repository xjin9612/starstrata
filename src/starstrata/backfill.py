"""回補不是 gone 的 repo 的歷史 star 曲線，寫入 data/backfill/（檔名見 backfill_path）。"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from starstrata.config import BackfillConfig
from starstrata.db import GONE
from starstrata.github.http import GitHubClient, GitHubError, RateLimitExceeded
from starstrata.github.star_history import estimate_requests, fetch_history
from starstrata.snapshots import node_file_stem, write_jsonl

log = logging.getLogger(__name__)

BACKFILL_DIR = "backfill"


@dataclass
class BackfillResult:
    done: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    requests: int = 0
    # 提前結束的原因；處理完全部待回補 repo 時為 None。
    stopped: str | None = None
    rate_limited: bool = False


def backfill_path(data_root: Path, node_id: str) -> Path:
    return data_root / BACKFILL_DIR / f"{node_file_stem(node_id)}.jsonl"


def candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """不是 gone、尚未匯入回補檔的 repo，依首次出現日期排序。"""
    return list(
        conn.execute(
            "SELECT node_id, full_name, created_at FROM repos "
            "WHERE state != ? AND node_id NOT IN (SELECT node_id FROM backfilled) "
            "ORDER BY first_seen, node_id",
            (GONE,),
        )
    )


def run_backfill(
    client: GitHubClient,
    conn: sqlite3.Connection,
    cfg: BackfillConfig,
    data_root: Path,
    today: date,
) -> BackfillResult:
    """在 daily_request_budget 內依序回補，一個 repo 抓完全部歷史才寫檔。

    剩餘預算不足以抓完下一個 repo 時結束，留給下次執行。
    """
    result = BackfillResult()
    budget = cfg.daily_request_budget
    for row in candidates(conn):
        node_id, full_name = row["node_id"], row["full_name"]
        path = backfill_path(data_root, node_id)
        if path.exists():
            continue
        remaining = budget - result.requests
        if estimate_requests(row["created_at"], today) > remaining:
            result.stopped = "預算不足"
            break
        before = client.request_count
        try:
            history = fetch_history(client, full_name, max_requests=remaining)
        except RateLimitExceeded as e:
            result.requests += client.request_count - before
            result.stopped = str(e)
            result.rate_limited = True
            break
        except (GitHubError, KeyError, TypeError, ValueError) as e:
            result.requests += client.request_count - before
            log.warning("回補 %s 失敗：%s", full_name, e)
            result.failed.append(node_id)
            continue
        result.requests += client.request_count - before
        if history.weeks is None or history.requests > remaining:
            result.stopped = "預算不足"
            break
        write_jsonl(
            path, ({"week": w.start.isoformat(), "days": list(w.days)} for w in history.weeks)
        )
        result.done.append(node_id)
    return result
