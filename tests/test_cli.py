from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from starstrata.cli import EXIT_USAGE, UsageError, main, thread_id
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [("", None), ("  ", None), ("1", 1), ("01", 1), ("42", 42), (" 42 ", 42)],
)
def test_thread_id(value: str, expected: int | None) -> None:
    assert thread_id(value) == expected


@pytest.mark.parametrize("value", ["abc", "0", "-5", "+5", "4_2", "４２", "4 2"])
def test_invalid_thread_id(value: str) -> None:
    with pytest.raises(UsageError, match="TELEGRAM_THREAD_ID"):
        thread_id(value)


def test_notify_with_invalid_thread_id_sends_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starstrata import cli

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("不應同步 DB 或建立 TelegramSender")

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    monkeypatch.setenv("TELEGRAM_THREAD_ID", "abc")
    monkeypatch.setattr(cli, "sync", fail)
    monkeypatch.setattr(cli, "TelegramSender", fail)
    assert main(_args(tmp_path, "notify")) == EXIT_USAGE
    assert not (tmp_path / "db.sqlite").exists()


def test_notify_passes_thread_id_to_sender(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from starstrata import cli

    created: list[int | None] = []

    class FakeSender:
        def __init__(self, token: str, chat_id: str, *, thread_id: int | None = None) -> None:
            created.append(thread_id)

        def __enter__(self) -> FakeSender:
            return self

        def __exit__(self, *exc: object) -> None:
            pass

        def send(self, text: str, **kwargs: object) -> None:
            pass

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    monkeypatch.setenv("TELEGRAM_THREAD_ID", "42")
    monkeypatch.setattr(cli, "TelegramSender", FakeSender)
    assert main(_args(tmp_path, "notify", "--date", "2026-11-24")) == 0
    assert created == [42]


def _english_config(tmp_path: Path) -> Path:
    text = REPO_CONFIG.read_text(encoding="utf-8")
    assert 'locale = "zh-TW"' in text
    path = tmp_path / "config-en.toml"
    path.write_text(text.replace('locale = "zh-TW"', 'locale = "en"'), encoding="utf-8")
    return path


def test_notify_uses_configured_locale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from starstrata import cli

    texts: list[str] = []

    class FakeSender:
        def __init__(self, token: str, chat_id: str, *, thread_id: int | None = None) -> None:
            pass

        def __enter__(self) -> FakeSender:
            return self

        def __exit__(self, *exc: object) -> None:
            pass

        def send(self, text: str, **kwargs: object) -> None:
            texts.append(text)

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    monkeypatch.delenv("TELEGRAM_THREAD_ID", raising=False)
    monkeypatch.setattr(cli, "TelegramSender", FakeSender)
    args = _args(tmp_path, "notify", "--date", "2026-11-24")
    args[1] = str(_english_config(tmp_path))
    assert main(args) == 0
    assert texts == [
        "<b>⭐ starstrata daily summary</b>\n2026-11-24\n\n"
        "No snapshots were imported today. Check the daily workflow."
    ]


def test_report_uses_configured_locale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GH_PAT", "GEMINI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    write_day(tmp_path / "data", date(2026, 11, 18), repos=[repo("R_a", 10)])
    args = _args(tmp_path, "report", "weekly", "--date", "2026-11-25")
    args[1] = str(_english_config(tmp_path))
    assert main([*args, "--reports", str(tmp_path / "reports")]) == 0
    text = (tmp_path / "reports" / "weekly" / "2026-W47.md").read_text(encoding="utf-8")
    assert text.startswith("# starstrata weekly report 2026-W47 (2026-11-16 to 2026-11-22)\n")
