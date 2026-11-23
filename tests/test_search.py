from __future__ import annotations

from datetime import date

from starstrata.config import SearchConfig
from starstrata.github.search import build_query, search_new_repos
from tests.conftest import FakeGitHub


def _items(n: int, start: int = 0) -> list[dict[str, str]]:
    return [{"node_id": f"R_{i}", "full_name": f"o/r{i}"} for i in range(start, start + n)]


def test_query_uses_creation_window_and_min_stars() -> None:
    cfg = SearchConfig(created_within_days=30, min_stars=100, limit=10)
    assert build_query(cfg, date(2026, 11, 22)) == "created:>=2026-10-23 stars:>=100"


def test_results_are_ranked_in_order(fake_github: FakeGitHub) -> None:
    fake_github.search_items = _items(3)
    cfg = SearchConfig(created_within_days=30, min_stars=100, limit=10)
    hits = search_new_repos(fake_github.client(), cfg, date(2026, 11, 22))
    assert [(h.rank, h.node_id, h.full_name) for h in hits] == [
        (1, "R_0", "o/r0"),
        (2, "R_1", "o/r1"),
        (3, "R_2", "o/r2"),
    ]
    params = fake_github.requests[0].url.params
    assert params["sort"] == "stars"
    assert params["order"] == "desc"


def test_pagination_stops_at_limit(fake_github: FakeGitHub) -> None:
    fake_github.search_items = _items(250)
    cfg = SearchConfig(created_within_days=30, min_stars=0, limit=150)
    hits = search_new_repos(fake_github.client(), cfg, date(2026, 11, 22))
    assert len(hits) == 150
    assert hits[-1].node_id == "R_149"
    assert [r.url.params["page"] for r in fake_github.requests] == ["1", "2"]


def test_small_limit_uses_small_page(fake_github: FakeGitHub) -> None:
    fake_github.search_items = _items(50)
    cfg = SearchConfig(created_within_days=30, min_stars=0, limit=5)
    hits = search_new_repos(fake_github.client(), cfg, date(2026, 11, 22))
    assert len(hits) == 5
    assert fake_github.requests[0].url.params["per_page"] == "5"
    assert len(fake_github.requests) == 1


def test_short_last_page_ends_pagination(fake_github: FakeGitHub) -> None:
    fake_github.search_items = _items(130)
    cfg = SearchConfig(created_within_days=30, min_stars=0, limit=1000)
    hits = search_new_repos(fake_github.client(), cfg, date(2026, 11, 22))
    assert len(hits) == 130
    assert len(fake_github.requests) == 2


def test_duplicates_across_pages_keep_first(fake_github: FakeGitHub) -> None:
    fake_github.search_items = [*_items(100), *_items(1, start=99), *_items(2, start=100)]
    cfg = SearchConfig(created_within_days=30, min_stars=0, limit=1000)
    hits = search_new_repos(fake_github.client(), cfg, date(2026, 11, 22))
    assert [h.node_id for h in hits[98:]] == ["R_98", "R_99", "R_100", "R_101"]
    assert [h.rank for h in hits[98:]] == [99, 100, 101, 102]


def test_pagination_never_requests_beyond_1000_results(fake_github: FakeGitHub) -> None:
    fake_github.search_items = [*_items(100), *_items(1, start=99), *_items(1100, start=100)]
    cfg = SearchConfig(created_within_days=30, min_stars=0, limit=1000)
    hits = search_new_repos(fake_github.client(), cfg, date(2026, 11, 22))
    assert len(hits) == 999
    assert max(int(r.url.params["page"]) for r in fake_github.requests) == 10
