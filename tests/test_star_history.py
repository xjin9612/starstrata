from __future__ import annotations

from datetime import date, timedelta

from starstrata.github.star_history import (
    PER_PAGE,
    Week,
    estimate_requests,
    expand,
    fetch_history,
)
from tests.conftest import FakeGitHub, history_weeks

EMPTY = [0] * 7


def test_estimate_requests() -> None:
    today = date(2026, 11, 22)
    assert estimate_requests(None, today) == 1
    assert estimate_requests("2026-11-20T00:00:00Z", today) == 1
    assert estimate_requests("2026-04-01T00:00:00Z", today) == 2
    assert estimate_requests("2011-09-04T22:48:12Z", today) == 27
    assert estimate_requests("1900-01-01T00:00:00Z", today) == 100


def test_estimate_requests_adds_two_weeks() -> None:
    today = date(2026, 11, 22)
    # 202 天是 28 週，加 2 週剛好一頁；203 天是 29 週，加 2 週超過一頁。
    assert PER_PAGE == 30
    assert estimate_requests(f"{today - timedelta(days=202)}T00:00:00Z", today) == 1
    assert estimate_requests(f"{today - timedelta(days=203)}T00:00:00Z", today) == 2


def test_single_page_history(fake_github: FakeGitHub) -> None:
    fake_github.histories["o/r"] = history_weeks(
        "2026-11-22", [[1, 0, 0, 0, 0, 0, 0], EMPTY, [0, 2, 0, 0, 0, 0, 3]]
    )
    history = fetch_history(fake_github.client(), "o/r", max_requests=10)
    assert history.requests == 1
    assert history.weeks == [
        Week(start=date(2026, 11, 8), days=(0, 2, 0, 0, 0, 0, 3)),
        Week(start=date(2026, 11, 22), days=(1, 0, 0, 0, 0, 0, 0)),
    ]
    params = fake_github.requests[0].url.params
    assert (params["per_page"], params["page"]) == (str(PER_PAGE), "1")


def test_pagination_until_short_page(fake_github: FakeGitHub) -> None:
    fake_github.histories["o/r"] = history_weeks("2026-11-22", [[1] * 7] * (PER_PAGE + 5))
    history = fetch_history(fake_github.client(), "o/r", max_requests=10)
    assert history.requests == 2
    assert history.weeks is not None and len(history.weeks) == PER_PAGE + 5


def test_exact_page_multiple_needs_one_empty_page(fake_github: FakeGitHub) -> None:
    fake_github.histories["o/r"] = history_weeks("2026-11-22", [[1] * 7] * PER_PAGE)
    history = fetch_history(fake_github.client(), "o/r", max_requests=10)
    assert history.requests == 2
    assert history.weeks is not None and len(history.weeks) == PER_PAGE


def test_budget_exhausted_returns_none(fake_github: FakeGitHub) -> None:
    fake_github.histories["o/r"] = history_weeks("2026-11-22", [[1] * 7] * (PER_PAGE * 3))
    history = fetch_history(fake_github.client(), "o/r", max_requests=2)
    assert history.weeks is None
    assert history.requests == 2


def test_expand_skips_zero_days() -> None:
    week = Week(start=date(2026, 11, 22), days=(0, 5, 0, 0, 0, 0, 1))
    assert expand(week) == [(date(2026, 11, 23), 5), (date(2026, 11, 28), 1)]


def test_week_repeated_across_pages_is_kept_once(fake_github: FakeGitHub) -> None:
    from datetime import UTC, datetime

    first_page = history_weeks("2026-11-22", [[1] * 7] * PER_PAGE)
    # 翻頁期間跨入新的一週：第二頁開頭重複第一頁最後一週，而且數值不同。
    repeated = dict(first_page[-1], days=[9] * 7)
    rest = history_weeks("2026-11-22", [[2] * 7] * (PER_PAGE + 3))[PER_PAGE:]
    fake_github.histories["o/r"] = [*first_page, repeated, *rest]
    history = fetch_history(fake_github.client(), "o/r", max_requests=10)
    assert history.weeks is not None and len(history.weeks) == PER_PAGE + 3
    repeated_start = datetime.fromtimestamp(first_page[-1]["week"], UTC).date()
    [kept] = [w for w in history.weeks if w.start == repeated_start]
    assert kept.days == (1,) * 7
