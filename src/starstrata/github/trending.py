"""抓取並解析 GitHub Trending 頁面。Trending 沒有官方 API，只能解析 HTML。"""

from __future__ import annotations

import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from starstrata.github.http import GitHubClient
from starstrata.snapshots import TrendingEntry

_STARS_IN_PERIOD = re.compile(r"([\d,]+)\s+stars?\s+(?:today|this week|this month)")


class TrendingParseError(Exception):
    """頁面結構與預期不符，通常代表 GitHub 改版。"""


def trending_path(language: str | None) -> str:
    return "/trending" if language is None else f"/trending/{quote(language, safe='')}"


def parse_trending(html: str, *, period: str, language: str | None) -> list[TrendingEntry]:
    """依頁面順序回傳上榜 repo。頁面沒有任何 repo 時回傳空清單。"""
    soup = BeautifulSoup(html, "html.parser")
    entries: list[TrendingEntry] = []
    for rank, row in enumerate(soup.select("article.Box-row"), start=1):
        link = row.select_one("h2 a[href]")
        if link is None:
            raise TrendingParseError(f"第 {rank} 列找不到 repo 連結")
        parts = str(link["href"]).strip("/").split("/")
        if len(parts) != 2 or not all(parts):
            raise TrendingParseError(f"第 {rank} 列的連結不是 repo：{link['href']}")
        match = _STARS_IN_PERIOD.search(row.get_text(" ", strip=True))
        stars = int(match.group(1).replace(",", "")) if match else None
        entries.append(
            TrendingEntry(
                period=period,
                language=language,
                rank=rank,
                full_name="/".join(parts),
                stars_in_period=stars,
            )
        )
    return entries


def fetch_trending(
    client: GitHubClient, *, language: str | None, period: str
) -> list[TrendingEntry]:
    html = client.get_html(trending_path(language), {"since": period})
    return parse_trending(html, period=period, language=language)
