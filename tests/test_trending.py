from __future__ import annotations

from pathlib import Path

import pytest

from starstrata.github.trending import (
    TrendingParseError,
    fetch_trending,
    parse_trending,
    trending_path,
)
from tests.conftest import FakeGitHub, trending_html


def test_trending_path() -> None:
    assert trending_path(None) == "/trending"
    assert trending_path("python") == "/trending/python"
    assert trending_path("c++") == "/trending/c%2B%2B"
    assert trending_path("c#") == "/trending/c%23"


def test_rows_are_ranked_in_page_order() -> None:
    html = trending_html([("a/one", "1,234 stars today"), ("b/two", "5 stars today")])
    entries = parse_trending(html, period="daily", language="python")
    assert [(e.rank, e.full_name, e.stars_in_period) for e in entries] == [
        (1, "a/one", 1234),
        (2, "b/two", 5),
    ]
    assert all(e.period == "daily" and e.language == "python" for e in entries)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1 star today", 1),
        ("320 stars this week", 320),
        ("12,345 stars this month", 12345),
    ],
)
def test_stars_in_period_variants(text: str, expected: int) -> None:
    [entry] = parse_trending(trending_html([("a/b", text)]), period="weekly", language=None)
    assert entry.stars_in_period == expected


def test_missing_stars_text_gives_none() -> None:
    [entry] = parse_trending(trending_html([("a/b", None)]), period="daily", language=None)
    assert entry.stars_in_period is None


def test_empty_page_gives_no_entries() -> None:
    assert parse_trending(trending_html([]), period="daily", language=None) == []


def test_row_without_link_is_a_parse_error() -> None:
    html = '<article class="Box-row"><h2>no link</h2></article>'
    with pytest.raises(TrendingParseError, match="第 1 列找不到 repo 連結"):
        parse_trending(html, period="daily", language=None)


def test_row_with_non_repo_link_is_a_parse_error() -> None:
    html = '<article class="Box-row"><h2><a href="/sponsors/x/y">x</a></h2></article>'
    with pytest.raises(TrendingParseError, match="不是 repo"):
        parse_trending(html, period="daily", language=None)


def test_fetch_requests_language_and_period(fake_github: FakeGitHub) -> None:
    fake_github.trending_pages["/trending/rust?since=weekly"] = trending_html(
        [("a/b", "3 stars this week")]
    )
    entries = fetch_trending(fake_github.client(), language="rust", period="weekly")
    assert [e.full_name for e in entries] == ["a/b"]


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "period", "count", "first"),
    [
        ("trending_overall_daily.html", "daily", 16, ("elementalsouls/Claude-BugHunter", 1721)),
        ("trending_rust_weekly.html", "weekly", 7, ("vercel-labs/agent-browser", 2193)),
    ],
)
def test_real_trending_pages(name: str, period: str, count: int, first: tuple[str, int]) -> None:
    entries = parse_trending(_fixture(name), period=period, language=None)
    assert len(entries) == count
    assert (entries[0].full_name, entries[0].stars_in_period) == first
    assert [e.rank for e in entries] == list(range(1, count + 1))
    assert len({e.full_name for e in entries}) == count
    assert all(e.stars_in_period is not None for e in entries)
