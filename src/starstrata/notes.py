"""讀取並驗證作者短評 notes.toml。格式見 docs/DEVELOPMENT.md「作者短評」。"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from starstrata.config import ConfigError

MAX_NOTE_LENGTH = 200
_FULL_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_KEYS = {"note", "article", "updated"}


class NotesError(ConfigError):
    """notes.toml 內容不合法。訊息包含出錯的 repo 與鍵。"""


@dataclass(frozen=True)
class Note:
    text: str
    article_url: str | None
    updated: date


def _note(name: str, value: Any) -> Note:
    if not isinstance(value, dict):
        raise NotesError(f'notes["{name}"]: 必須是表格')
    unknown = sorted(set(value) - _KEYS)
    if unknown:
        raise NotesError(f'notes["{name}"]: 未知的設定 {", ".join(unknown)}')
    text = value.get("note")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_NOTE_LENGTH:
        raise NotesError(f'notes["{name}"].note: 必須是 1 到 {MAX_NOTE_LENGTH} 字的字串')
    article = value.get("article")
    if article is not None and not (isinstance(article, str) and article.startswith("https://")):
        raise NotesError(f'notes["{name}"].article: 必須是 https:// 開頭的網址')
    updated = value.get("updated")
    # datetime 是 date 的子類別，必須另外排除。
    if not isinstance(updated, date) or isinstance(updated, datetime):
        raise NotesError(f'notes["{name}"].updated: 必須是日期，例如 2026-11-24')
    return Note(text=text.strip(), article_url=article, updated=updated)


def parse_notes(text: str) -> dict[str, Note]:
    """解析 notes.toml 的內容，key 轉成小寫的 full_name。"""
    try:
        root = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise NotesError(f"notes.toml 格式錯誤：{e}") from e
    notes: dict[str, Note] = {}
    for name, value in root.items():
        if not _FULL_NAME.fullmatch(name):
            raise NotesError(f'notes["{name}"]: key 必須是 owner/name')
        key = name.lower()
        if key in notes:
            raise NotesError(f'notes["{name}"]: 與其他 key 只差在大小寫')
        notes[key] = _note(name, value)
    return notes


def load_notes(path: Path) -> dict[str, Note]:
    """檔案不存在時回傳空的對照表。"""
    if not path.is_file():
        return {}
    return parse_notes(path.read_text(encoding="utf-8"))
