"""判斷今天的收集是否已經完整，印出 true 或 false。daily workflow 的備援排程依此決定要不要跳過。

只用標準函式庫，不必先安裝相依套件。

    python3 scripts/collected_today.py [--config config.toml] [--data data]
"""

from __future__ import annotations

import argparse
import json
import tomllib
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def today(config: Path, now: datetime | None = None) -> date:
    """config 的 schedule.timezone 下的今天，與 collect 寫入快照的日期相同。"""
    timezone = tomllib.loads(config.read_text(encoding="utf-8"))["schedule"]["timezone"]
    return (now or datetime.now(UTC)).astimezone(ZoneInfo(timezone)).date()


def complete(data: Path, day: date) -> bool:
    """day 的收集是否完整。

    merged 只涵蓋依 node_id 查詢的 repo，以名稱查詢的 Trending repo 失敗時要看最後一次執行的
    sources；DB 無法讀取時要查詢的 repo 本身就缺，要看 tracking_fresh。缺任何一項都視為不完整。
    """
    path = data / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}" / "run.json"
    if not path.is_file():
        return False
    run = json.loads(path.read_text(encoding="utf-8"))
    merged, sources = run.get("merged"), run.get("sources")
    return (
        isinstance(merged, dict)
        and merged.get("trending_missing") == []
        and merged.get("search_ok") is True
        and merged.get("repos_missing") == []
        and isinstance(sources, dict)
        and all(sources.get(s, {}).get("status") == "ok" for s in ("trending", "search", "repos"))
        and run.get("tracking_fresh") is True
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="判斷今天的收集是否已經完整")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    print("true" if complete(args.data, today(args.config)) else "false")


if __name__ == "__main__":
    main()
