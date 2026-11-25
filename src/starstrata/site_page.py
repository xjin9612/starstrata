"""產生網站時填入 index.html 的語言、說明、Open Graph 標籤與頁尾的 GitHub 連結。

web/index.html 用 HTML 註解標出要填入的位置，未經 starstrata site 處理時仍是合法的頁面。
"""

from __future__ import annotations

import html
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

from starstrata.config import Config
from starstrata.messages import HTML_LANG

META_MARK = "<!-- site:meta -->"
REPO_START = "<!-- site:repo-link -->"
REPO_END = "<!-- /site:repo-link -->"
REPO_URL_TOKEN = "__REPO_URL__"
REPO_NAME_TOKEN = "__REPO_NAME__"
SOCIAL_PREVIEW = "social-preview.png"

DESCRIPTIONS = {
    "zh-TW": (
        "starstrata 每天收集 GitHub 趨勢，累積成長期歷史：今天什麼在紅、"
        "什麼是新冒出來的、哪些 repo 突然爆紅。"
    ),
    "en": (
        "starstrata collects GitHub trends every day and keeps the long-term history: "
        "what is hot today, what just appeared, and which repos suddenly took off."
    ),
}

_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_REMOTE = re.compile(r"github\.com[:/]([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?$")


def detect_repo(environ: Mapping[str, str], cwd: Path) -> str | None:
    """網站所屬的 GitHub repo（owner/name）。

    依序取 GITHUB_REPOSITORY 環境變數（GitHub Actions 提供）與 git 的 origin remote；
    都取不到或不是 GitHub 時回傳 None。
    """
    env = environ.get("GITHUB_REPOSITORY", "")
    if _REPO.fullmatch(env):
        return env
    try:
        remote = subprocess.run(
            ["git", "-C", str(cwd), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except OSError, subprocess.SubprocessError:
        return None
    match = _REMOTE.search(remote)
    return match.group(1) if match else None


def _meta_tags(cfg: Config) -> str:
    description = html.escape(DESCRIPTIONS[cfg.i18n.locale])
    tags = [
        f'<meta name="description" content="{description}">',
        '<meta property="og:type" content="website">',
        '<meta property="og:title" content="starstrata">',
        f'<meta property="og:description" content="{description}">',
        '<meta name="twitter:card" content="summary_large_image">',
    ]
    site_url = cfg.notify.site_url
    if site_url:
        base = site_url if site_url.endswith("/") else site_url + "/"
        tags += [
            f'<meta property="og:url" content="{html.escape(base)}">',
            f'<meta property="og:image" content="{html.escape(base + SOCIAL_PREVIEW)}">',
        ]
    return "\n".join(tags)


def fill_index(text: str, cfg: Config, repo: str | None) -> str:
    """填入 index.html 的內容。repo 為 None 時移除頁尾的 GitHub 連結。"""
    text = re.sub(
        r"<html lang=\"[^\"]*\"", f'<html lang="{HTML_LANG[cfg.i18n.locale]}"', text, count=1
    )
    text = text.replace(META_MARK, _meta_tags(cfg), 1)
    start, end = text.find(REPO_START), text.find(REPO_END)
    if start < 0 or end < start:
        return text
    if repo is None:
        return text[:start] + text[end + len(REPO_END) :]
    block = text[start + len(REPO_START) : end]
    block = block.replace(REPO_URL_TOKEN, html.escape(f"https://github.com/{repo}"))
    block = block.replace(REPO_NAME_TOKEN, html.escape(repo))
    return text[:start] + block + text[end + len(REPO_END) :]
