from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from starstrata.notes import MAX_NOTE_LENGTH, Note, NotesError, load_notes, parse_notes

REPO_ROOT = Path(__file__).resolve().parent.parent


def _entry(note: str = "'好用'", extra: str = "") -> str:
    return f'["Owner/Repo"]\nnote = {note}\nupdated = 2026-11-24\n{extra}'


def test_repo_notes_file_is_valid() -> None:
    load_notes(REPO_ROOT / "notes.toml")


def test_valid_note_with_article() -> None:
    notes = parse_notes(_entry(extra='article = "https://example.com/a"\n'))
    assert notes == {
        "owner/repo": Note(
            text="好用", article_url="https://example.com/a", updated=date(2026, 11, 24)
        )
    }


@pytest.mark.parametrize("length", [1, MAX_NOTE_LENGTH])
def test_note_length_bounds_are_accepted(length: int) -> None:
    assert len(parse_notes(_entry(f'"{"字" * length}"'))["owner/repo"].text) == length


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (_entry('""'), 'notes["Owner/Repo"].note: 必須是 1 到 200 字的字串'),
        (_entry('"   "'), 'notes["Owner/Repo"].note: 必須是 1 到 200 字的字串'),
        (_entry(f'"{"字" * (MAX_NOTE_LENGTH + 1)}"'), "note: 必須是 1 到 200 字"),
        (_entry("1"), "note: 必須是 1 到 200 字"),
        (_entry(extra='article = "http://example.com"\n'), "article: 必須是 https:// 開頭"),
        (_entry(extra='article = "javascript:alert(1)"\n'), "article: 必須是 https:// 開頭"),
        ('["o/r"]\nnote = "x"\nupdated = "2026-11-24"\n', 'notes["o/r"].updated: 必須是日期'),
        ('["o/r"]\nnote = "x"\nupdated = 2026-11-24T10:00:00\n', "updated: 必須是日期"),
        ('["o/r"]\nnote = "x"\n', "updated: 必須是日期"),
        (_entry(extra="extra = 1\n"), 'notes["Owner/Repo"]: 未知的設定 extra'),
        ('["not-a-repo"]\nnote = "x"\nupdated = 2026-11-24\n', "key 必須是 owner/name"),
        ('"o/r" = "x"\n', 'notes["o/r"]: 必須是表格'),
        (_entry() + '["owner/repo"]\nnote = "y"\nupdated = 2026-11-24\n', "只差在大小寫"),
        ('["o/r"\n', "notes.toml 格式錯誤"),
    ],
)
def test_invalid_notes_are_rejected(text: str, message: str) -> None:
    with pytest.raises(NotesError, match=re.escape(message)):
        parse_notes(text)


def test_missing_file_means_no_notes(tmp_path: Path) -> None:
    assert load_notes(tmp_path / "notes.toml") == {}
