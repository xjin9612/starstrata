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
from starstrata.site import build_site, repo_path, repo_series
from starstrata.snapshots import write_jsonl
from starstrata.translate import text_key
from tests.snapshot_builder import gone, meta, repo, search, trending, write_day

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


def _repo_json(out: Path, full_name: str) -> Path:
    owner, name = full_name.split("/")
    return out / "data" / "repos" / owner / f"{name}.json"


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _build(cfg: Config, tmp_path: Path, data: Path, **kwargs: Any) -> tuple[Path, Any]:
    db_path = tmp_path / "db.sqlite"
    sync(db_path, cfg, data)
    web = tmp_path / "web"
    web.mkdir(exist_ok=True)
    (web / "index.html").write_text("<html></html>", encoding="utf-8")
    out = tmp_path / "_site"
    conn = db.connect(db_path)
    try:
        stats = build_site(conn, cfg, out, web, NOW, **kwargs)
    finally:
        conn.close()
    return out, stats


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
    (tmp_path / "_site").mkdir()
    (tmp_path / "_site" / "stale.txt").write_text("old", encoding="utf-8")

    out, stats = _build(cfg, tmp_path, data)

    assert stats.latest == date(2026, 11, 2) and stats.repos == 3
    assert not (out / "stale.txt").exists()
    assert (out / "index.html").is_file()
    assert _read(out / "data" / "meta.json") == {
        "data_date": "2026-11-02",
        "tracked_count": 3,
        "is_first_day": False,
        "new_count": 1,
        "hot_count": 0,
        "generated_at": "2026-11-23T01:00:00+00:00",
    }
    new = _read(out / "data" / "new.json")["items"]
    assert [(r["rank"], r["full_name"], r["period_stars"], r["is_new"]) for r in new] == [
        (1, "o/b", None, True)
    ]
    daily = _read(out / "data" / "daily.json")["items"]
    assert [(r["rank"], r["full_name"], r["period_stars"], r["stargazers"]) for r in daily] == [
        (1, "o/a", 30, 130)
    ]
    trending_json = _read(out / "data" / "trending.json")
    assert set(trending_json) == set(cfg.trending.periods)
    assert all(not page for pages in trending_json.values() for page in pages.values())
    assert _read(out / "data" / "alerts.json") == {"items": []}
    assert _read(out / "data" / "search-index.json") == [
        ["o/a", "Python", 130],
        ["o/b", "Python", 7],
        ["o/c", "Python", 5],
    ]
    detail = _read(_repo_json(out, "o/a"))
    assert detail["topics"] == ["x"]
    assert detail["stargazers"] == 130 and detail["created_at"] == "2026-01-01"
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
    assert _read(_repo_json(out, "o/b"))["points"] == [["2026-11-02", 7, "s"]]


def test_trending_pages_and_new_repo_order(cfg: Config, tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_day(data, date(2026, 11, 1), repos=[repo("R_old", 1, full_name="o/old", meta=meta())])
    write_day(
        data,
        date(2026, 11, 2),
        repos=[
            repo("R_old", 2, full_name="o/old"),
            repo("R_t", 10, full_name="o/t", meta=meta()),
            repo("R_py", 20, full_name="o/py", meta=meta()),
            repo("R_s", 900, full_name="o/s", meta=meta()),
        ],
        trending_rows=[
            trending("o/t", stars=50),
            trending("o/old", 2, stars=None),
            trending("o/py", language="python", stars=80),
            trending("ghost/missing", 3),
        ],
        search_rows=[search("R_s", "o/s")],
    )
    out, _ = _build(cfg, tmp_path, data)
    pages = _read(out / "data" / "trending.json")["daily"]
    assert [(r["rank"], r["full_name"], r["period_stars"], r["is_new"]) for r in pages["all"]] == [
        (1, "o/t", 50, True),
        (2, "o/old", None, False),
        (3, "ghost/missing", 10, False),
    ]
    assert pages["all"][2]["stargazers"] is None and pages["all"][2]["language"] is None
    assert [r["full_name"] for r in pages["python"]] == ["o/py"]
    new = _read(out / "data" / "new.json")["items"]
    assert [(r["rank"], r["full_name"], r["period_stars"]) for r in new] == [
        (1, "o/py", 80),
        (2, "o/t", 50),
        (3, "o/s", None),
    ]


def test_first_day_hides_new_and_counts(cfg: Config, tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_day(
        data,
        date(2026, 11, 1),
        repos=[repo("R_a", 1, meta=meta())],
        search_rows=[search("R_a", "o/r_a")],
    )
    out, _ = _build(cfg, tmp_path, data)
    meta_json = _read(out / "data" / "meta.json")
    assert meta_json["is_first_day"] is True
    assert meta_json["new_count"] is None and meta_json["hot_count"] is None
    assert _read(out / "data" / "new.json") == {"items": []}


def test_empty_database_builds_placeholder(cfg: Config, tmp_path: Path) -> None:
    out, stats = _build(cfg, tmp_path, tmp_path / "data")
    assert stats.latest is None and stats.repos == 0
    meta_json = _read(out / "data" / "meta.json")
    assert meta_json["data_date"] is None and meta_json["is_first_day"] is True
    assert _read(out / "data" / "search-index.json") == []


def test_alerts_use_rounded_baseline(cfg: Config, tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_day(data, date(2026, 11, 1), repos=[repo("R_a", 1, full_name="o/a", meta=meta())])
    write_day(data, date(2026, 11, 2), repos=[repo("R_a", 2, full_name="o/a")])
    db_path = tmp_path / "db.sqlite"
    sync(db_path, cfg, data)
    conn = db.connect(db_path)
    conn.executemany(
        "INSERT INTO alerts (node_id, date, daily_stars, baseline) VALUES (?, ?, ?, ?)",
        [("R_a", "2026-11-02", 500, 10.6), ("R_a", "2026-10-01", 300, None)],
    )
    conn.commit()
    conn.close()
    out, _ = _build(cfg, tmp_path, data)
    assert _read(out / "data" / "alerts.json") == {
        "items": [{"date": "2026-11-02", "full_name": "o/a", "added": 500, "baseline_7d": 11}]
    }
    assert _read(out / "data" / "meta.json")["hot_count"] == 1
    assert _read(out / "data" / "daily.json")["items"][0]["is_hot"] is True


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("o/a.b", Path("o") / "a.b.json"),
        ("o/.github", Path("o") / ".github.json"),
        ("o/..", None),
        ("o/a/b", None),
        ("o/a b", None),
        ("noslash", None),
    ],
)
def test_repo_path(full_name: str, expected: Path | None) -> None:
    assert repo_path(full_name) == expected


def test_case_collision_prefers_live_repo(cfg: Config, tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_day(
        data,
        date(2026, 11, 1),
        repos=[repo("R_old", 1, full_name="O/X", meta=meta(description="old"))],
        search_rows=[search("R_old", "O/X")],
    )
    write_day(
        data,
        date(2026, 11, 2),
        repos=[gone("R_old"), repo("R_new", 5, full_name="o/x", meta=meta(description="new"))],
        search_rows=[search("R_new", "o/x")],
    )
    out, stats = _build(cfg, tmp_path, data)
    files = list((out / "data" / "repos").rglob("*.json"))
    assert len(files) == 1 and stats.repos == 1
    assert _read(files[0])["description"] == "new"


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


def test_translations_set_zh_fields(cfg: Config, tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_day(
        data,
        date(2026, 11, 1),
        repos=[
            repo("R_a", 10, full_name="o/a", meta=meta(description="English")),
            repo("R_b", 5, full_name="o/b", meta=meta(description="已是中文")),
            repo("R_c", 1, full_name="o/c", meta=meta(description="Untranslated")),
        ],
        trending_rows=[trending("o/a"), trending("o/b", 2), trending("o/c", 3)],
    )
    translations = {text_key("English"): "英文", text_key("已是中文"): "已是中文"}
    out, _ = _build(cfg, tmp_path, data, translations=translations)
    items = _read(out / "data" / "trending.json")["daily"]["all"]
    assert [(r["description_zh"], r["zh_native"]) for r in items] == [
        ("英文", False),
        ("已是中文", True),
        (None, False),
    ]
    detail = _read(_repo_json(out, "o/a"))
    assert (detail["description"], detail["description_zh"]) == ("English", "英文")
