"""scripts/collected_today.py 的測試。"""

from __future__ import annotations

import importlib.util
from datetime import UTC, date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from starstrata.snapshots import RUN_FILE, day_dir, write_json

ROOT = Path(__file__).resolve().parent.parent
DAY = date(2026, 11, 26)
MERGED = {"trending_missing": [], "search_ok": True, "repos_missing": []}
SOURCES = {s: {"status": "ok"} for s in ("trending", "search", "repos")}
COMPLETE = {"merged": MERGED, "sources": SOURCES, "tracking_fresh": True}


def _load() -> ModuleType:
    path = ROOT / "scripts" / "collected_today.py"
    spec = importlib.util.spec_from_file_location("collected_today", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collected = _load()


def _write(data: Path, day: date, run: dict[str, Any]) -> None:
    write_json(day_dir(data, day) / RUN_FILE, run)


def test_today_uses_configured_timezone() -> None:
    # UTC 11-25 21:17 是台北 11-26 05:17，與 collect 寫入快照的日期相同。
    now = datetime(2026, 11, 25, 21, 17, tzinfo=UTC)
    assert collected.today(ROOT / "config.toml", now) == DAY


def test_complete_run(tmp_path: Path) -> None:
    _write(tmp_path, DAY, COMPLETE)
    assert collected.complete(tmp_path, DAY)


def _without(d: dict[str, Any], key: str) -> dict[str, Any]:
    return {k: v for k, v in d.items() if k != key}


@pytest.mark.parametrize(
    "run",
    [
        COMPLETE
        | {"merged": MERGED | {"trending_missing": [{"period": "daily", "language": None}]}},
        COMPLETE | {"merged": MERGED | {"search_ok": False}},
        COMPLETE | {"merged": MERGED | {"repos_missing": ["R_a"]}},
        COMPLETE | {"merged": _without(MERGED, "trending_missing")},
        _without(COMPLETE, "merged"),
        # 以名稱查詢的 Trending repo 失敗時 merged 仍完整，要看這次執行的 sources。
        COMPLETE | {"sources": SOURCES | {"repos": {"status": "partial"}}},
        _without(COMPLETE, "sources"),
        # DB 無法讀取時要查詢的 repo 本身就缺，merged 看不出來。
        COMPLETE | {"tracking_fresh": False},
        _without(COMPLETE, "tracking_fresh"),
    ],
)
def test_incomplete_run(tmp_path: Path, run: dict[str, Any]) -> None:
    _write(tmp_path, DAY, run)
    assert not collected.complete(tmp_path, DAY)


def test_missing_run(tmp_path: Path) -> None:
    assert not collected.complete(tmp_path, DAY)


@pytest.mark.parametrize(("run", "expected"), [(COMPLETE, "true"), ({}, "false")])
def test_main_reads_given_data(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], run: dict[str, Any], expected: str
) -> None:
    today = collected.today(ROOT / "config.toml")
    _write(tmp_path, today, run)
    collected.main(["--config", str(ROOT / "config.toml"), "--data", str(tmp_path)])
    assert capsys.readouterr().out == expected + "\n"
