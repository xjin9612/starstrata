"""用 Search API 找最近建立、star 數高的 repo。"""

from __future__ import annotations

from datetime import date, timedelta

from starstrata.config import SEARCH_MAX_RESULTS, SearchConfig
from starstrata.github.http import GitHubClient, GitHubError
from starstrata.snapshots import SearchHit

_PER_PAGE_MAX = 100


def build_query(cfg: SearchConfig, today: date) -> str:
    since = today - timedelta(days=cfg.created_within_days)
    return f"created:>={since.isoformat()} stars:>={cfg.min_stars}"


def search_new_repos(client: GitHubClient, cfg: SearchConfig, today: date) -> list[SearchHit]:
    """依 star 數由高到低取前 cfg.limit 個。翻頁期間排序變動造成的重複 repo 只保留第一次出現。"""
    query = build_query(cfg, today)
    per_page = min(_PER_PAGE_MAX, cfg.limit)
    hits: list[SearchHit] = []
    seen: set[str] = set()
    page = 1
    while len(hits) < cfg.limit:
        body = client.rest_get(
            "/search/repositories",
            {"q": query, "sort": "stars", "order": "desc", "per_page": per_page, "page": page},
        )
        items = body.get("items") if isinstance(body, dict) else None
        if not isinstance(items, list):
            raise GitHubError("Search 回應格式不符")
        for item in items:
            node_id = item["node_id"]
            if node_id in seen:
                continue
            seen.add(node_id)
            hits.append(SearchHit(rank=len(hits) + 1, node_id=node_id, full_name=item["full_name"]))
            if len(hits) == cfg.limit:
                break
        # Search API 只提供前 1000 筆，再翻頁會回 422。
        if len(items) < per_page or page * per_page >= SEARCH_MAX_RESULTS:
            break
        page += 1
    return hits
