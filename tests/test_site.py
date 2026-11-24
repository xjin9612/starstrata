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
from starstrata.notes import parse_notes
from starstrata.reports import GAINERS_HEADING
from starstrata.site import build_site, repo_path, repo_series
from starstrata.snapshots import write_json, write_jsonl
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
        "timezone": "Asia/Taipei",
        "locale": "zh-TW",
        "translation_lang": "zh-TW",
        "alert_rule": {
            "baseline_days": 7,
            "multiplier": 3.0,
            "min_daily_stars": 200,
            "cooldown_days": 7,
        },
        "collected_at": None,
        "run": None,
        "generated_at": "2026-11-23T01:00:00+00:00",
        "trending_prev_date": "2026-11-01",
        "reports_new": 0,
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
    assert _read(out / "data" / "search-full.json") == []


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
        "items": [{"date": "2026-11-02", "full_name": "o/a", "added": 500, "baseline": 10.6}]
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


def test_same_path_prefers_most_recent_snapshot(cfg: Config, tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_day(
        data,
        date(2026, 11, 1),
        repos=[repo("R_old", 1, full_name="o/x", meta=meta(description="old"))],
    )
    write_day(
        data,
        date(2026, 11, 3),
        repos=[repo("R_new", 5, full_name="O/X", meta=meta(description="new"))],
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


def test_translations_set_tr_fields(cfg: Config, tmp_path: Path) -> None:
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
    assert [(r["description_tr"], r["tr_native"]) for r in items] == [
        ("英文", False),
        ("已是中文", True),
        (None, False),
    ]
    detail = _read(_repo_json(out, "o/a"))
    assert (detail["description"], detail["description_tr"]) == ("English", "英文")
    assert _read(out / "data" / "search-full.json") == [
        ["o/a", "English", "英文", ["t"]],
        ["o/b", "已是中文", None, ["t"]],
        ["o/c", "Untranslated", None, ["t"]],
    ]


def _meta_with_run(cfg: Config, tmp_path: Path, run: dict[str, Any] | None) -> Any:
    data = tmp_path / "data"
    write_day(data, date(2026, 11, 1), repos=[repo("R_a", 100, full_name="o/a", meta=meta())])
    if run is not None:
        write_json(data / "2026" / "11" / "01" / "run.json", run)
    out, _ = _build(cfg, tmp_path, data, data_root=data)
    return _read(out / "data" / "meta.json")


def test_meta_reads_merged_run_status(cfg: Config, tmp_path: Path) -> None:
    missing = [{"period": "weekly", "language": "rust"}]
    run = {
        "finished_at": "2026-11-01T00:05:00+00:00",
        "merged": {"trending_missing": missing, "search_ok": False, "repos_missing": ["R_x"]},
    }
    meta_json = _meta_with_run(cfg, tmp_path, run)
    assert meta_json["collected_at"] == "2026-11-01T00:05:00+00:00"
    assert meta_json["run"] == {"trending_missing": missing, "search_ok": False, "repos_missing": 1}


def test_meta_without_merged_run_status(cfg: Config, tmp_path: Path) -> None:
    meta_json = _meta_with_run(cfg, tmp_path, {"finished_at": "2026-11-01T00:05:00+00:00"})
    assert meta_json["collected_at"] == "2026-11-01T00:05:00+00:00"
    assert meta_json["run"] is None


def test_meta_without_run_json(cfg: Config, tmp_path: Path) -> None:
    meta_json = _meta_with_run(cfg, tmp_path, None)
    assert meta_json["collected_at"] is None and meta_json["run"] is None


def _trending_build(
    cfg: Config, tmp_path: Path, days: dict[date, list[dict[str, Any]]]
) -> tuple[Any, Any]:
    data = tmp_path / "data"
    repos = [repo(f"R_{n}", 10, full_name=f"o/{n}", meta=meta()) for n in "abc"]
    for day, rows in days.items():
        write_day(data, day, repos=repos, trending_rows=rows)
    out, _ = _build(cfg, tmp_path, data)
    return _read(out / "data" / "meta.json"), _read(out / "data" / "trending.json")


def test_prev_rank_compares_with_previous_trending_date(cfg: Config, tmp_path: Path) -> None:
    meta_json, trending_json = _trending_build(
        cfg,
        tmp_path,
        {
            date(2026, 11, 1): [
                trending("o/a", 1),
                trending("o/b", 2),
                trending("X/Unresolved", 4),
                trending("o/a", 3, language="python"),
            ],
            # 11-02 沒有 Trending 資料，比較對象是 11-01。
            date(2026, 11, 2): [],
            date(2026, 11, 3): [
                trending("o/b", 1),
                trending("o/a", 2),
                trending("o/c", 3),
                # 查不到 node_id 的 repo 依不分大小寫的名稱比對。
                trending("x/unresolved", 4),
                trending("o/a", 3, language="python"),
            ],
        },
    )
    assert meta_json["trending_prev_date"] == "2026-11-01"
    assert [(i["full_name"], i["prev_rank"]) for i in trending_json["daily"]["all"]] == [
        ("o/b", 2),
        ("o/a", 1),
        ("o/c", None),
        ("x/unresolved", 4),
    ]
    assert trending_json["daily"]["python"][0]["prev_rank"] == 3


def test_no_prev_rank_without_previous_trending_date(cfg: Config, tmp_path: Path) -> None:
    meta_json, trending_json = _trending_build(
        cfg, tmp_path, {date(2026, 11, 1): [trending("o/a", 1)]}
    )
    assert meta_json["trending_prev_date"] is None
    assert "prev_rank" not in trending_json["daily"]["all"][0]


def test_spark_covers_last_seven_data_days(cfg: Config, tmp_path: Path) -> None:
    data = tmp_path / "data"
    for n in range(8):
        repos = [repo("R_a", 100 + 10 * n, full_name="o/a", meta=meta())]
        if n in (0, 7):
            # 每 7 天才有一次快照，與 dormant 相同。
            repos.append(repo("R_b", 50 + n, full_name="o/b", meta=meta()))
        if n != 4:
            repos.append(repo("R_c", 10 + n, full_name="o/c", meta=meta()))
        rows = [trending("o/a", 1), trending("o/b", 2), trending("o/c", 3), trending("x/none", 4)]
        write_day(data, date(2026, 11, 1 + n), repos=repos, trending_rows=rows)
    out, _ = _build(cfg, tmp_path, data)
    sparks = {
        i["full_name"]: i["spark"] for i in _read(out / "data" / "trending.json")["daily"]["all"]
    }
    assert sparks == {
        "o/a": [10] * 7,
        "o/b": [None] * 7,
        "o/c": [1, 1, 1, None, None, 1, 1],
        "x/none": None,
    }


def test_notes_are_matched_case_insensitively(
    cfg: Config, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data = tmp_path / "data"
    write_day(
        data,
        date(2026, 11, 1),
        repos=[
            repo("R_a", 10, full_name="Owner/A", meta=meta()),
            repo("R_b", 5, full_name="o/b", meta=meta()),
        ],
        trending_rows=[trending("Owner/A"), trending("o/b", 2)],
    )
    notes = parse_notes(
        '["owner/a"]\nnote = "好用"\nupdated = 2026-11-20\n'
        '["gone/x"]\nnote = "找不到"\nupdated = 2026-11-20\n'
    )
    out, _ = _build(cfg, tmp_path, data, notes=notes)
    items = _read(out / "data" / "trending.json")["daily"]["all"]
    assert [(i["full_name"], i["has_note"]) for i in items] == [("Owner/A", True), ("o/b", False)]
    assert _read(_repo_json(out, "Owner/A"))["note"] == {
        "text": "好用",
        "article_url": None,
        "updated": "2026-11-20",
    }
    assert _read(_repo_json(out, "o/b"))["note"] is None
    assert "notes.toml 的 gone/x 對不到任何 repo" in caplog.text


def test_new_marks_repos_only_found_by_search(cfg: Config, tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_day(data, date(2026, 11, 1), repos=[repo("R_a", 10, full_name="o/a", meta=meta())])
    write_day(
        data,
        date(2026, 11, 2),
        repos=[
            repo("R_t", 50, full_name="o/trend", meta=meta()),
            repo("R_n", 40, full_name="o/nostars", meta=meta()),
            repo("R_s", 30, full_name="o/search", meta=meta()),
            repo("R_w", 20, full_name="o/weekly", meta=meta()),
        ],
        # Trending 有時不顯示期間新增數，這種 repo 仍算在 Trending 上。
        trending_rows=[
            trending("o/trend", 1, stars=5),
            trending("o/nostars", 2, stars=None),
            trending("o/weekly", 1) | {"period": "weekly"},
        ],
        search_rows=[search("R_s", "o/search")],
    )
    out, _ = _build(cfg, tmp_path, data)
    new = _read(out / "data" / "new.json")["items"]
    assert [(i["full_name"], i["period_stars"], i["from_search"]) for i in new] == [
        ("o/trend", 5, False),
        ("o/nostars", None, False),
        ("o/search", None, True),
        ("o/weekly", None, False),
    ]


def _report(title: str, top: str) -> str:
    return (
        f"# {title}\n\n## 摘要\n\n- x\n\n## {GAINERS_HEADING}\n\n"
        "| # | Repo |\n|---:|---|\n"
        f"| 1 | [{top}](https://github.com/{top}) |\n"
    )


def test_reports_are_converted_and_listed(
    cfg: Config, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    data = tmp_path / "data"
    write_day(data, date(2026, 11, 24), repos=[repo("R_a", 10, full_name="o/a", meta=meta())])
    root = tmp_path / "reports"
    for kind, label, top in [
        ("weekly", "2026-W46", "o/old"),
        ("weekly", "2026-W47", "o/new"),
        ("monthly", "2026-10", "o/month"),
        ("weekly", "draft", "o/x"),
    ]:
        (root / kind).mkdir(parents=True, exist_ok=True)
        (root / kind / f"{label}.md").write_text(_report(f"報告 {label}", top), encoding="utf-8")

    out, _ = _build(cfg, tmp_path, data, reports_root=root, github_repo="me/site")

    index = _read(out / "data" / "reports" / "index.json")["items"]
    assert [(i["kind"], i["period"], i["start"], i["end"], i["top"]) for i in index] == [
        ("weekly", "2026-W47", "2026-11-16", "2026-11-22", ["o/new"]),
        ("weekly", "2026-W46", "2026-11-09", "2026-11-15", ["o/old"]),
        ("monthly", "2026-10", "2026-10-01", "2026-10-31", ["o/month"]),
    ]
    assert index[0]["title"] == "報告 2026-W47"
    assert index[0]["url"] == "https://github.com/me/site/blob/HEAD/reports/weekly/2026-W47.md"
    detail = _read(out / "data" / "reports" / "weekly" / "2026-W47.json")
    assert "top" not in detail
    assert '<a href="#/repo/o/new">' in detail["html"] and "<h1" not in detail["html"]
    # 2026-11-24 與 W47 的結束日相差 2 天、與 W46 相差 9 天。
    assert _read(out / "data" / "meta.json")["reports_new"] == 1
    assert "略過無法辨識期間的報告" in caplog.text


def test_reports_without_repo_or_directory(cfg: Config, tmp_path: Path) -> None:
    data = tmp_path / "data"
    write_day(data, date(2026, 11, 24), repos=[repo("R_a", 10, full_name="o/a", meta=meta())])
    root = tmp_path / "reports"
    (root / "monthly").mkdir(parents=True)
    (root / "monthly" / "2026-10.md").write_text("沒有標題\n", encoding="utf-8")
    out, _ = _build(cfg, tmp_path, data, reports_root=root)
    [item] = _read(out / "data" / "reports" / "index.json")["items"]
    assert item["url"] is None and item["top"] == []
    assert item["title"] == "starstrata 月報 2026-10（2026-10-01 至 2026-10-31）"

    out, _ = _build(cfg, tmp_path, data, reports_root=tmp_path / "none")
    assert _read(out / "data" / "reports" / "index.json") == {"items": []}
