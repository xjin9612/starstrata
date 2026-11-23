from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from starstrata.cli import EXIT_USAGE, main
from tests.snapshot_builder import repo, search, write_day

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


def _args(tmp_path: Path, *rest: str) -> list[str]:
    return [
        "--config",
        str(REPO_CONFIG),
        "--data",
        str(tmp_path / "data"),
        "--db",
        str(tmp_path / "db.sqlite"),
        *rest,
    ]


def test_collect_requires_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GH_PAT", raising=False)
    assert main(_args(tmp_path, "collect")) == EXIT_USAGE
    assert not (tmp_path / "db.sqlite").exists()


def test_invalid_config_is_usage_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text("[schedule\n", encoding="utf-8")
    assert main(["--config", str(bad), "ingest"]) == EXIT_USAGE


def test_ingest_then_rebuild(tmp_path: Path) -> None:
    write_day(
        tmp_path / "data",
        date(2026, 11, 1),
        repos=[repo("R_a", 1)],
        search_rows=[search("R_a", "o/r_a")],
    )
    assert main(_args(tmp_path, "ingest")) == 0
    assert (tmp_path / "db.sqlite").is_file()
    assert main(_args(tmp_path, "rebuild")) == 0


def test_collect_runs_even_when_ingest_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import starstrata.cli as cli

    monkeypatch.setenv("GH_PAT", "token")
    calls: list[dict[str, object]] = []

    def broken_sync(*args: object, **kwargs: object) -> None:
        raise KeyError("full_name")

    def fake_collect(*args: object, **kwargs: object) -> object:
        calls.append(kwargs)
        return type("R", (), {"ok": True, "run": {"sources": {}}})()

    monkeypatch.setattr(cli, "sync", broken_sync)
    monkeypatch.setattr(cli, "collect", fake_collect)
    assert main(_args(tmp_path, "collect")) == cli.EXIT_PARTIAL
    assert len(calls) == 1 and calls[0]["due_ids"] == []
