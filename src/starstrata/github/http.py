"""GitHub HTTP client：認證、重試與 API 額度處理。"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

API_BASE = "https://api.github.com"
WEB_BASE = "https://github.com"
API_VERSION = "2026-03-10"
USER_AGENT = "starstrata"

# 5xx 與連線錯誤的重試次數；間隔依序為 1、2、4 秒。
MAX_RETRIES = 3

# secondary rate limit 的 Retry-After 不超過此秒數才等待重試，超過就停止該資料來源。
MAX_RETRY_AFTER_SECONDS = 60

_TIMEOUT = httpx.Timeout(30.0)


class GitHubError(Exception):
    """GitHub 請求失敗：4xx，或 5xx 與連線錯誤重試後仍失敗。"""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        # HTTP 狀態碼；連線失敗時為 None。
        self.status = status


class RateLimitExceeded(GitHubError):
    """API 額度用盡。呼叫端應停止該資料來源，保留已取得的資料。"""


class GraphQLError(GitHubError):
    """GraphQL 回應含有 NOT_FOUND 以外的錯誤。"""


@dataclass(frozen=True)
class GraphQLResult:
    data: dict[str, Any]
    # 每個 NOT_FOUND 錯誤的 path，例如 ("r0",) 或 ("nodes", 3)。
    not_found_paths: tuple[tuple[str | int, ...], ...]
    # NOT_FOUND 以外、只影響單一欄位的錯誤：path → 錯誤訊息。
    field_errors: dict[tuple[str | int, ...], str]

    def error_under(self, *prefix: str | int) -> str | None:
        """path 以 prefix 開頭的第一個欄位錯誤。"""
        for path, message in self.field_errors.items():
            if path[: len(prefix)] == prefix:
                return message
        return None


class GitHubClient:
    """呼叫 GitHub 的唯一入口。token 只放在 API 請求的 header，不送到 github.com 網頁。"""

    def __init__(
        self,
        token: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        api_headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": API_VERSION,
            # 舊 repo 預設回傳舊格式的 node ID；此 header 讓所有 repo 一律回傳 R_ 開頭的新格式。
            "X-Github-Next-Global-ID": "1",
        }
        if token:
            api_headers["Authorization"] = f"Bearer {token}"
        # GitHub 官方建議 REST 用戶端跟隨轉址回應。
        self._api = httpx.Client(
            base_url=API_BASE,
            headers=api_headers,
            timeout=_TIMEOUT,
            transport=transport,
            follow_redirects=True,
        )
        self._web = httpx.Client(
            base_url=WEB_BASE,
            headers={"User-Agent": USER_AGENT},
            timeout=_TIMEOUT,
            transport=transport,
            follow_redirects=True,
        )
        self._sleep = sleep
        self.rate_remaining: dict[str, int] = {}
        # 實際送出的 HTTP 請求數，含重試。
        self.request_count = 0

    def close(self) -> None:
        self._api.close()
        self._web.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_html(self, path: str, params: Mapping[str, str] | None = None) -> str:
        return self._request(self._web, "GET", path, params=params).text

    def rest_get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        return _json(self._request(self._api, "GET", path, params=params), f"GET {path}")

    def get_readme(self, full_name: str) -> str | None:
        """repo README 的原始文字；repo 沒有 README 時回傳 None。"""
        try:
            response = self._request(
                self._api,
                "GET",
                f"/repos/{full_name}/readme",
                headers={"Accept": "application/vnd.github.raw+json"},
            )
        except GitHubError as e:
            if e.status != 404 or isinstance(e, RateLimitExceeded):
                raise
            return None
        return response.text

    def graphql(self, query: str, variables: Mapping[str, Any] | None = None) -> GraphQLResult:
        """執行 GraphQL 查詢。

        有 path 的錯誤只影響該欄位，欄位為 null：NOT_FOUND 列在 not_found_paths，
        其他列在 field_errors。沒有 path 的錯誤或沒有 data 時丟出 GraphQLError。
        """
        payload: dict[str, Any] = {"query": query, "variables": dict(variables or {})}
        body = _json(self._request(self._api, "POST", "/graphql", json=payload), "GraphQL")
        if not isinstance(body, dict):
            raise GraphQLError("GraphQL 回應格式不符")
        errors: list[dict[str, Any]] = body.get("errors") or []
        if any(e.get("type") == "RATE_LIMITED" for e in errors):
            raise RateLimitExceeded("GraphQL 額度用盡")
        data = body.get("data")
        global_errors = [e for e in errors if not e.get("path")]
        if global_errors or not isinstance(data, dict):
            shown = global_errors if isinstance(data, dict) else errors
            messages = "; ".join(str(e.get("message")) for e in shown) or "回應沒有 data"
            raise GraphQLError(f"GraphQL 錯誤：{messages}")
        not_found = tuple(tuple(e["path"]) for e in errors if e.get("type") == "NOT_FOUND")
        field_errors = {
            tuple(e["path"]): f"{e.get('type')}: {e.get('message')}"
            for e in errors
            if e.get("type") != "NOT_FOUND"
        }
        return GraphQLResult(data=data, not_found_paths=not_found, field_errors=field_errors)

    def _request(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        attempt = 0
        while True:
            self.request_count += 1
            try:
                response = client.request(method, path, params=params, json=json, headers=headers)
            except httpx.TransportError as e:
                if attempt >= MAX_RETRIES:
                    raise GitHubError(f"{method} {path} 連線失敗：{e}") from e
                self._backoff(attempt)
                attempt += 1
                continue
            except httpx.RequestError as e:
                raise GitHubError(f"{method} {path} 請求失敗：{type(e).__name__}") from e

            self._record_rate_limit(response)
            status = response.status_code
            if status in (403, 429):
                retry_after = self._retry_after(response)
                if response.headers.get("x-ratelimit-remaining") == "0" or (
                    retry_after is None and "secondary rate limit" in response.text.lower()
                ):
                    raise RateLimitExceeded(f"{method} {path} API 額度用盡", status)
                if retry_after is not None:
                    if retry_after > MAX_RETRY_AFTER_SECONDS or attempt >= MAX_RETRIES:
                        raise RateLimitExceeded(
                            f"{method} {path} 觸發 secondary rate limit，需等待 {retry_after} 秒",
                            status,
                        )
                    self._sleep(retry_after)
                    attempt += 1
                    continue
            if status >= 500:
                if attempt >= MAX_RETRIES:
                    raise GitHubError(f"{method} {path} 回應 {status}", status)
                self._backoff(attempt)
                attempt += 1
                continue
            if status >= 400:
                raise GitHubError(f"{method} {path} 回應 {status}：{response.text[:200]}", status)
            return response

    def _backoff(self, attempt: int) -> None:
        self._sleep(2**attempt)

    def _record_rate_limit(self, response: httpx.Response) -> None:
        resource = response.headers.get("x-ratelimit-resource")
        remaining = response.headers.get("x-ratelimit-remaining")
        if resource and remaining and remaining.isdigit():
            self.rate_remaining[resource] = int(remaining)

    @staticmethod
    def _retry_after(response: httpx.Response) -> int | None:
        value = response.headers.get("retry-after")
        return int(value) if value and value.isdigit() else None


def _json(response: httpx.Response, label: str) -> Any:
    try:
        return response.json()
    except ValueError as e:
        raise GitHubError(f"{label} 回應不是 JSON", response.status_code) from e
