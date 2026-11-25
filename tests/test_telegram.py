from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest

from starstrata.cli import main
from starstrata.telegram import (
    MAX_RETRY_AFTER_SECONDS,
    Button,
    TelegramError,
    TelegramSender,
    split_message,
)

TOKEN = "123456:secret-bot-token"


def _sender(handler: httpx.MockTransport, sleeps: list[float] | None = None) -> TelegramSender:
    record = sleeps if sleeps is not None else []
    return TelegramSender(TOKEN, "42", transport=handler, sleep=record.append)


def test_split_keeps_short_message() -> None:
    assert split_message("a\nb", limit=10) == ["a\nb"]


def test_split_on_line_boundaries() -> None:
    assert split_message("aaaa\nbbbb\ncccc", limit=9) == ["aaaa\nbbbb", "cccc"]


def test_split_hard_cuts_overlong_line() -> None:
    assert split_message("x" * 25, limit=10) == ["x" * 10, "x" * 10, "x" * 5]


def test_split_chunks_never_exceed_limit() -> None:
    text = "\n".join("line" * n for n in range(1, 40))
    chunks = split_message(text, limit=50)
    assert all(len(c) <= 50 for c in chunks)
    assert "\n".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_send_posts_html_message() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    _sender(httpx.MockTransport(handler)).send("<b>hi</b>")
    body = json.loads(seen[0].content)
    assert seen[0].url.path == f"/bot{TOKEN}/sendMessage"
    assert body == {
        "chat_id": "42",
        "text": "<b>hi</b>",
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
        "disable_notification": False,
    }


def test_default_request_is_unchanged() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    _sender(httpx.MockTransport(handler)).send("<b>hi</b>")
    assert seen[0].content == (
        b'{"chat_id":"42","text":"<b>hi</b>","parse_mode":"HTML",'
        b'"link_preview_options":{"is_disabled":true},"disable_notification":false}'
    )


def test_thread_id_is_sent_with_every_chunk() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    sender = TelegramSender(TOKEN, "42", thread_id=7, transport=httpx.MockTransport(handler))
    sender.send("\n".join(["a" * 3000, "b" * 3000]))
    assert len(seen) == 2
    assert all(type(body["message_thread_id"]) is int for body in seen)
    assert [body["message_thread_id"] for body in seen] == [7, 7]


def test_silent_send_disables_notification() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    _sender(httpx.MockTransport(handler)).send("x", silent=True)
    assert json.loads(seen[0].content)["disable_notification"] is True


def test_buttons_are_attached_to_last_chunk_only() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    text = "\n".join(["a" * 3000, "b" * 3000])
    _sender(httpx.MockTransport(handler)).send(text, buttons=[Button("開", "https://e.com/")])
    assert len(seen) == 2 and "reply_markup" not in seen[0]
    assert seen[1]["reply_markup"] == {
        "inline_keyboard": [[{"text": "開", "url": "https://e.com/"}]]
    }


@pytest.mark.parametrize("retry_after", [3, MAX_RETRY_AFTER_SECONDS])
def test_rate_limit_is_retried(retry_after: int) -> None:
    responses = [
        httpx.Response(429, json={"ok": False, "parameters": {"retry_after": retry_after}}),
        httpx.Response(200, json={"ok": True}),
    ]
    sleeps: list[float] = []
    _sender(httpx.MockTransport(lambda r: responses.pop(0)), sleeps).send("x")
    assert sleeps == [retry_after]


@pytest.mark.parametrize("retry_after", [MAX_RETRY_AFTER_SECONDS + 1, 600])
def test_long_rate_limit_fails(retry_after: int) -> None:
    response = httpx.Response(429, json={"ok": False, "parameters": {"retry_after": retry_after}})
    with pytest.raises(TelegramError, match="429"):
        _sender(httpx.MockTransport(lambda r: response)).send("x")


def test_errors_do_not_leak_token() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with pytest.raises(TelegramError) as info:
        _sender(httpx.MockTransport(fail)).send("x")
    assert TOKEN not in str(info.value)
    assert info.value.__cause__ is None

    bad = httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})
    with pytest.raises(TelegramError, match="chat not found") as info2:
        _sender(httpx.MockTransport(lambda r: bad)).send("x")
    assert TOKEN not in str(info2.value)


def test_cli_logging_hides_request_urls(caplog: pytest.LogCaptureFixture) -> None:
    main(["--config", "does-not-exist.toml", "ingest"])
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="starstrata"):
        _sender(httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True}))).send("x")
    assert TOKEN not in caplog.text
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_transport_errors_are_retried() -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"ok": True})

    sleeps: list[float] = []
    _sender(httpx.MockTransport(handler), sleeps).send("x")
    assert len(attempts) == 3 and sleeps == [1, 2]


def test_server_errors_are_retried_then_fail() -> None:
    sleeps: list[float] = []
    response = httpx.Response(502, json={"ok": False})
    with pytest.raises(TelegramError, match="502"):
        _sender(httpx.MockTransport(lambda r: response), sleeps).send("x")
    assert sleeps == [1, 2]


def test_client_error_is_not_retried() -> None:
    sleeps: list[float] = []
    bad = httpx.Response(400, json={"ok": False, "description": "Bad Request"})
    with pytest.raises(TelegramError):
        _sender(httpx.MockTransport(lambda r: bad), sleeps).send("x")
    assert sleeps == []
