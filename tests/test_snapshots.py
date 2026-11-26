from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from starstrata.snapshots import (
    RepoMeta,
    RepoSnapshot,
    day_dir,
    read_jsonl,
    repo_row,
    write_json,
    write_jsonl,
)

_META = RepoMeta(
    description="說明", topics=("a", "b"), language="Rust", archived=False, created_at="2026-01-01"
)
_SNAP = RepoSnapshot(
    node_id="R_a", full_name="o/a", stars=1, forks=2, watchers=3, open_issues=4, meta=_META
)


def test_day_dir_is_zero_padded(tmp_path: Path) -> None:
    assert day_dir(tmp_path, date(2026, 3, 7)) == tmp_path / "2026" / "03" / "07"


def test_jsonl_roundtrip_keeps_unicode(tmp_path: Path) -> None:
    path = tmp_path / "x" / "rows.jsonl"
    rows: list[dict[str, Any]] = [{"b": 1, "a": "中文"}, {"a": None}]
    write_jsonl(path, rows)
    assert read_jsonl(path) == rows
    text = path.read_text(encoding="utf-8")
    assert text == '{"a":"中文","b":1}\n{"a":null}\n'


def test_write_replaces_file_and_leaves_no_temp(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    write_jsonl(path, [{"a": 1}])
    write_jsonl(path, [{"a": 2}])
    assert read_jsonl(path) == [{"a": 2}]
    assert [p.name for p in tmp_path.iterdir()] == ["rows.jsonl"]


def test_empty_jsonl_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    write_jsonl(path, [])
    assert read_jsonl(path) == []


def test_write_json_is_pretty_and_sorted(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    write_json(path, {"b": 1, "a": 2})
    assert path.read_text(encoding="utf-8") == '{\n  "a": 2,\n  "b": 1\n}\n'


def test_repo_row_includes_meta_only_when_asked() -> None:
    assert "meta" not in repo_row(_SNAP, include_meta=False)
    row = repo_row(_SNAP, include_meta=True)
    assert row["meta"] == {
        "description": "說明",
        "topics": ["a", "b"],
        "language": "Rust",
        "archived": False,
        "created_at": "2026-01-01",
    }


def test_day_with_only_run_json_is_not_a_snapshot_day(tmp_path: Path) -> None:
    from starstrata.snapshots import list_snapshot_days

    write_json(day_dir(tmp_path, date(2026, 11, 1)) / "run.json", {"date": "2026-11-01"})
    write_jsonl(day_dir(tmp_path, date(2026, 11, 2)) / "search.jsonl", [])
    assert list_snapshot_days(tmp_path) == [date(2026, 11, 2)]


def test_node_file_stem_roundtrip_and_case_safety() -> None:
    from starstrata.snapshots import node_file_stem, node_id_from_stem

    a, b = node_file_stem("R_kgDOabc"), node_file_stem("R_kgDOABC")
    assert a.lower() != b.lower()
    assert node_id_from_stem(a) == "R_kgDOabc"
    assert node_id_from_stem("not-hex") is None
