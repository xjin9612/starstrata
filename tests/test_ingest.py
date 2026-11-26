from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pytest

from starstrata import db, tracking
from starstrata.config import AlertsConfig, Config, TrackingConfig, load_config
from starstrata.ingest import ALERTS_THROUGH, AlertsFileError, freeze_alerts, sync
from starstrata.snapshots import ALERTS_FILE, day_dir, read_jsonl, write_jsonl
from tests.snapshot_builder import gone, meta, repo, search, trending, write_day

D1 = date(2026, 11, 1)
REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


def day(n: int) -> date:
    return D1 + timedelta(days=n - 1)


@pytest.fixture
def cfg() -> Config:
    base = load_config(REPO_CONFIG)
    return replace(
        base,
        tracking=TrackingConfig(
            dormant_after_days=3,
            dormant_daily_stars_below=5,
            reactivate_weekly_stars=20,
            dormant_snapshot_interval_days=7,
        ),
        alerts=AlertsConfig(baseline_days=2, multiplier=3.0, min_daily_stars=100, cooldown_days=7),
    )


@pytest.fixture
def paths(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "data", tmp_path / "db.sqlite"


def _query(db_path: Path, sql: str, *params: object) -> list[tuple[object, ...]]:
    conn = db.connect(db_path)
    try:
        return [tuple(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def _state(db_path: Path, node_id: str) -> tuple[object, ...]:
    [row] = _query(db_path, "SELECT state, state_since FROM repos WHERE node_id = ?", node_id)
    return row


def _dump(db_path: Path) -> dict[str, list[tuple[object, ...]]]:
    tables = [
        "meta",
        "repos",
        "repo_daily",
        "trending_entries",
        "search_hits",
        "star_backfill",
        "backfilled",
        "alerts",
        "ingested_dates",
    ]
    return {t: sorted(_query(db_path, f"SELECT * FROM {t}"), key=repr) for t in tables}


def test_first_ingest_creates_db(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(
        data,
        day(1),
        repos=[repo("R_a", 100, full_name="Owner/A", meta=meta(topics=["x", "y"]))],
        trending_rows=[trending("Owner/A")],
        search_rows=[],
    )
    result = sync(db_path, cfg, data)
    assert result.rebuilt and result.ingested == [day(1)]
    [row] = _query(db_path, "SELECT full_name, topics, first_seen, state, last_snapshot FROM repos")
    assert row == ("Owner/A", '["x", "y"]', "2026-11-01", "active", "2026-11-01")
    assert _query(db_path, "SELECT language, node_id FROM trending_entries") == [("", "R_a")]


def test_second_sync_is_incremental(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], trending_rows=[trending("o/r_a")])
    sync(db_path, cfg, data)
    assert sync(db_path, cfg, data).ingested == []
    write_day(data, day(2), repos=[repo("R_a", 110)])
    result = sync(db_path, cfg, data)
    assert not result.rebuilt and result.ingested == [day(2)]


def test_trending_name_resolves_case_insensitively_from_known_repos(
    cfg: Config, paths: tuple[Path, Path]
) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100, full_name="Owner/A")], search_rows=[])
    write_day(data, day(2), trending_rows=[trending("owner/a")])
    sync(db_path, cfg, data)
    assert _query(db_path, "SELECT node_id FROM trending_entries") == [("R_a",)]


def test_unresolved_trending_name_is_kept_without_node(
    cfg: Config, paths: tuple[Path, Path]
) -> None:
    data, db_path = paths
    write_day(data, day(1), trending_rows=[trending("ghost/repo")])
    sync(db_path, cfg, data)
    assert _query(db_path, "SELECT full_name, node_id FROM trending_entries") == [
        ("ghost/repo", None)
    ]
    assert _query(db_path, "SELECT count(*) FROM repos") == [(0,)]


def test_search_hit_without_snapshot_still_tracks_repo(
    cfg: Config, paths: tuple[Path, Path]
) -> None:
    data, db_path = paths
    write_day(data, day(1), search_rows=[search("R_s", "s/s")])
    sync(db_path, cfg, data)
    assert _query(db_path, "SELECT full_name, state, last_snapshot FROM repos") == [
        ("s/s", "active", None)
    ]


def test_meta_updates_only_when_present(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 1, meta=meta(description="first"))])
    write_day(data, day(2), repos=[repo("R_a", 2)])
    sync(db_path, cfg, data)
    assert _query(db_path, "SELECT description FROM repos") == [("first",)]
    write_day(data, day(3), repos=[repo("R_a", 3, meta=meta(description="second"))])
    sync(db_path, cfg, data)
    assert _query(db_path, "SELECT description FROM repos") == [("second",)]


def test_goes_dormant_after_enough_quiet_days(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    for n, s in [(2, 104), (3, 108)]:
        write_day(data, day(n), repos=[repo("R_a", s)])
    sync(db_path, cfg, data)
    assert _state(db_path, "R_a") == ("active", "2026-11-01")
    write_day(data, day(4), repos=[repo("R_a", 112)])
    sync(db_path, cfg, data)
    assert _state(db_path, "R_a") == ("dormant", "2026-11-04")


def test_one_busy_day_prevents_dormancy(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    for n, s in [(2, 104), (3, 109), (4, 113)]:
        write_day(data, day(n), repos=[repo("R_a", s)])
    sync(db_path, cfg, data)
    assert _state(db_path, "R_a")[0] == "active"


def test_missing_day_prevents_dormancy(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    for n, s in [(3, 101), (4, 102)]:
        write_day(data, day(n), repos=[repo("R_a", s)])
    sync(db_path, cfg, data)
    assert _state(db_path, "R_a")[0] == "active"


def _make_dormant(data: Path) -> None:
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    for n in (2, 3, 4):
        write_day(data, day(n), repos=[repo("R_a", 100)])


@pytest.mark.parametrize(("stars", "expected"), [(119, "dormant"), (120, "active")])
def test_reactivation_threshold(
    cfg: Config, paths: tuple[Path, Path], stars: int, expected: str
) -> None:
    data, db_path = paths
    _make_dormant(data)
    write_day(data, day(11), repos=[repo("R_a", stars)])
    sync(db_path, cfg, data)
    assert _state(db_path, "R_a")[0] == expected


def test_appearance_reactivates_dormant(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    _make_dormant(data)
    write_day(data, day(5), trending_rows=[trending("o/r_a")], repos=[repo("R_a", 100)])
    sync(db_path, cfg, data)
    assert _state(db_path, "R_a") == ("active", "2026-11-05")


def test_gone_and_return(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    write_day(data, day(2), repos=[gone("R_a")])
    sync(db_path, cfg, data)
    assert _state(db_path, "R_a") == ("gone", "2026-11-02")
    write_day(data, day(3), search_rows=[search("R_a", "o/r_a")])
    sync(db_path, cfg, data)
    assert _state(db_path, "R_a") == ("active", "2026-11-03")


def test_alert_is_detected_during_ingest(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    for n, s in [(2, 110), (3, 120), (4, 420)]:
        write_day(data, day(n), repos=[repo("R_a", s)])
    sync(db_path, cfg, data)
    assert _query(db_path, "SELECT node_id, date, daily_stars, baseline FROM alerts") == [
        ("R_a", "2026-11-04", 300, 10.0)
    ]


def test_alert_is_redetected_after_backfill_arrives(cfg: Config, paths: tuple[Path, Path]) -> None:
    from starstrata.backfill import backfill_path
    from starstrata.snapshots import write_jsonl

    data, db_path = paths
    write_day(data, day(10), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    write_day(data, day(11), repos=[repo("R_a", 400)])
    sync(db_path, cfg, data)
    assert _query(db_path, "SELECT date, baseline FROM alerts") == [("2026-11-11", None)]
    # 首筆快照前 2、3 天各 +200，基準 200；日增 300 未達 3 倍。
    write_jsonl(
        backfill_path(data, "R_a"),
        [{"week": "2026-11-02", "days": [200, 200, 200, 200, 200, 200, 200]}],
    )
    sync(db_path, cfg, data)
    assert _query(db_path, "SELECT date, baseline FROM alerts") == []


@pytest.mark.parametrize(("first", "expected"), [(2, 10.0), (1, None)])
def test_alert_window_reaches_baseline_days_before_period(
    cfg: Config, paths: tuple[Path, Path], first: int, expected: float | None
) -> None:
    data, db_path = paths
    # baseline_days=2：day(7) 的期間開頭是 day(4)。缺快照時往前最多找到 day(2)，day(1) 超出範圍。
    write_day(data, day(first), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    write_day(data, day(6), repos=[repo("R_a", 140)], search_rows=[search("R_a", "o/r_a")])
    write_day(data, day(7), repos=[repo("R_a", 400)])
    sync(db_path, cfg, data)
    assert _query(db_path, "SELECT date, daily_stars, baseline FROM alerts") == [
        ("2026-11-07", 260, expected)
    ]


def test_earlier_days_are_frozen_and_latest_is_not(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    write_day(data, day(2), repos=[repo("R_a", 400)])
    write_day(data, day(3), repos=[repo("R_a", 401)])
    result = sync(db_path, cfg, data)
    assert read_jsonl(day_dir(data, day(2)) / ALERTS_FILE) == [
        {"node_id": "R_a", "daily_stars": 300, "baseline": None}
    ]
    assert read_jsonl(day_dir(data, day(1)) / ALERTS_FILE) == []
    assert not (day_dir(data, day(3)) / ALERTS_FILE).exists()
    assert _query(db_path, "SELECT value FROM meta WHERE key = ?", ALERTS_THROUGH) == [
        ("2026-11-03",)
    ]
    # 凍結後更新了雜湊，下次同步不會因為新檔案而重建。
    assert not sync(db_path, cfg, data).rebuilt and result.rebuilt


def test_latest_day_frozen_with_alerts_then_synced(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    write_day(data, day(2), repos=[repo("R_a", 400)])
    sync(db_path, cfg, data)
    conn = db.connect(db_path)
    try:
        with conn:
            freeze_alerts(conn, data, day(2))
    finally:
        conn.close()
    write_day(data, day(3), repos=[repo("R_a", 401)])
    result = sync(db_path, cfg, data)
    assert not result.rebuilt
    assert _query(db_path, "SELECT date, daily_stars FROM alerts") == [("2026-11-02", 300)]


def test_frozen_alerts_are_read_not_recomputed(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    write_day(data, day(2), repos=[repo("R_a", 101)])
    write_jsonl(
        day_dir(data, day(2)) / ALERTS_FILE, [{"node_id": "R_a", "daily_stars": 7, "baseline": 1.5}]
    )
    write_day(data, day(3), repos=[repo("R_a", 102)])
    sync(db_path, cfg, data)
    assert _query(db_path, "SELECT date, daily_stars, baseline FROM alerts") == [
        ("2026-11-02", 7, 1.5)
    ]


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ([{"node_id": "R_a", "daily_stars": 5}], "第 1 行格式不符"),
        ([{"node_id": "R_a", "daily_stars": "5", "baseline": None}], "第 1 行格式不符"),
        (
            [
                {"node_id": "R_a", "daily_stars": 5, "baseline": None},
                {"node_id": "R_a", "daily_stars": 6, "baseline": 1.0},
            ],
            "第 2 行重複的 node_id",
        ),
    ],
)
def test_malformed_frozen_alerts_are_rejected(
    cfg: Config, paths: tuple[Path, Path], rows: list[dict[str, object]], message: str
) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    write_jsonl(day_dir(data, day(1)) / ALERTS_FILE, rows)
    write_day(data, day(2), repos=[repo("R_a", 101)])
    with pytest.raises(AlertsFileError, match=message):
        sync(db_path, cfg, data)


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        (['{"node_id": "R_a"'], "第 1 行不是合法的 JSON"),
        (["", '["R_a", 5, null]'], "第 2 行不是 JSON 物件"),
        (['"R_a"'], "第 1 行不是 JSON 物件"),
        (['{"node_id": "R_a", "daily_stars": true, "baseline": null}'], "第 1 行格式不符"),
        (['{"node_id": "R_a", "daily_stars": 5, "baseline": "1.0"}'], "第 1 行格式不符"),
    ],
)
def test_unparseable_frozen_alerts_are_rejected(
    cfg: Config, paths: tuple[Path, Path], lines: list[str], message: str
) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    path = day_dir(data, day(1)) / ALERTS_FILE
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    write_day(data, day(2), repos=[repo("R_a", 101)])
    with pytest.raises(AlertsFileError, match=f"{ALERTS_FILE} {message}"):
        sync(db_path, cfg, data)


def test_frozen_alerts_of_unknown_repos_are_skipped(
    cfg: Config, paths: tuple[Path, Path], caplog: pytest.LogCaptureFixture
) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    write_jsonl(
        day_dir(data, day(1)) / ALERTS_FILE,
        [
            {"node_id": "R_a", "daily_stars": 5, "baseline": None},
            {"node_id": "R_x", "daily_stars": 9, "baseline": 2},
        ],
    )
    write_day(data, day(2), repos=[repo("R_a", 101)])
    sync(db_path, cfg, data)
    assert _query(db_path, "SELECT node_id, date FROM alerts") == [("R_a", "2026-11-01")]
    assert "R_x 不在追蹤的 repo 中" in caplog.text


def test_editing_frozen_alerts_triggers_rebuild(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    write_day(data, day(2), repos=[repo("R_a", 101)])
    sync(db_path, cfg, data)
    assert read_jsonl(day_dir(data, day(1)) / ALERTS_FILE) == []
    write_jsonl(
        day_dir(data, day(1)) / ALERTS_FILE,
        [{"node_id": "R_a", "daily_stars": 5, "baseline": None}],
    )
    result = sync(db_path, cfg, data)
    assert result.rebuilt and result.rebuild_reason == "2026-11-01 的快照已改變"
    assert _query(db_path, "SELECT date FROM alerts") == [("2026-11-01",)]


def test_interrupted_detection_resumes(
    cfg: Config, paths: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from starstrata import ingest

    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    sync(db_path, cfg, data)
    write_day(data, day(2), repos=[repo("R_a", 600)])
    write_day(data, day(3), repos=[repo("R_a", 601)])

    def interrupted(*args: object) -> None:
        raise RuntimeError("中斷")

    with monkeypatch.context() as m:
        m.setattr(ingest, "_sync_backfill", interrupted)
        with pytest.raises(RuntimeError):
            sync(db_path, cfg, data)
    result = sync(db_path, cfg, data)
    assert not result.rebuilt and result.ingested == []
    assert _query(db_path, "SELECT date, daily_stars FROM alerts") == [("2026-11-02", 500)]


def test_freeze_alerts_skips_unknown_or_frozen_days(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    write_day(data, day(2), repos=[repo("R_a", 400)])
    sync(db_path, cfg, data)
    conn = db.connect(db_path)
    try:
        with conn:
            freeze_alerts(conn, data, day(5))
            freeze_alerts(conn, data, day(2))
            conn.execute("DELETE FROM alerts")
            freeze_alerts(conn, data, day(2))
    finally:
        conn.close()
    assert not day_dir(data, day(5)).exists()
    assert read_jsonl(day_dir(data, day(2)) / ALERTS_FILE) == [
        {"node_id": "R_a", "daily_stars": 300, "baseline": None}
    ]


def test_due_ids(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    _make_dormant(data)
    write_day(
        data,
        day(1),
        repos=[repo("R_a", 100), repo("R_b", 5)],
        search_rows=[search("R_a", "o/r_a"), search("R_b", "o/r_b", rank=2)],
    )
    write_day(data, day(4), repos=[repo("R_a", 100), gone("R_b")])
    sync(db_path, cfg, data)
    conn = db.connect(db_path)
    try:
        assert tracking.due_ids(conn, cfg.tracking, day(10)) == []
        assert tracking.due_ids(conn, cfg.tracking, day(11)) == ["R_a"]
    finally:
        conn.close()


def test_known_meta_reflects_latest(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 1, meta=meta(topics=["a"])), repo("R_b", 1)])
    sync(db_path, cfg, data)
    conn = db.connect(db_path)
    try:
        known = db.known_meta(conn)
    finally:
        conn.close()
    assert set(known) == {"R_a"}
    assert known["R_a"].topics == ("a",)


def _history(data: Path) -> None:
    write_day(
        data,
        day(1),
        repos=[repo("R_a", 100, meta=meta())],
        search_rows=[search("R_a", "o/r_a")],
        trending_rows=[trending("o/r_a")],
    )
    for n, s in [(2, 104), (3, 108), (4, 500), (5, 504), (6, 505), (7, 506), (8, 507)]:
        write_day(data, day(n), repos=[repo("R_a", s)])
    write_day(data, day(9), search_rows=[search("R_a", "o/r_a")], repos=[repo("R_a", 600)])


def test_incremental_equals_rebuild(cfg: Config, paths: tuple[Path, Path], tmp_path: Path) -> None:
    data, db_path = paths
    _history(data)
    for n in range(1, 10):
        # 每次只讓一天可見，模擬每日執行。
        visible = tmp_path / "visible"
        for k in range(1, n + 1):
            src = data / f"{day(k):%Y/%m/%d}"
            dst = visible / f"{day(k):%Y/%m/%d}"
            if not dst.exists():
                dst.mkdir(parents=True)
                for f in src.iterdir():
                    (dst / f.name).write_bytes(f.read_bytes())
        sync(db_path, cfg, visible)
    incremental = _dump(db_path)
    rebuilt_path = tmp_path / "rebuilt.sqlite"
    sync(rebuilt_path, cfg, data, force_rebuild=True)
    assert _dump(rebuilt_path) == incremental


@pytest.fixture
def ingested(cfg: Config, paths: tuple[Path, Path]) -> Iterator[tuple[Path, Path]]:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    write_day(data, day(2), repos=[repo("R_a", 110)])
    sync(db_path, cfg, data)
    yield data, db_path


def test_changed_snapshot_triggers_rebuild(cfg: Config, ingested: tuple[Path, Path]) -> None:
    data, db_path = ingested
    write_day(data, day(2), repos=[repo("R_a", 999)])
    result = sync(db_path, cfg, data)
    assert result.rebuilt and result.rebuild_reason == "2026-11-02 的快照已改變"
    assert _query(db_path, "SELECT stars FROM repo_daily WHERE date = '2026-11-02'") == [(999,)]


def test_config_change_triggers_rebuild(cfg: Config, ingested: tuple[Path, Path]) -> None:
    data, db_path = ingested
    changed = replace(cfg, alerts=replace(cfg.alerts, min_daily_stars=1))
    result = sync(db_path, changed, data)
    assert result.rebuild_reason == "追蹤或爆紅設定已變更"


def test_unrelated_config_change_does_not_rebuild(cfg: Config, ingested: tuple[Path, Path]) -> None:
    data, db_path = ingested
    changed = replace(cfg, search=replace(cfg.search, min_stars=1))
    assert not sync(db_path, changed, data).rebuilt


def test_schema_version_mismatch_triggers_rebuild(cfg: Config, ingested: tuple[Path, Path]) -> None:
    data, db_path = ingested
    conn = db.connect(db_path)
    with conn:
        db.set_meta(conn, "schema_version", "0")
    conn.close()
    assert sync(db_path, cfg, data).rebuild_reason == "schema 版本不符"


def test_corrupt_db_triggers_rebuild(cfg: Config, ingested: tuple[Path, Path]) -> None:
    data, db_path = ingested
    db_path.write_bytes(b"not a database" * 100)
    result = sync(db_path, cfg, data)
    assert result.rebuilt and (result.rebuild_reason or "").startswith("DB 無法讀取")
    assert _query(db_path, "SELECT count(*) FROM ingested_dates") == [(2,)]


def test_backdated_snapshot_triggers_rebuild(cfg: Config, ingested: tuple[Path, Path]) -> None:
    data, db_path = ingested
    write_day(data, day(1) - timedelta(days=1), repos=[repo("R_a", 90)])
    assert sync(db_path, cfg, data).rebuild_reason == "出現比已匯入日期更早的新快照"


def test_deleted_snapshot_day_triggers_rebuild(cfg: Config, ingested: tuple[Path, Path]) -> None:
    data, db_path = ingested
    for f in (data / f"{day(2):%Y/%m/%d}").iterdir():
        f.unlink()
    assert sync(db_path, cfg, data).rebuild_reason == "2026-11-02 的快照已改變"


def test_failed_rebuild_keeps_previous_db(cfg: Config, ingested: tuple[Path, Path]) -> None:
    data, db_path = ingested
    before = db_path.read_bytes()
    write_day(data, day(2), repos=[{"node_id": "R_a"}])
    with pytest.raises(KeyError):
        sync(db_path, cfg, data)
    assert db_path.read_bytes() == before


def test_stale_search_hit_does_not_revive_repo_gone_that_day(
    cfg: Config, paths: tuple[Path, Path]
) -> None:
    data, db_path = paths
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    write_day(
        data,
        day(2),
        repos=[gone("R_a"), gone("R_ghost")],
        search_rows=[search("R_a", "o/r_a"), search("R_ghost", "x/ghost", rank=2)],
    )
    sync(db_path, cfg, data)
    assert _state(db_path, "R_a") == ("gone", "2026-11-02")
    assert _query(db_path, "SELECT count(*) FROM repos WHERE node_id = 'R_ghost'") == [(0,)]


def test_name_lookup_prefers_live_repo(cfg: Config, paths: tuple[Path, Path]) -> None:
    data, db_path = paths
    write_day(
        data,
        day(1),
        repos=[repo("R_old", 5, full_name="o/same")],
        search_rows=[search("R_old", "o/same")],
    )
    write_day(data, day(2), repos=[gone("R_old")])
    write_day(
        data,
        day(3),
        repos=[repo("R_new", 9, full_name="o/same")],
        search_rows=[search("R_new", "o/same")],
    )
    write_day(data, day(4), trending_rows=[trending("o/same")])
    sync(db_path, cfg, data)
    assert _query(db_path, "SELECT node_id FROM trending_entries") == [("R_new",)]
    assert _state(db_path, "R_old")[0] == "gone"


def _random_history(data: Path, seed: int) -> int:
    """產生隨機的多 repo 歷史。

    涵蓋缺日、gone 與再出現、改名、名稱大小寫不同、查不到的名稱、meta 變動與回補檔。
    """
    import random

    from starstrata.backfill import backfill_path
    from starstrata.snapshots import write_jsonl

    rng = random.Random(seed)
    ids = [f"R_{c}" for c in "abcdef"]
    stars = {i: rng.randint(0, 500) for i in ids}
    names = {i: f"o/{i.lower()}" for i in ids}
    alive = set(ids)
    days = rng.randint(12, 30)
    for n in range(1, days + 1):
        if rng.random() < 0.15:
            continue
        repos_rows: list[dict[str, object]] = []
        trending_rows: list[dict[str, object]] = []
        search_rows: list[dict[str, object]] = []
        for node_id in ids:
            if node_id not in alive:
                if rng.random() < 0.2:
                    alive.add(node_id)
                    search_rows.append(search(node_id, names[node_id], rank=len(search_rows) + 1))
                continue
            if rng.random() < 0.08:
                alive.discard(node_id)
                repos_rows.append(gone(node_id))
                continue
            if rng.random() < 0.1:
                names[node_id] = f"o/{node_id.lower()}-{n}"
            stars[node_id] += rng.choice([0, 1, 3, 8, 40, 250])
            extra = {"meta": meta(description=f"d{n}")} if rng.random() < 0.2 else {}
            repos_rows.append(repo(node_id, stars[node_id], full_name=names[node_id], **extra))
            if rng.random() < 0.3:
                shown = names[node_id].upper() if rng.random() < 0.3 else names[node_id]
                trending_rows.append(trending(shown, rank=len(trending_rows) + 1))
            if rng.random() < 0.2:
                search_rows.append(search(node_id, names[node_id], rank=len(search_rows) + 1))
        if rng.random() < 0.2:
            trending_rows.append(trending("ghost/unknown", rank=len(trending_rows) + 1))
        write_day(
            data,
            day(n),
            repos=repos_rows,
            trending_rows=trending_rows,
            search_rows=search_rows,
        )
    # 回補涵蓋首筆快照前幾週，日增夠大才會影響警示基準。
    for node_id in rng.sample(ids, 3):
        write_jsonl(
            backfill_path(data, node_id),
            [
                {"week": week, "days": [rng.choice([0, 40, 300]) for _ in range(7)]}
                for week in ("2026-10-18", "2026-10-25")
            ],
        )
    return days


@pytest.mark.parametrize("seed", range(25))
def test_random_histories_incremental_equals_rebuild(
    cfg: Config, tmp_path: Path, seed: int
) -> None:
    import random
    import shutil

    data = tmp_path / "data"
    _random_history(data, seed)
    visible = tmp_path / "visible"
    incremental = tmp_path / "incremental.sqlite"
    folders = sorted(p for p in data.glob("[0-9]*/*/*") if p.is_dir())
    # 回補檔在隨機的一天才出現，模擬回補比快照晚完成。
    backfill_at = random.Random(seed).randrange(len(folders) + 1)
    for i, folder in enumerate(folders):
        if i == backfill_at:
            shutil.copytree(data / "backfill", visible / "backfill")
        shutil.copytree(folder, visible / folder.relative_to(data))
        result = sync(incremental, cfg, visible)
        assert not result.rebuilt or result.rebuild_reason == "DB 不存在"
    if backfill_at == len(folders):
        shutil.copytree(data / "backfill", visible / "backfill")
        sync(incremental, cfg, visible)
    rebuilt = tmp_path / "rebuilt.sqlite"
    sync(rebuilt, cfg, visible, force_rebuild=True)
    assert _dump(rebuilt) == _dump(incremental)


def test_alert_baseline_window_covers_baseline_days_plus_one(
    cfg: Config, paths: tuple[Path, Path]
) -> None:
    data, db_path = paths
    narrow = replace(
        cfg,
        tracking=replace(cfg.tracking, dormant_after_days=2),
        alerts=AlertsConfig(baseline_days=4, multiplier=3.0, min_daily_stars=100, cooldown_days=7),
    )
    write_day(data, day(1), repos=[repo("R_a", 100)], search_rows=[search("R_a", "o/r_a")])
    for n, s in [(2, 110), (3, 130), (4, 140), (5, 160), (6, 500)]:
        write_day(data, day(n), repos=[repo("R_a", s)], search_rows=[search("R_a", "o/r_a")])
    sync(db_path, narrow, data)
    assert _query(db_path, "SELECT date, daily_stars, baseline FROM alerts") == [
        ("2026-11-06", 340, 15.0)
    ]
