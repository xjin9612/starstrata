"""發送 Telegram 訊息。"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

API_BASE = "https://api.telegram.org"

# Bot API sendMessage 的 text 上限。
MAX_MESSAGE_LENGTH = 4096

# 429 回應的 retry_after 不超過此秒數才等待重試。
MAX_RETRY_AFTER_SECONDS = 60

# 每則訊息最多嘗試次數。連線錯誤與 5xx 的重試間隔依序為 1、2 秒。
MAX_ATTEMPTS = 3


@dataclass(frozen=True)
class Button:
    """訊息下方的網址按鈕。"""

    text: str
    url: str


class TelegramError(Exception):
    """發送失敗。訊息不含 bot token。"""


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """依行切成不超過 limit 字元的段落。單行超過 limit 時硬切，切點不會避開 HTML 標籤。"""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


class TelegramSender:
    """bot token 只出現在請求路徑，不會寫進例外訊息。"""

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        thread_id: int | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """thread_id 是群組主題（forum topic）的 ID；None 表示不指定主題。"""
        self._path = f"/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._thread_id = thread_id
        self._client = httpx.Client(base_url=API_BASE, timeout=30.0, transport=transport)
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TelegramSender:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def send(self, html_text: str, *, silent: bool = False, buttons: Sequence[Button] = ()) -> None:
        """以 HTML parse mode 發送，超過長度上限時分段，按鈕只附在最後一段。

        silent 為 True 時收訊者不會收到通知音。
        """
        chunks = split_message(html_text)
        for i, chunk in enumerate(chunks):
            self._send_one(chunk, silent, buttons if i == len(chunks) - 1 else ())

    def _send_one(self, text: str, silent: bool, buttons: Sequence[Button]) -> None:
        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
            "disable_notification": silent,
        }
        if self._thread_id is not None:
            payload["message_thread_id"] = self._thread_id
        if buttons:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": b.text, "url": b.url} for b in buttons]]
            }
        for attempt in range(MAX_ATTEMPTS):
            last = attempt + 1 == MAX_ATTEMPTS
            try:
                response = self._client.post(self._path, json=payload)
            except httpx.TransportError as e:
                if last:
                    raise TelegramError(f"連線失敗：{type(e).__name__}") from None
                self._sleep(2**attempt)
                continue
            status = response.status_code
            if status == 429 and not last:
                retry_after = _retry_after(response)
                if retry_after is not None and retry_after <= MAX_RETRY_AFTER_SECONDS:
                    self._sleep(retry_after)
                    continue
            if status >= 500 and not last:
                self._sleep(2**attempt)
                continue
            if status != 200:
                raise TelegramError(f"回應 {status}：{_description(response)}")
            return


def _retry_after(response: httpx.Response) -> int | None:
    try:
        value = response.json().get("parameters", {}).get("retry_after")
    except ValueError:
        return None
    return value if isinstance(value, int) else None


def _description(response: httpx.Response) -> str:
    try:
        return str(response.json().get("description", ""))[:200]
    except ValueError:
        return ""
