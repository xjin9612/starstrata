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
    """day 的 run.json 合併後沒有缺少的 Trending 榜、Search 成功過，也沒有缺快照的 repo。"""
    path = data / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}" / "run.json"
    if not path.is_file():
        return False
    merged = json.loads(path.read_text(encoding="utf-8")).get("merged")
    return (
        isinstance(merged, dict)
        and merged.get("trending_missing") == []
        and merged.get("search_ok") is True
        and merged.get("repos_missing") == []
    )


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="判斷今天的收集是否已經完整")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--data", type=Path, default=Path("data"))
    args = parser.parse_args(argv)
    print("true" if complete(args.data, today(args.config)) else "false")


if __name__ == "__main__":
    main()
