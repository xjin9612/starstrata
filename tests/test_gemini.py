from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from starstrata.gemini import GeminiError, generate_json
from starstrata.reports import Interpretation, InterpretError, interpret_with_gemini

KEY = "test-gemini-key"
SCHEMA: dict[str, Any] = {"type": "object"}


def _ok(
    text: str, finish: str = "STOP", parts: list[dict[str, Any]] | None = None
) -> httpx.Response:
    body = {
        "candidates": [
            {
                "content": {"role": "model", "parts": parts or [{"text": text}]},
                "finishReason": finish,
            }
        ]
    }
    return httpx.Response(200, json=body)


def _transport(
    *responses: httpx.Response | Exception, seen: list[httpx.Request] | None = None
) -> httpx.MockTransport:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.MockTransport(handler)


def test_request_shape() -> None:
    seen: list[httpx.Request] = []
    text = generate_json(
        KEY,
        "gemini-3.5-flash-lite",
        "sys",
        "prompt",
        SCHEMA,
        transport=_transport(_ok("{}"), seen=seen),
    )
    assert text == "{}"
    request = seen[0]
    assert request.url.path == "/v1beta/models/gemini-3.5-flash-lite:generateContent"
    assert request.headers["x-goog-api-key"] == KEY
    assert KEY not in str(request.url)
    body = json.loads(request.content)
    assert body["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert body["contents"] == [{"role": "user", "parts": [{"text": "prompt"}]}]
    assert body["generationConfig"] == {
        "responseMimeType": "application/json",
        "responseJsonSchema": SCHEMA,
    }


def test_thought_parts_are_ignored() -> None:
    parts: list[dict[str, Any]] = [
        {"text": "thinking...", "thought": True},
        {"text": '{"a":'},
        {"text": "1}"},
    ]
    assert (
        generate_json(KEY, "m", "s", "p", SCHEMA, transport=_transport(_ok("", parts=parts)))
        == '{"a":1}'
    )


def test_retries_rate_limit_then_succeeds() -> None:
    sleeps: list[float] = []
    transport = _transport(httpx.Response(429), httpx.Response(503), _ok("{}"))
    assert (
        generate_json(KEY, "m", "s", "p", SCHEMA, transport=transport, sleep=sleeps.append) == "{}"
    )
    assert sleeps == [5, 10]


def test_transport_errors_are_retried() -> None:
    sleeps: list[float] = []
    transport = _transport(httpx.ConnectError("x"), _ok("{}"))
    assert (
        generate_json(KEY, "m", "s", "p", SCHEMA, transport=transport, sleep=sleeps.append) == "{}"
    )
    assert sleeps == [5]


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (
            httpx.Response(
                400, json={"error": {"status": "INVALID_ARGUMENT", "message": f"bad key {KEY}"}}
            ),
            "Gemini 回應 400：INVALID_ARGUMENT bad key \\*\\*\\*",
        ),
        (
            httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
            "prompt 被擋下：SAFETY",
        ),
        (httpx.Response(200, json={"candidates": []}), "沒有回傳結果"),
        (_ok('{"items": [', finish="MAX_TOKENS"), "回應中止：MAX_TOKENS"),
        (_ok("", parts=[{"text": "x", "thought": True}]), "沒有文字內容"),
        (httpx.Response(200, text="<html>"), "不是 JSON"),
    ],
)
def test_failures_raise_without_key(response: httpx.Response, message: str) -> None:
    with pytest.raises(GeminiError, match=message) as info:
        generate_json(
            KEY, "m", "s", "p", SCHEMA, transport=_transport(response), sleep=lambda s: None
        )
    assert KEY not in str(info.value)


def test_key_straddling_truncation_is_masked() -> None:
    message = "x" * (200 - len(KEY) // 2) + KEY
    response = httpx.Response(
        403, json={"error": {"status": "PERMISSION_DENIED", "message": message}}
    )
    with pytest.raises(GeminiError) as info:
        generate_json(
            KEY, "m", "s", "p", SCHEMA, transport=_transport(response), sleep=lambda s: None
        )
    assert KEY[: len(KEY) // 2] not in str(info.value)


def test_retry_exhausted_reports_last_status() -> None:
    transport = _transport(*[httpx.Response(503)] * 3)
    with pytest.raises(GeminiError, match="503"):
        generate_json(KEY, "m", "s", "p", SCHEMA, transport=transport, sleep=lambda s: None)


def test_invalid_model_name_is_rejected_before_request() -> None:
    with pytest.raises(GeminiError, match="模型名稱不合法"):
        generate_json(KEY, "../admin", "s", "p", SCHEMA, transport=_transport())


def test_interpret_with_gemini_parses_and_filters() -> None:
    payload = {
        "items": [
            {"full_name": "o/a", "what": "w", "why": "y"},
            {"full_name": "o/b", "what": 1, "why": "y"},
            {"full_name": "evil/x", "what": "w", "why": "y"},
            "not an object",
        ]
    }
    transport = _transport(_ok(json.dumps(payload)))
    result = interpret_with_gemini(KEY, "m", "prompt", ["o/a", "o/b"], transport=transport)
    assert result == {"o/a": Interpretation(what="w", why="y")}


def test_interpret_with_gemini_wraps_errors() -> None:
    with pytest.raises(InterpretError, match="回應中止：SAFETY"):
        interpret_with_gemini(
            KEY, "m", "p", ["o/a"], transport=_transport(_ok("", finish="SAFETY"))
        )
    with pytest.raises(InterpretError, match="Gemini 回應格式不符"):
        interpret_with_gemini(KEY, "m", "p", ["o/a"], transport=_transport(_ok('{"items": 3}')))
