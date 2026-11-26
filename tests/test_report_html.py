"""報告轉換器的測試。輸入以 reports.generate() 實際產生的 Markdown 為主。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest

from starstrata import db
from starstrata.config import load_config
from starstrata.ingest import sync
from starstrata.messages import EN, ZH_TW, Messages
from starstrata.report_html import title, to_html, top_repos
from starstrata.reports import (
    WEEKLY,
    Interpretation,
    Interpreter,
    Period,
    generate,
    model_label,
)
from tests.snapshot_builder import meta, repo, search, write_day

REPO_ROOT = Path(__file__).resolve().parent.parent
# 包含第一個匯入日 11-15，摘要會有首日加註。
BASELINE_WEEK = Period(WEEKLY, date(2026, 11, 9), date(2026, 11, 15))
NEXT_WEEK = Period(WEEKLY, date(2026, 11, 16), date(2026, 11, 22))
MODEL = model_label(["gemini-3.5-flash-lite", "gemini-3.5-flash"], ZH_TW)
HOSTILE = "看 <script>alert(1)</script> 與 `x` 和 [o/a](https://evil.example)"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    write_day(
        data,
        date(2026, 11, 15),
        repos=[repo("R_a", 100, full_name="o/a", meta=meta(description="A | pipe"))],
        search_rows=[search("R_a", "o/a")],
    )
    write_day(data, date(2026, 11, 18), repos=[repo("R_a", 150, full_name="o/a")])
    write_day(
        data,
        date(2026, 11, 20),
        repos=[
            repo("R_a", 400, full_name="o/a"),
            repo("R_b", 50, full_name="o/b", meta=meta(language="Rust")),
        ],
        search_rows=[search("R_b", "o/b")],
    )
    write_day(
        data,
        date(2026, 11, 22),
        repos=[repo("R_a", 410, full_name="o/a"), repo("R_b", 90, full_name="o/b")],
    )
    path = tmp_path / "db.sqlite"
    sync(path, load_config(REPO_ROOT / "config.toml"), data)
    return path


def _hostile(system: str, prompt: str, expected: Sequence[str]) -> dict[str, Interpretation]:
    return {name: Interpretation(what=HOSTILE, why="因為\n熱門") for name in expected}


def _render(
    db_path: Path,
    period: Period,
    interpreter: Interpreter | None = _hostile,
    messages: Messages = ZH_TW,
) -> str:
    conn = db.connect(db_path)
    try:
        text, _ = generate(
            conn,
            period,
            llm_top_n=2,
            model=MODEL,
            base_lookback_days=7,
            interpreter=interpreter,
            fetch_readme=lambda name: "",
            messages=messages,
        )
    finally:
        conn.close()
    return text


def test_structure_title_and_top_repos(db_path: Path) -> None:
    md = _render(db_path, NEXT_WEEK)
    html = to_html(md)
    assert title(md) == NEXT_WEEK.title(ZH_TW)
    assert "<h1" not in html and NEXT_WEEK.title(ZH_TW) not in html
    assert re.findall(r"<h3>(.*?)</h3>", html) == [
        "摘要",
        "Star 增加最多",
        "重點專案解讀",
        "新上榜",
        "爆紅警示",
    ]
    assert top_repos(md) == ["o/a", "o/b"]
    assert '<a href="#/repo/o/a"><span class="own">o/</span>a</a>' in html
    assert '<th scope="col" class="num">#</th>' in html
    assert '<td class="num">+310</td>' in html


def test_english_report_structure_and_top_repos(db_path: Path) -> None:
    md = _render(db_path, NEXT_WEEK, messages=EN)
    assert title(md) == "starstrata weekly report 2026-W47 (2026-11-16 to 2026-11-22)"
    assert re.findall(r"<h3>(.*?)</h3>", to_html(md)) == [
        "Summary",
        "Most stars gained",
        "Highlights",
        "New repos",
        "Spike alerts",
    ]
    assert top_repos(md) == ["o/a", "o/b"]


def test_model_label_becomes_inline_code(db_path: Path) -> None:
    html = to_html(_render(db_path, NEXT_WEEK))
    assert "<code>gemini-3.5-flash-lite</code>" in html
    assert "<code>gemini-3.5-flash</code>" in html
    assert "`gemini" not in html


def test_baseline_note_is_kept(db_path: Path) -> None:
    html = to_html(_render(db_path, BASELINE_WEEK))
    assert "<li>本期新上榜：0 個（不含 2026-11-15 首日建立基準時收錄的 repo）</li>" in html


def test_interpretation_stays_plain_text(db_path: Path) -> None:
    html = to_html(_render(db_path, NEXT_WEEK))
    assert "<script" not in html and "evil.example" not in html
    assert "&lt;script&gt;" in html
    # 解讀經過 reports._plain，反引號已被跳脫，不會變成 code。
    assert "<code>x</code>" not in html and "`x`" in html


def test_report_without_interpretation(db_path: Path) -> None:
    html = to_html(_render(db_path, NEXT_WEEK, interpreter=None))
    assert "<blockquote>未產生解讀：未設定 AI 解讀的 API key</blockquote>" in html


def test_report_without_new_repos_or_alerts(db_path: Path) -> None:
    html = to_html(_render(db_path, BASELINE_WEEK))
    assert "<p>本期沒有新上榜的 repo。</p>" in html
    assert "<p>本期沒有爆紅警示。</p>" in html


def test_escaped_pipe_stays_in_cell() -> None:
    html = to_html("| a | b |\n|---|---|\n| x \\| y | z |\n")
    assert "<td>x | y</td><td>z</td>" in html


@pytest.mark.parametrize(
    ("md", "expected"),
    [
        ("[x](javascript:alert(1))", "<p>[x](javascript:alert(1))</p>"),
        ("[o/a](https://evil.example/o/a)", "<p>o/a</p>"),
        ("[o/b](https://github.com/o/a)", "<p>o/b</p>"),
        ("[<b>x</b>](https://evil.example)", "<p>&lt;b&gt;x&lt;/b&gt;</p>"),
        ("<img src=x onerror=alert(1)>", "<p>&lt;img src=x onerror=alert(1)&gt;</p>"),
        ('`<b onclick="x">`', "<p><code>&lt;b onclick=&quot;x&quot;&gt;</code></p>"),
    ],
)
def test_hostile_markdown_stays_text(md: str, expected: str) -> None:
    assert to_html(md) == expected


def test_top_repos_without_section() -> None:
    assert top_repos("# t\n\n## 摘要\n\n- x\n") == []
    assert title("## 摘要\n") is None
