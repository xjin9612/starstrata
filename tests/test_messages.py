"""Telegram 與報告文字的語言檔檢查。"""

from __future__ import annotations

import string
from dataclasses import fields

import pytest

from starstrata.messages import HTML_LANG, LOCALES, MESSAGES, ZH_TW


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name is not None}


@pytest.mark.parametrize("locale", [loc for loc in LOCALES if loc != "zh-TW"])
def test_placeholders_match_zh_tw(locale: str) -> None:
    other = MESSAGES[locale]
    for f in fields(ZH_TW):
        assert _placeholders(getattr(other, f.name)) == _placeholders(getattr(ZH_TW, f.name)), (
            f.name
        )


def test_every_locale_has_html_lang() -> None:
    assert set(HTML_LANG) == set(LOCALES)


def test_report_headings_differ_between_locales() -> None:
    headings = [m.gainers_report_heading for m in MESSAGES.values()]
    assert len(set(headings)) == len(headings)
