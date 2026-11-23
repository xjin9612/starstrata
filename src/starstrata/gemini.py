"""以 Gemini API 的 generateContent 產生符合 JSON schema 的回應。"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

import httpx

API_BASE = "https://generativelanguage.googleapis.com/v1beta"

# 每次呼叫最多嘗試次數。429 與 5xx 的重試間隔依序為 5、10 秒。
MAX_ATTEMPTS = 3
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})

# 模型名稱會放進網址路徑。
MODEL_PATTERN = re.compile(r"[a-z0-9][a-z0-9.-]*")


class GeminiError(Exception):
    """沒有取得可用的回應。訊息不含 API key。"""


def generate_json(
    api_key: str,
    model: str,
    system: str,
    prompt: str,
    schema: dict[str, Any],
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    """回傳模型輸出的 JSON 文字。被安全機制擋下、輸出被截斷或 HTTP 錯誤時丟出 GeminiError。"""
    if not MODEL_PATTERN.fullmatch(model):
        raise GeminiError(f"模型名稱不合法：{model!r}")
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": schema,
        },
    }
    # API key 放在 header，不放在網址，避免出現在例外訊息或 log。
    headers = {"x-goog-api-key": api_key}
    with httpx.Client(base_url=API_BASE, timeout=120.0, transport=transport) as client:
        for attempt in range(MAX_ATTEMPTS):
            last = attempt + 1 == MAX_ATTEMPTS
            try:
                response = client.post(
                    f"/models/{model}:generateContent", json=body, headers=headers
                )
            except httpx.TransportError as e:
                if last:
                    raise GeminiError(f"連線失敗：{type(e).__name__}") from None
                sleep(5 * 2**attempt)
                continue
            if response.status_code in _RETRY_STATUS and not last:
                sleep(5 * 2**attempt)
                continue
            if response.status_code != 200:
                detail = _error_detail(response, api_key)
                raise GeminiError(f"Gemini 回應 {response.status_code}{detail}")
            return _text(response)
    raise GeminiError("重試次數用盡")


def _error_detail(response: httpx.Response, api_key: str) -> str:
    """Google 錯誤回應的 status 與 message。先遮掉 api_key 再截斷到 200 字元。"""
    try:
        error = response.json()["error"]
    except (ValueError, KeyError, TypeError):
        return ""
    if not isinstance(error, dict):
        return ""
    parts = [
        str(error[k]).replace(api_key, "***")[:200] for k in ("status", "message") if error.get(k)
    ]
    return f"：{' '.join(parts)}" if parts else ""


def _text(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError as e:
        raise GeminiError("Gemini 回應不是 JSON") from e
    block = (data.get("promptFeedback") or {}).get("blockReason")
    if block:
        raise GeminiError(f"prompt 被擋下：{block}")
    candidates = data.get("candidates") or []
    if not candidates:
        raise GeminiError("Gemini 沒有回傳結果")
    candidate = candidates[0]
    finish = candidate.get("finishReason")
    if finish != "STOP":
        raise GeminiError(f"Gemini 回應中止：{finish}")
    parts = (candidate.get("content") or {}).get("parts") or []
    # thought 為 true 的 part 是模型的思考摘要，不是答案。
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    if not text:
        raise GeminiError("Gemini 回應沒有文字內容")
    return text
