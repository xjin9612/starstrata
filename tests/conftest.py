from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from starstrata.github.http import GitHubClient

TEST_TOKEN = "test-token-value"


def repo_node(
    node_id: str,
    full_name: str,
    *,
    stars: int = 100,
    forks: int = 10,
    watchers: int = 5,
    open_issues: int = 3,
    description: str | None = "desc",
    topics: Sequence[str] = (),
    language: str | None = "Python",
    archived: bool = False,
    created_at: str = "2026-01-01T00:00:00Z",
) -> dict[str, Any]:
    """GraphQL 回傳的 Repository 物件。"""
    return {
        "id": node_id,
        "nameWithOwner": full_name,
        "stargazerCount": stars,
        "forkCount": forks,
        "watchers": {"totalCount": watchers},
        "issues": {"totalCount": open_issues},
        "description": description,
        "repositoryTopics": {"nodes": [{"topic": {"name": t}} for t in topics]},
        "primaryLanguage": {"name": language} if language else None,
        "isArchived": archived,
        "createdAt": created_at,
    }


def trending_html(rows: Sequence[tuple[str, str | None]]) -> str:
    """產生與 Trending 頁面相同結構的 HTML。rows 為 (owner/name, 期間 star 文字)。"""
    articles = []
    for full_name, stars_text in rows:
        stars = (
            f'<span class="d-inline-block float-sm-right">{stars_text}</span>' if stars_text else ""
        )
        articles.append(
            f'<article class="Box-row"><h2 class="h3 lh-condensed">'
            f'<a href="/{full_name}" class="Link">{full_name.replace("/", " / ")}</a></h2>'
            f"<p>description</p><div>{stars}</div></article>"
        )
    return f"<html><body><div class='Box'>{''.join(articles)}</div></body></html>"


@dataclass
class FakeGitHub:
    """模擬 github.com 與 api.github.com。"""

    # Trending 路徑（含 since 參數）→ HTML；沒有設定的路徑回傳空頁面。
    trending_pages: dict[str, str] = field(default_factory=dict)
    trending_status: dict[str, int] = field(default_factory=dict)
    search_items: list[dict[str, Any]] = field(default_factory=list)
    search_status: int | None = None
    repos: list[dict[str, Any]] = field(default_factory=list)
    # 第幾次（從 1 起算）GraphQL 呼叫開始回傳額度用盡。
    graphql_rate_limited_from: int | None = None
    # 這些 node ID 以 node 層級的 FORBIDDEN 錯誤回應。
    forbidden_ids: set[str] = field(default_factory=set)
    # 這些 node ID 因子欄位逾時而為 null。
    subfield_error_ids: set[str] = field(default_factory=set)
    # 這些 node ID 為 null，但回應沒有任何錯誤。
    silent_null_ids: set[str] = field(default_factory=set)
    # 第幾次（從 1 起算）GraphQL 呼叫 → 回應的 HTTP 狀態碼。
    graphql_status_on_call: dict[int, int] = field(default_factory=dict)
    # full_name → star history API 的週資料，由新到舊。
    histories: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    history_status: dict[str, int] = field(default_factory=dict)
    # full_name → 成功之前先回幾次 502。
    history_failures: dict[str, int] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    sleeps: list[float] = field(default_factory=list)
    graphql_calls: int = 0

    def client(self) -> GitHubClient:
        return GitHubClient(
            TEST_TOKEN, transport=httpx.MockTransport(self.handle), sleep=self.sleeps.append
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = request.url
        if url.host == "github.com" and url.path.startswith("/trending"):
            key = f"{url.path}?since={url.params.get('since')}"
            if key in self.trending_status:
                return httpx.Response(self.trending_status[key])
            return httpx.Response(200, text=self.trending_pages.get(key, trending_html([])))
        if url.host == "api.github.com" and url.path == "/search/repositories":
            if self.search_status is not None:
                return httpx.Response(self.search_status, json={"message": "error"})
            page, per_page = int(url.params["page"]), int(url.params["per_page"])
            if (page - 1) * per_page >= 1000:
                return httpx.Response(422, json={"message": "Only the first 1000 results"})
            items = self.search_items[(page - 1) * per_page : page * per_page]
            return httpx.Response(200, json={"total_count": len(self.search_items), "items": items})
        if url.host == "api.github.com" and url.path.endswith("/stargazers/history"):
            full_name = url.path.removeprefix("/repos/").removesuffix("/stargazers/history")
            if full_name in self.history_status:
                status = self.history_status[full_name]
                headers = {"x-ratelimit-remaining": "0"} if status == 403 else {}
                return httpx.Response(status, json={"message": "x"}, headers=headers)
            if self.history_failures.get(full_name, 0) > 0:
                self.history_failures[full_name] -= 1
                return httpx.Response(502, json={"message": "x"})
            if full_name not in self.histories:
                return httpx.Response(404, json={"message": "Not Found"})
            page, per_page = int(url.params["page"]), int(url.params["per_page"])
            weeks = self.histories[full_name][(page - 1) * per_page : page * per_page]
            return httpx.Response(200, json=weeks)
        if url.host == "api.github.com" and url.path == "/graphql":
            return self._graphql(json.loads(request.content))
        return httpx.Response(404)

    def _graphql(self, body: dict[str, Any]) -> httpx.Response:
        self.graphql_calls += 1
        if self.graphql_calls in self.graphql_status_on_call:
            return httpx.Response(self.graphql_status_on_call[self.graphql_calls])
        limit_from = self.graphql_rate_limited_from
        if limit_from is not None and self.graphql_calls >= limit_from:
            return httpx.Response(
                200, json={"errors": [{"type": "RATE_LIMITED", "message": "limit"}]}
            )
        variables: dict[str, Any] = body["variables"]
        by_id = {r["id"]: r for r in self.repos}
        by_name = {r["nameWithOwner"].lower(): r for r in self.repos}
        errors: list[dict[str, Any]] = []
        data: dict[str, Any] = {}
        if "nodes(ids:" in body["query"]:
            nodes = []
            for i, node_id in enumerate(variables["ids"]):
                node = by_id.get(node_id)
                if node_id in self.forbidden_ids:
                    node = None
                    errors.append({"type": "FORBIDDEN", "path": ["nodes", i], "message": "dmca"})
                elif node_id in self.subfield_error_ids:
                    node = None
                    errors.append(
                        {
                            "type": "SERVICE_UNAVAILABLE",
                            "path": ["nodes", i, "issues"],
                            "message": "timedout",
                        }
                    )
                elif node_id in self.silent_null_ids:
                    node = None
                elif node is None:
                    errors.append({"type": "NOT_FOUND", "path": ["nodes", i], "message": "nf"})
                nodes.append(node)
            data["nodes"] = nodes
        else:
            i = 0
            while f"o{i}" in variables:
                node = by_name.get(f"{variables[f'o{i}']}/{variables[f'n{i}']}".lower())
                if node is None:
                    errors.append({"type": "NOT_FOUND", "path": [f"r{i}"], "message": "nf"})
                data[f"r{i}"] = node
                i += 1
        payload: dict[str, Any] = {"data": data}
        if errors:
            payload["errors"] = errors
        return httpx.Response(200, json=payload)


@pytest.fixture
def fake_github() -> FakeGitHub:
    return FakeGitHub()


def history_weeks(newest_sunday: str, counts: list[list[int]]) -> list[dict[str, Any]]:
    """star history API 的回應：counts 由新到舊，每週 7 個數字。"""
    from datetime import UTC, datetime, timedelta

    start = datetime.fromisoformat(newest_sunday).replace(tzinfo=UTC)
    return [
        {
            "week": int((start - timedelta(weeks=i)).timestamp()),
            "total": sum(days),
            "days": days,
        }
        for i, days in enumerate(counts)
    ]
