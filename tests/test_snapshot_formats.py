"""既有快照格式的相容性測試。

tests/fixtures/snapshots/ 下每個版本的檔案一旦提交就不再修改；快照格式改變時新增一個版本目錄，
舊版本必須持續能匯入。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from starstrata import db
from starstrata.config import load_config
from starstrata.ingest import sync

ROOT = Path(__file__).resolve().parent
FIXTURES = ROOT / "fixtures" / "snapshots"


def _rows(db_path: Path, sql: str) -> list[tuple[object, ...]]:
    conn = db.connect(db_path)
    try:
        return [tuple(r) for r in conn.execute(sql)]
    finally:
        conn.close()


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_snapshot_version_still_imports(version: str, tmp_path: Path) -> None:
    data = tmp_path / "data"
    shutil.copytree(FIXTURES / version, data)
    db_path = tmp_path / "db.sqlite"
    result = sync(db_path, load_config(ROOT.parent / "config.toml"), data)
    assert [d.isoformat() for d in result.ingested] == ["2026-11-01", "2026-11-02"]

    assert _rows(db_path, "SELECT node_id, full_name, state, topics, archived FROM repos") == [
        ("R_alpha", "Owner/Alpha", "active", '["ai", "cli"]', 0),
        ("R_beta", "owner/beta", "gone", "[]", 1),
        ("R_gamma", "new/gamma", "active", "[]", 0),
    ]
    assert _rows(
        db_path, "SELECT date, period, language, node_id, stars_in_period FROM trending_entries"
    ) == [
        ("2026-11-01", "daily", "", "R_alpha", 120),
        ("2026-11-01", "weekly", "python", "R_beta", None),
    ]
    assert _rows(db_path, "SELECT node_id FROM search_hits") == [("R_gamma",)]
    assert _rows(
        db_path, "SELECT node_id, date, stars FROM repo_daily WHERE date = '2026-11-02'"
    ) == [
        ("R_alpha", "2026-11-02", 1400),
        ("R_gamma", "2026-11-02", 320),
    ]
    assert _rows(db_path, "SELECT * FROM star_backfill") == [("R_alpha", "2026-10-31", 5)]


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        # v1 沒有 alerts.jsonl，依快照判定。回補檔在首筆快照前 2 天以前沒有新增，基準為 0。
        ("v1", [("R_alpha", "2026-11-02", 400, 0.0)]),
        # v2 新增 alerts.jsonl：凍結的警示照檔案匯入，空檔案表示沒有警示。
        ("v2", [("R_alpha", "2026-11-01", 250, None)]),
    ],
)
def test_snapshot_version_alerts(
    version: str, expected: list[tuple[object, ...]], tmp_path: Path
) -> None:
    data = tmp_path / "data"
    shutil.copytree(FIXTURES / version, data)
    db_path = tmp_path / "db.sqlite"
    sync(db_path, load_config(ROOT.parent / "config.toml"), data)
    assert _rows(db_path, "SELECT node_id, date, daily_stars, baseline FROM alerts") == expected
