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
COMPLETE = {"trending_missing": [], "search_ok": True, "repos_missing": []}


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "collected_today", ROOT / "scripts" / "collected_today.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collected = _load()


def _run(data: Path, run: dict[str, Any]) -> None:
    write_json(day_dir(data, DAY) / RUN_FILE, run)


def test_today_uses_configured_timezone() -> None:
    # UTC 11-25 21:17 是台北 11-26 05:17，與 collect 寫入快照的日期相同。
    now = datetime(2026, 11, 25, 21, 17, tzinfo=UTC)
    assert collected.today(ROOT / "config.toml", now) == DAY


def test_complete_run(tmp_path: Path) -> None:
    _run(tmp_path, {"merged": COMPLETE})
    assert collected.complete(tmp_path, DAY)


@pytest.mark.parametrize(
    "merged",
    [
        COMPLETE | {"trending_missing": [{"period": "daily", "language": None}]},
        COMPLETE | {"search_ok": False},
        COMPLETE | {"repos_missing": ["R_a"]},
        None,
    ],
)
def test_incomplete_run(tmp_path: Path, merged: dict[str, Any] | None) -> None:
    _run(tmp_path, {} if merged is None else {"merged": merged})
    assert not collected.complete(tmp_path, DAY)


def test_missing_run(tmp_path: Path) -> None:
    assert not collected.complete(tmp_path, DAY)


def test_main_prints_result(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    today = collected.today(ROOT / "config.toml")
    write_json(day_dir(tmp_path, today) / RUN_FILE, {"merged": COMPLETE})
    collected.main(["--config", str(ROOT / "config.toml"), "--data", str(tmp_path)])
    assert capsys.readouterr().out == "true\n"
