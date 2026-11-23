from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from starstrata.github.http import (
    API_VERSION,
    MAX_RETRIES,
    GitHubClient,
    GitHubError,
    GraphQLError,
    RateLimitExceeded,
)
from tests.conftest import TEST_TOKEN


def _client(
    handler: Callable[[httpx.Request], httpx.Response], sleeps: list[float] | None = None
) -> GitHubClient:
    record = sleeps if sleeps is not None else []
    return GitHubClient(TEST_TOKEN, transport=httpx.MockTransport(handler), sleep=record.append)


def _sequence(*responses: httpx.Response | Exception) -> Callable[[httpx.Request], httpx.Response]:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return handler


def test_api_requests_carry_auth_and_version_headers() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    _client(handler).rest_get("/rate_limit")
    headers = seen[0].headers
    assert headers["authorization"] == f"Bearer {TEST_TOKEN}"
    assert headers["x-github-api-version"] == API_VERSION
    assert headers["x-github-next-global-id"] == "1"


def test_web_requests_do_not_send_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="<html></html>")

    _client(handler).get_html("/trending", {"since": "daily"})
    assert seen[0].url.host == "github.com"
    assert "authorization" not in seen[0].headers


def test_client_without_token_sends_no_authorization() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    GitHubClient(None, transport=httpx.MockTransport(handler)).rest_get("/rate_limit")
    assert "authorization" not in seen[0].headers


def test_server_error_is_retried_with_exponential_backoff() -> None:
    sleeps: list[float] = []
    client = _client(
        _sequence(httpx.Response(502), httpx.Response(503), httpx.Response(200, json={"a": 1})),
        sleeps,
    )
    assert client.rest_get("/x") == {"a": 1}
    assert sleeps == [1, 2]


def test_server_error_gives_up_after_max_retries() -> None:
    sleeps: list[float] = []
    client = _client(_sequence(*[httpx.Response(500)] * (MAX_RETRIES + 1)), sleeps)
    with pytest.raises(GitHubError, match="回應 500"):
        client.rest_get("/x")
    assert sleeps == [1, 2, 4]


def test_transport_error_is_retried() -> None:
    client = _client(_sequence(httpx.ConnectError("boom"), httpx.Response(200, json={})))
    assert client.rest_get("/x") == {}


def test_transport_error_gives_up_after_max_retries() -> None:
    client = _client(_sequence(*[httpx.ConnectError("boom")] * (MAX_RETRIES + 1)))
    with pytest.raises(GitHubError, match="連線失敗"):
        client.rest_get("/x")


def test_exhausted_primary_rate_limit_is_not_retried() -> None:
    sleeps: list[float] = []
    response = httpx.Response(403, headers={"x-ratelimit-remaining": "0"})
    client = _client(_sequence(response), sleeps)
    with pytest.raises(RateLimitExceeded):
        client.rest_get("/x")
    assert sleeps == []


def test_short_retry_after_is_waited_then_retried() -> None:
    sleeps: list[float] = []
    client = _client(
        _sequence(httpx.Response(429, headers={"retry-after": "5"}), httpx.Response(200, json={})),
        sleeps,
    )
    assert client.rest_get("/x") == {}
    assert sleeps == [5]


def test_retry_after_at_limit_is_waited() -> None:
    sleeps: list[float] = []
    client = _client(
        _sequence(httpx.Response(429, headers={"retry-after": "60"}), httpx.Response(200, json={})),
        sleeps,
    )
    client.rest_get("/x")
    assert sleeps == [60]


def test_retry_after_over_limit_stops() -> None:
    sleeps: list[float] = []
    client = _client(_sequence(httpx.Response(429, headers={"retry-after": "61"})), sleeps)
    with pytest.raises(RateLimitExceeded, match="61"):
        client.rest_get("/x")
    assert sleeps == []


def test_secondary_rate_limit_without_retry_after_stops() -> None:
    response = httpx.Response(403, text="You have exceeded a secondary rate limit.")
    with pytest.raises(RateLimitExceeded):
        _client(_sequence(response)).rest_get("/x")


def test_plain_forbidden_is_a_github_error() -> None:
    with pytest.raises(GitHubError, match="回應 403") as info:
        _client(_sequence(httpx.Response(403, text="Forbidden"))).rest_get("/x")
    assert not isinstance(info.value, RateLimitExceeded)


def test_client_error_is_not_retried_and_hides_token() -> None:
    sleeps: list[float] = []
    client = _client(_sequence(httpx.Response(401, text="Bad credentials")), sleeps)
    with pytest.raises(GitHubError) as info:
        client.rest_get("/x")
    assert sleeps == []
    assert TEST_TOKEN not in str(info.value)


def test_rate_limit_headers_are_recorded() -> None:
    response = httpx.Response(
        200, json={}, headers={"x-ratelimit-resource": "graphql", "x-ratelimit-remaining": "4321"}
    )
    client = _client(_sequence(response))
    client.rest_get("/x")
    assert client.rate_remaining == {"graphql": 4321}


def test_graphql_not_found_is_returned_as_paths() -> None:
    body = {
        "data": {"r0": None, "r1": {"id": "R_1"}},
        "errors": [{"type": "NOT_FOUND", "path": ["r0"], "message": "nf"}],
    }
    result = _client(_sequence(httpx.Response(200, json=body))).graphql("query { x }")
    assert result.data["r1"] == {"id": "R_1"}
    assert result.not_found_paths == (("r0",),)


def test_graphql_other_errors_raise() -> None:
    body = {"data": None, "errors": [{"type": "INTERNAL", "message": "oops"}]}
    with pytest.raises(GraphQLError, match="oops"):
        _client(_sequence(httpx.Response(200, json=body))).graphql("query { x }")


def test_graphql_rate_limited_raises_rate_limit() -> None:
    body = {"errors": [{"type": "RATE_LIMITED", "message": "limit"}]}
    with pytest.raises(RateLimitExceeded):
        _client(_sequence(httpx.Response(200, json=body))).graphql("query { x }")


def test_request_count_includes_retries() -> None:
    client = _client(_sequence(httpx.Response(500), httpx.Response(200, json={})))
    client.rest_get("/x")
    assert client.request_count == 2


def test_api_follows_redirects() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/old/name":
            return httpx.Response(
                301, headers={"location": "https://api.github.com/repos/new/name"}
            )
        return httpx.Response(200, json={"full_name": "new/name"})

    assert _client(handler).rest_get("/repos/old/name") == {"full_name": "new/name"}


def test_errors_carry_status() -> None:
    with pytest.raises(GitHubError) as info:
        _client(_sequence(httpx.Response(404))).rest_get("/x")
    assert info.value.status == 404
    failures = [httpx.ConnectError("x")] * (MAX_RETRIES + 1)
    with pytest.raises(GitHubError) as info2:
        _client(_sequence(*failures)).rest_get("/x")
    assert info2.value.status is None


def test_non_json_response_is_github_error() -> None:
    with pytest.raises(GitHubError, match="不是 JSON"):
        _client(_sequence(httpx.Response(200, text="<html>"))).rest_get("/x")


def test_too_many_redirects_is_github_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(301, headers={"location": "https://api.github.com/loop"})

    with pytest.raises(GitHubError, match="TooManyRedirects"):
        _client(handler).rest_get("/loop")


def test_graphql_field_errors_are_returned() -> None:
    body = {
        "data": {"nodes": [None, {"id": "R_1"}]},
        "errors": [{"type": "FORBIDDEN", "path": ["nodes", 0], "message": "dmca"}],
    }
    result = _client(_sequence(httpx.Response(200, json=body))).graphql("query { x }")
    assert result.error_under("nodes", 0) == "FORBIDDEN: dmca"
    assert result.error_under("nodes", 1) is None


def test_graphql_without_data_keeps_path_error_messages() -> None:
    body = {
        "data": None,
        "errors": [{"path": ["query", "nodes", "foo"], "message": "Field 'foo' doesn't exist"}],
    }
    with pytest.raises(GraphQLError, match="Field 'foo' doesn't exist"):
        _client(_sequence(httpx.Response(200, json=body))).graphql("query { x }")
