from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from starstrata.gemini import GeminiError, generate_json, generate_json_with_fallback
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
    result = interpret_with_gemini(
        KEY, ["m"], "system", "prompt", ["o/a", "o/b"], transport=transport
    )
    assert result == {"o/a": Interpretation(what="w", why="y")}


def test_interpret_with_gemini_wraps_errors() -> None:
    with pytest.raises(InterpretError, match="回應中止：SAFETY"):
        interpret_with_gemini(
            KEY, ["m"], "s", "p", ["o/a"], transport=_transport(_ok("", finish="SAFETY"))
        )
    with pytest.raises(InterpretError, match="Gemini 回應格式不符"):
        interpret_with_gemini(
            KEY, ["m"], "s", "p", ["o/a"], transport=_transport(_ok('{"items": 3}'))
        )


def _by_model(responses: dict[str, list[httpx.Response]], seen: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        model = request.url.path.split("/models/")[1].split(":")[0]
        seen.append(model)
        return responses[model].pop(0)

    return httpx.MockTransport(handler)


def test_fallback_after_retries_exhausted(caplog: pytest.LogCaptureFixture) -> None:
    seen: list[str] = []
    transport = _by_model({"a": [httpx.Response(503)] * 3, "b": [_ok("{}")]}, seen)
    text = generate_json_with_fallback(
        KEY, ["a", "b"], "s", "p", SCHEMA, transport=transport, sleep=lambda s: None
    )
    assert text == "{}" and seen == ["a", "a", "a", "b"]
    assert "a 無法使用" in caplog.text and "改用 b" in caplog.text


def test_missing_model_falls_back_without_retry() -> None:
    seen: list[str] = []
    transport = _by_model({"a": [httpx.Response(404)], "b": [_ok("{}")]}, seen)
    generate_json_with_fallback(
        KEY, ["a", "b"], "s", "p", SCHEMA, transport=transport, sleep=lambda s: None
    )
    assert seen == ["a", "b"]


@pytest.mark.parametrize(
    "response",
    [httpx.Response(400), httpx.Response(403), _ok("", finish="SAFETY")],
)
def test_non_retryable_errors_do_not_fall_back(response: httpx.Response) -> None:
    seen: list[str] = []
    transport = _by_model({"a": [response], "b": [_ok("{}")]}, seen)
    with pytest.raises(GeminiError):
        generate_json_with_fallback(
            KEY, ["a", "b"], "s", "p", SCHEMA, transport=transport, sleep=lambda s: None
        )
    assert seen == ["a"]


def test_all_models_failing_raises_last_error() -> None:
    seen: list[str] = []
    transport = _by_model({"a": [httpx.Response(429)] * 3, "b": [httpx.Response(404)]}, seen)
    with pytest.raises(GeminiError, match="404"):
        generate_json_with_fallback(
            KEY, ["a", "b"], "s", "p", SCHEMA, transport=transport, sleep=lambda s: None
        )


@pytest.mark.parametrize("models", ["ab", []])
def test_models_must_be_a_non_empty_list(models: Any) -> None:
    with pytest.raises(GeminiError, match="models 必須是非空的模型名稱清單"):
        generate_json_with_fallback(KEY, models, "s", "p", SCHEMA, transport=_transport())
