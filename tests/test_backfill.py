from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from starstrata import db
from starstrata.backfill import backfill_path, candidates, run_backfill
from starstrata.config import BackfillConfig, Config, load_config
from starstrata.github.star_history import PER_PAGE
from starstrata.ingest import sync
from starstrata.snapshots import read_jsonl
from tests.conftest import FakeGitHub, history_weeks
from tests.snapshot_builder import gone, meta, repo, search, write_day

TODAY = date(2026, 11, 22)
REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


@pytest.fixture
def cfg() -> Config:
    return load_config(REPO_CONFIG)


@pytest.fixture
def setup(cfg: Config, tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "data"
    recent = meta(created_at="2026-10-01T00:00:00Z")
    write_day(
        data,
        date(2026, 11, 1),
        repos=[
            repo("R_a", 10, full_name="o/a", meta=recent),
            repo("R_b", 10, full_name="o/b", meta=recent),
        ],
        search_rows=[search("R_a", "o/a"), search("R_b", "o/b", rank=2)],
    )
    write_day(
        data,
        date(2026, 11, 2),
        repos=[repo("R_c", 10, full_name="o/c", meta=recent), gone("R_b")],
        search_rows=[search("R_c", "o/c")],
    )
    db_path = tmp_path / "db.sqlite"
    sync(db_path, cfg, data)
    return data, db_path


def _run(fake: FakeGitHub, db_path: Path, data: Path, budget: int) -> Any:
    conn = db.connect(db_path)
    try:
        return run_backfill(
            fake.client(), conn, BackfillConfig(daily_request_budget=budget), data, TODAY
        )
    finally:
        conn.close()


def test_candidates_skip_gone_and_follow_first_seen(setup: tuple[Path, Path]) -> None:
    _, db_path = setup
    conn = db.connect(db_path)
    try:
        assert [r["node_id"] for r in candidates(conn)] == ["R_a", "R_c"]
    finally:
        conn.close()


def test_backfill_writes_weekly_file_and_ingests(
    fake_github: FakeGitHub, setup: tuple[Path, Path], cfg: Config
) -> None:
    data, db_path = setup
    fake_github.histories["o/a"] = history_weeks(
        "2026-11-15", [[0, 0, 0, 0, 0, 0, 4], [0] * 7, [1, 0, 0, 0, 0, 0, 0]]
    )
    fake_github.histories["o/c"] = []
    result = _run(fake_github, db_path, data, budget=10)
    assert result.done == ["R_a", "R_c"] and result.requests == 2 and result.stopped is None
    assert read_jsonl(backfill_path(data, "R_a")) == [
        {"week": "2026-11-01", "days": [1, 0, 0, 0, 0, 0, 0]},
        {"week": "2026-11-15", "days": [0, 0, 0, 0, 0, 0, 4]},
    ]
    assert read_jsonl(backfill_path(data, "R_c")) == []

    sync(db_path, cfg, data)
    conn = db.connect(db_path)
    try:
        rows = [tuple(r) for r in conn.execute("SELECT * FROM star_backfill ORDER BY date")]
        assert rows == [("R_a", "2026-11-01", 1), ("R_a", "2026-11-21", 4)]
        assert candidates(conn) == []
    finally:
        conn.close()


def test_budget_stops_before_large_repo(fake_github: FakeGitHub, setup: tuple[Path, Path]) -> None:
    data, db_path = setup
    fake_github.histories["o/a"] = history_weeks("2026-11-15", [[1] * 7])
    result = _run(fake_github, db_path, data, budget=0)
    assert result.done == [] and result.stopped == "預算不足"
    assert fake_github.requests == []


def test_history_longer_than_estimate_is_discarded(
    fake_github: FakeGitHub, setup: tuple[Path, Path]
) -> None:
    data, db_path = setup
    fake_github.histories["o/a"] = history_weeks("2026-11-15", [[1] * 7] * (PER_PAGE * 3))
    result = _run(fake_github, db_path, data, budget=2)
    assert result.done == [] and result.stopped == "預算不足" and result.requests == 2
    assert not backfill_path(data, "R_a").exists()


def test_retries_count_toward_budget(fake_github: FakeGitHub, setup: tuple[Path, Path]) -> None:
    data, db_path = setup
    fake_github.histories["o/a"] = history_weeks("2026-11-15", [[1] * 7])
    fake_github.history_failures["o/a"] = 1
    result = _run(fake_github, db_path, data, budget=1)
    assert result.done == [] and result.stopped == "預算不足" and result.requests == 2
    assert not backfill_path(data, "R_a").exists()


def test_retries_within_budget_are_kept(fake_github: FakeGitHub, setup: tuple[Path, Path]) -> None:
    data, db_path = setup
    fake_github.histories["o/a"] = history_weeks("2026-11-15", [[1] * 7])
    fake_github.history_failures["o/a"] = 1
    result = _run(fake_github, db_path, data, budget=2)
    assert result.done == ["R_a"] and result.requests == 2


def test_failed_repo_is_skipped(fake_github: FakeGitHub, setup: tuple[Path, Path]) -> None:
    data, db_path = setup
    fake_github.histories["o/c"] = history_weeks("2026-11-15", [[1] * 7])
    result = _run(fake_github, db_path, data, budget=10)
    assert result.failed == ["R_a"] and result.done == ["R_c"]
    assert result.requests == 2


def test_rate_limit_stops(fake_github: FakeGitHub, setup: tuple[Path, Path]) -> None:
    data, db_path = setup
    fake_github.history_status["o/a"] = 403
    result = _run(fake_github, db_path, data, budget=10)
    assert result.rate_limited and result.done == []
    assert len(fake_github.requests) == 1


def test_existing_file_is_not_fetched_again(
    fake_github: FakeGitHub, setup: tuple[Path, Path]
) -> None:
    data, db_path = setup
    path = backfill_path(data, "R_a")
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    fake_github.histories["o/c"] = []
    result = _run(fake_github, db_path, data, budget=10)
    assert result.done == ["R_c"]


def test_changed_and_removed_backfill_files_resync(setup: tuple[Path, Path], cfg: Config) -> None:
    data, db_path = setup
    path = backfill_path(data, "R_a")
    path.parent.mkdir(parents=True)
    path.write_text('{"days":[1,0,0,0,0,0,0],"week":"2026-11-01"}\n', encoding="utf-8")
    sync(db_path, cfg, data)
    path.write_text('{"days":[2,0,0,0,0,0,0],"week":"2026-11-01"}\n', encoding="utf-8")
    sync(db_path, cfg, data)

    def rows() -> list[tuple[Any, ...]]:
        conn = db.connect(db_path)
        try:
            return [tuple(r) for r in conn.execute("SELECT * FROM star_backfill")]
        finally:
            conn.close()

    assert rows() == [("R_a", "2026-11-01", 2)]
    path.unlink()
    sync(db_path, cfg, data)
    assert rows() == []


def test_unexpected_history_response_skips_repo(
    fake_github: FakeGitHub, setup: tuple[Path, Path]
) -> None:
    data, db_path = setup
    fake_github.histories["o/a"] = {}  # type: ignore[assignment]
    fake_github.histories["o/c"] = []
    result = _run(fake_github, db_path, data, budget=10)
    assert result.failed == ["R_a"] and result.done == ["R_c"]
    assert not backfill_path(data, "R_a").exists()


def test_invalid_backfill_filename_is_ignored(setup: tuple[Path, Path], cfg: Config) -> None:
    data, db_path = setup
    (data / "backfill").mkdir(parents=True, exist_ok=True)
    (data / "backfill" / "R_a.jsonl").write_text(
        '{"days":[1,0,0,0,0,0,0],"week":"2026-11-01"}\n', encoding="utf-8"
    )
    sync(db_path, cfg, data)
    conn = db.connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM star_backfill").fetchone()[0] == 0
    finally:
        conn.close()
