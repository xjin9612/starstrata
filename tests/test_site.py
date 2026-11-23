from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from starstrata import db
from starstrata.backfill import backfill_path
from starstrata.cli import main
from starstrata.config import Config, load_config
from starstrata.ingest import sync
from starstrata.site import build_site, repo_series
from starstrata.snapshots import node_file_stem, write_jsonl
from tests.snapshot_builder import meta, repo, search, trending, write_day

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 23, 1, 0, tzinfo=UTC)


def test_series_uses_backfill_before_first_snapshot() -> None:
    points, daily = repo_series(
        snapshots=[("2026-11-03", 20), ("2026-11-04", 25), ("2026-11-06", 30)],
        backfill=[("2026-11-01", 5), ("2026-11-02", 3), ("2026-11-03", 9)],
    )
    assert points == [
        ["2026-11-01", 5, "b"],
        ["2026-11-02", 8, "b"],
        ["2026-11-03", 20, "s"],
        ["2026-11-04", 25, "s"],
        ["2026-11-06", 30, "s"],
    ]
    assert daily == [["2026-11-01", 5, "b"], ["2026-11-02", 3, "b"], ["2026-11-04", 5, "s"]]


def test_series_without_backfill_or_snapshots() -> None:
    assert repo_series([], []) == ([], [])
    assert repo_series([], [("2026-11-01", 2)]) == (
        [["2026-11-01", 2, "b"]],
        [["2026-11-01", 2, "b"]],
    )


@pytest.fixture
def cfg() -> Config:
    return load_config(REPO_ROOT / "config.toml")


def _repo_json(out: Path, node_id: str) -> Path:
    return out / "data" / "repos" / f"{node_file_stem(node_id)}.json"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def test_build_site(cfg: Config, tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_day(
        data,
        date(2026, 11, 1),
        repos=[
            repo("R_a", 100, full_name="o/a", meta=meta(topics=["x"])),
            repo("R_c", 5, full_name="o/c", meta=meta()),
        ],
        trending_rows=[trending("o/a"), trending("o/a", language="python")],
        search_rows=[search("R_c", "o/c")],
    )
    write_day(
        data,
        date(2026, 11, 2),
        repos=[repo("R_a", 130, full_name="o/a"), repo("R_b", 7, full_name="o/b", meta=meta())],
        search_rows=[search("R_b", "o/b")],
    )
    write_jsonl(backfill_path(data, "R_a"), [{"week": "2026-10-25", "days": [1, 0, 0, 0, 0, 0, 2]}])
    db_path = tmp_path / "db.sqlite"
    sync(db_path, cfg, data)
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html></html>", encoding="utf-8")
    out = tmp_path / "_site"
    out.mkdir()
    (out / "stale.txt").write_text("old", encoding="utf-8")

    conn = db.connect(db_path)
    try:
        stats = build_site(conn, cfg, out, web, NOW)
    finally:
        conn.close()

    assert stats.latest == date(2026, 11, 2) and stats.repos == 3
    assert not (out / "stale.txt").exists()
    assert (out / "index.html").is_file()
    latest = _read(out / "data" / "latest.json")
    assert latest["date"] == "2026-11-02"
    assert latest["generated_at"] == "2026-11-23T01:00:00+00:00"
    assert [r["full_name"] for r in latest["new_repos"]] == ["o/b"]
    assert [(r["full_name"], r["delta"]) for r in latest["gainers"]] == [("o/a", 30)]
    assert latest["trending"] == []
    assert _read(out / "data" / "index.json") == [
        ["R_a", "o/a", "Python", "active", 130],
        ["R_b", "o/b", "Python", "active", 7],
        ["R_c", "o/c", "Python", "active", 5],
    ]
    detail = _read(_repo_json(out, "R_a"))
    assert detail["topics"] == ["x"]
    assert detail["points"] == [
        ["2026-10-25", 1, "b"],
        ["2026-10-31", 3, "b"],
        ["2026-11-01", 100, "s"],
        ["2026-11-02", 130, "s"],
    ]
    assert detail["trending"] == [
        ["2026-11-01", "daily", None, 1],
        ["2026-11-01", "daily", "python", 1],
    ]
    assert _read(_repo_json(out, "R_b"))["points"] == [["2026-11-02", 7, "s"]]


def test_latest_trending_follows_config_order(cfg: Config, tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_day(
        data,
        date(2026, 11, 1),
        repos=[repo("R_a", 1, full_name="o/a")],
        trending_rows=[
            trending("o/a", language="rust"),
            trending("o/a", language="python"),
            trending("o/a"),
        ],
    )
    db_path = tmp_path / "db.sqlite"
    sync(db_path, cfg, data)
    web = tmp_path / "web"
    web.mkdir()
    conn = db.connect(db_path)
    try:
        build_site(conn, cfg, tmp_path / "_site", web, NOW)
    finally:
        conn.close()
    latest = _read(tmp_path / "_site" / "data" / "latest.json")
    assert [p["language"] for p in latest["trending"]] == [None, "python", "rust"]


def test_empty_database_builds_placeholder(cfg: Config, tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite"
    sync(db_path, cfg, tmp_path / "data")
    web = tmp_path / "web"
    web.mkdir()
    conn = db.connect(db_path)
    try:
        stats = build_site(conn, cfg, tmp_path / "_site", web, NOW)
    finally:
        conn.close()
    assert stats.latest is None
    latest = _read(tmp_path / "_site" / "data" / "latest.json")
    assert latest["date"] is None and latest["trending"] == []


def test_cli_site_copies_web_and_icon(tmp_path: Path) -> None:
    out = tmp_path / "_site"
    code = main(
        [
            "--config",
            str(REPO_ROOT / "config.toml"),
            "--data",
            str(tmp_path / "data"),
            "--db",
            str(tmp_path / "db.sqlite"),
            "site",
            "--out",
            str(out),
            "--web",
            str(REPO_ROOT / "web"),
            "--icon",
            str(REPO_ROOT / "assets" / "icon.svg"),
        ]
    )
    assert code == 0
    assert {p.name for p in out.iterdir()} >= {
        "index.html",
        "app.js",
        "style.css",
        "icon.svg",
        "data",
    }
