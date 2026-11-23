from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from starstrata import db
from starstrata.config import AlertsConfig, Config, NotifyConfig, load_config
from starstrata.ingest import sync
from starstrata.notify import format_alert, format_summary, notify
from starstrata.queries import AlertLine, RepoLine
from tests.snapshot_builder import meta, repo, search, trending, write_day

D1 = date(2026, 11, 1)
REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"
NOTIFY = NotifyConfig(top_gainers=2, new_repos_limit=1, site_url="https://example.com/s/")


def day(n: int) -> date:
    return D1 + timedelta(days=n - 1)


@pytest.fixture
def cfg() -> Config:
    base = load_config(REPO_CONFIG)
    return replace(
        base,
        alerts=AlertsConfig(baseline_days=2, multiplier=3.0, min_daily_stars=100, cooldown_days=7),
        notify=NOTIFY,
    )


@pytest.fixture
def world(cfg: Config, tmp_path: Path) -> Iterator[tuple[Any, Path]]:
    data = tmp_path / "data"
    write_day(
        data,
        day(1),
        repos=[
            repo("R_a", 100, full_name="o/a", meta=meta(description="A <tool> & more")),
            repo("R_b", 50, full_name="o/b", meta=meta(language=None)),
        ],
        search_rows=[search("R_a", "o/a"), search("R_b", "o/b", rank=2)],
    )
    write_day(
        data, day(2), repos=[repo("R_a", 110, full_name="o/a"), repo("R_b", 55, full_name="o/b")]
    )
    write_day(
        data, day(3), repos=[repo("R_a", 120, full_name="o/a"), repo("R_b", 56, full_name="o/b")]
    )
    write_day(
        data,
        day(4),
        repos=[
            repo("R_a", 520, full_name="o/a"),
            repo("R_b", 57, full_name="o/b"),
            repo("R_c", 9, full_name="new/c", meta=meta()),
        ],
        trending_rows=[trending("new/c")],
    )
    db_path = tmp_path / "db.sqlite"
    sync(db_path, cfg, data)
    conn = db.connect(db_path)
    yield conn, data
    conn.close()


def test_summary_and_alert_are_sent_once(world: tuple[Any, Path]) -> None:
    conn, data = world
    sent: list[str] = []
    result = notify(conn, NOTIFY, day(4), data, sent.append)
    assert result.summary_sent and result.alerts_sent == ["R_a"]
    assert len(sent) == 2
    summary, alert = sent
    assert "<b>starstrata 每日摘要 2026-11-04</b>" in summary
    assert '<a href="https://github.com/new/c">new/c</a> ★9' in summary
    assert '1. <a href="https://github.com/o/a">o/a</a> +400（★520）' in summary
    assert '2. <a href="https://github.com/o/b">o/b</a> +1（★57）' in summary
    assert "<b>爆紅警示</b>：1 則，另發訊息" in summary
    assert '<a href="https://example.com/s/">開啟 dashboard</a>' in summary
    assert "日增 ★+400，近期平均 +10.0/日" in alert
    assert "A &lt;tool&gt; &amp; more" in alert

    log = json.loads((data / "2026/11/04/notified.json").read_text(encoding="utf-8"))
    assert log == {"summary": True, "alerts": ["R_a"]}
    again: list[str] = []
    result2 = notify(conn, NOTIFY, day(4), data, again.append)
    assert again == [] and not result2.summary_sent and result2.alerts_sent == []


def test_failure_mid_way_does_not_resend(world: tuple[Any, Path]) -> None:
    conn, data = world
    calls: list[str] = []

    def flaky(text: str) -> None:
        calls.append(text)
        if len(calls) == 2:
            raise RuntimeError("network")

    with pytest.raises(RuntimeError):
        notify(conn, NOTIFY, day(4), data, flaky)
    retry: list[str] = []
    notify(conn, NOTIFY, day(4), data, retry.append)
    assert len(retry) == 1 and "爆紅警示" in retry[0]


def test_day_without_snapshots_reports_missing(world: tuple[Any, Path]) -> None:
    conn, data = world
    sent: list[str] = []
    notify(conn, NOTIFY, day(9), data, sent.append)
    assert sent == [
        "<b>starstrata 每日摘要 2026-11-09</b>\n\n今天沒有匯入任何快照，請檢查 daily workflow。"
    ]


def test_new_repo_list_is_truncated(world: tuple[Any, Path]) -> None:
    conn, data = world
    sent: list[str] = []
    notify(conn, NOTIFY, day(1), data, sent.append)
    assert "<b>新上榜</b>（共 2 個）" in sent[0]
    assert "…另有 1 個" in sent[0]
    assert "<b>日增 star 前 2</b>\n（無）" in sent[0]


def test_summary_without_site_url_has_no_link() -> None:
    text = format_summary(
        replace(NOTIFY, site_url=""), D1, ingested=True, new=[], gainers=[], alert_count=0
    )
    assert "dashboard" not in text
    assert text.endswith("<b>爆紅警示</b>：0 則")


def test_repo_names_are_escaped() -> None:
    line = RepoLine("R", 'evil/"x"<y>', None, None, 1)
    text = format_alert(D1, AlertLine(line, 100, None))
    assert '"x"' not in text.split("\n")[1].split(">")[0]
    assert "&lt;y&gt;" in text
    assert "基準資料不足" in text


def test_missing_day_reminder_does_not_block_real_summary(
    world: tuple[Any, Path], cfg: Config, tmp_path: Path
) -> None:
    conn, data = world
    first: list[str] = []
    notify(conn, NOTIFY, day(5), data, first.append)
    assert "今天沒有匯入任何快照" in first[0]
    write_day(data, day(5), repos=[repo("R_a", 530, full_name="o/a")])
    sync(tmp_path / "db.sqlite", cfg, data)
    conn2 = db.connect(tmp_path / "db.sqlite")
    try:
        second: list[str] = []
        result = notify(conn2, NOTIFY, day(5), data, second.append)
    finally:
        conn2.close()
    assert result.summary_sent and "今天沒有匯入任何快照" not in second[0]
    third: list[str] = []
    conn3 = db.connect(tmp_path / "db.sqlite")
    try:
        notify(conn3, NOTIFY, day(5), data, third.append)
    finally:
        conn3.close()
    assert third == []
