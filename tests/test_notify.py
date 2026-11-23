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
from starstrata.notify import (
    Message,
    SummaryData,
    alert_message,
    format_alert,
    notify,
    short_count,
    summary_message,
)
from starstrata.queries import AlertLine, RepoLine
from starstrata.telegram import Button
from starstrata.translate import text_key
from tests.snapshot_builder import meta, repo, search, trending, write_day

D1 = date(2026, 11, 1)
REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"
NOTIFY = NotifyConfig(trending_top=4, top_gainers=2, site_url="https://example.com/s/")


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


Sent = list[Message]


def test_summary_and_alert_are_sent_once(world: tuple[Any, Path]) -> None:
    conn, data = world
    sent: Sent = []
    result = notify(conn, NOTIFY, day(4), data, sent.append)
    assert result.summary_sent and result.alerts_sent == ["R_a"]
    assert [m.silent for m in sent] == [True, False]
    assert sent[0].buttons == (Button("開啟 dashboard", "https://example.com/s/"),)
    assert sent[1].buttons == (
        Button("GitHub", "https://github.com/o/a"),
        Button("詳細頁", "https://example.com/s/#/repo/R_a"),
    )
    summary, alert = (m.text for m in sent)
    assert summary == "\n".join(
        [
            "<b>⭐ starstrata 每日摘要</b>",
            "2026-11-04 · 追蹤 3 個 repo",
            "",
            "<b>📈 今日新增最多</b>",
            '🥇 <a href="https://github.com/new/c">new/c</a> 🆕',
            "      <b>+10</b> 今日 · Python · ★9",
            "      desc",
            "",
            "<b>🚀 日增最多</b>（追蹤中的 repo）",
            '1. <a href="https://github.com/o/a">o/a</a> 🔥  <b>+400</b> · ★520',
            '2. <a href="https://github.com/o/b">o/b</a>  <b>+1</b> · ★57',
            "",
            "新上榜 1 · 爆紅 1",
        ]
    )
    assert "日增 ★+400，近期平均 +10.0/日" in alert
    assert "A &lt;tool&gt; &amp; more" in alert

    log = json.loads((data / "2026/11/04/notified.json").read_text(encoding="utf-8"))
    assert log == {"summary": True, "alerts": ["R_a"]}
    again: Sent = []
    result2 = notify(conn, NOTIFY, day(4), data, again.append)
    assert again == [] and not result2.summary_sent and result2.alerts_sent == []


def test_trending_is_ordered_by_gain(cfg: Config, tmp_path: Path) -> None:
    data = tmp_path / "data"
    names = ["o/r1", "o/r2", "o/r3", "o/r4", "o/r5"]
    gains = [5, None, 300, 1_234, 40]
    write_day(
        data,
        D1,
        repos=[
            repo(
                f"R_{i}", 1_500_000 if i == 0 else 10, full_name=n, meta=meta(description="x" * 80)
            )
            for i, n in enumerate(names[:4])
        ],
        trending_rows=[
            trending(n, i + 1, stars=g) for i, (n, g) in enumerate(zip(names, gains, strict=True))
        ],
    )
    sync(tmp_path / "db.sqlite", cfg, data)
    conn = db.connect(tmp_path / "db.sqlite")
    try:
        sent: Sent = []
        notify(conn, NOTIFY, D1, data, sent.append)
    finally:
        conn.close()
    lines = sent[0].text.split("\n")
    section = lines[lines.index("<b>📈 今日新增最多</b>") + 1 :]
    assert section[0] == '🥇 <a href="https://github.com/o/r4">o/r4</a>'
    assert section[2] == "      " + "x" * 49 + "…"
    assert section[3].startswith("🥈") and section[6].startswith("🥉")
    assert section[6:9] == [
        '🥉 <a href="https://github.com/o/r5">o/r5</a>',
        "      <b>+40</b> 今日",
        '4. <a href="https://github.com/o/r1">o/r1</a>  <b>+5</b>',
    ]
    assert section[9] == "      Python · ★1.5M"
    assert "o/r2" not in sent[0].text


def test_translations_replace_descriptions(world: tuple[Any, Path]) -> None:
    conn, data = world
    sent: Sent = []
    translations = {text_key("desc"): "說明", text_key("A <tool> & more"): "工具 <與> 更多"}
    notify(conn, NOTIFY, day(4), data, sent.append, translations=translations)
    summary, alert = (m.text for m in sent)
    assert "      說明" in summary.split("\n")
    assert "工具 &lt;與&gt; 更多" in alert


def test_failure_mid_way_does_not_resend(world: tuple[Any, Path]) -> None:
    conn, data = world
    calls: list[str] = []

    def flaky(message: Message) -> None:
        calls.append(message.text)
        if len(calls) == 2:
            raise RuntimeError("network")

    with pytest.raises(RuntimeError):
        notify(conn, NOTIFY, day(4), data, flaky)
    retry: Sent = []
    notify(conn, NOTIFY, day(4), data, retry.append)
    assert len(retry) == 1 and "爆紅警示" in retry[0].text


def test_day_without_snapshots_reports_missing(world: tuple[Any, Path]) -> None:
    conn, data = world
    sent: Sent = []
    notify(conn, NOTIFY, day(9), data, sent.append)
    assert [m.text for m in sent] == [
        "<b>⭐ starstrata 每日摘要</b>\n2026-11-09\n\n今天沒有匯入任何快照，請檢查 daily workflow。"
    ]
    assert sent[0].silent


def test_first_day_has_no_new_marks_or_counts(world: tuple[Any, Path]) -> None:
    conn, data = world
    sent: Sent = []
    notify(conn, NOTIFY, day(1), data, sent.append)
    text = sent[0].text
    assert "首日建立基準，新上榜與日增明天開始計算。" in text
    assert "🆕" not in text and "新上榜 " not in text
    assert "日增最多" not in text and "今日新增最多" not in text
    assert text.endswith("\n\n爆紅 0")


def test_summary_without_site_url_has_no_link() -> None:
    data = SummaryData(
        active=0,
        first_day=False,
        trending=[],
        gainers=[],
        new_ids=frozenset(),
        alert_ids=frozenset(),
    )
    message = summary_message(replace(NOTIFY, site_url=""), D1, data)
    assert message.buttons == ()
    assert message.text.endswith("新上榜 0 · 爆紅 0")


def test_alert_buttons_encode_names() -> None:
    line = RepoLine("R_a/b+c", "o/a b", None, None, 1)
    message = alert_message(NOTIFY, D1, AlertLine(line, 100, None))
    assert message.buttons == (
        Button("GitHub", "https://github.com/o/a%20b"),
        Button("詳細頁", "https://example.com/s/#/repo/R_a%2Fb%2Bc"),
    )
    no_site = alert_message(replace(NOTIFY, site_url=""), D1, AlertLine(line, 100, None))
    assert [b.text for b in no_site.buttons] == ["GitHub"]


@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (999, "999"),
        (1_000, "1k"),
        (19_262, "19.3k"),
        (999_949, "999.9k"),
        (999_950, "1M"),
        (1_250_000, "1.2M"),
    ],
)
def test_short_count(n: int, expected: str) -> None:
    assert short_count(n) == expected


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
    first: Sent = []
    notify(conn, NOTIFY, day(5), data, first.append)
    assert "今天沒有匯入任何快照" in first[0].text
    write_day(data, day(5), repos=[repo("R_a", 530, full_name="o/a")])
    sync(tmp_path / "db.sqlite", cfg, data)
    conn2 = db.connect(tmp_path / "db.sqlite")
    try:
        second: Sent = []
        result = notify(conn2, NOTIFY, day(5), data, second.append)
    finally:
        conn2.close()
    assert result.summary_sent and "今天沒有匯入任何快照" not in second[0].text
    third: Sent = []
    conn3 = db.connect(tmp_path / "db.sqlite")
    try:
        notify(conn3, NOTIFY, day(5), data, third.append)
    finally:
        conn3.close()
    assert third == []
