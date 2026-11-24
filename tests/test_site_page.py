from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from starstrata.config import Config, SiteConfig, load_config
from starstrata.site_page import detect_repo, fill_index

REPO_ROOT = Path(__file__).resolve().parent.parent
PAGE = """<!doctype html>
<html lang="zh-Hant">
<head>
<title>t</title>
<!-- site:meta -->
</head>
<body>
<footer>
  <!-- site:repo-link -->
  <a href="__REPO_URL__">__REPO_NAME__</a>
  <!-- /site:repo-link -->
</footer>
</body>
</html>
"""


@pytest.fixture
def cfg() -> Config:
    return load_config(REPO_ROOT / "config.toml")


def _with(
    cfg: Config, *, locale: str = "zh-TW", site_url: str = "https://u.github.io/s/"
) -> Config:
    return replace(
        cfg, site=SiteConfig(locale=locale), notify=replace(cfg.notify, site_url=site_url)
    )


def test_fill_index_with_site_url_and_repo(cfg: Config) -> None:
    page = fill_index(PAGE, _with(cfg), "o/r")
    assert '<html lang="zh-Hant">' in page
    assert '<meta property="og:url" content="https://u.github.io/s/">' in page
    assert '<meta property="og:image" content="https://u.github.io/s/social-preview.png">' in page
    assert '<a href="https://github.com/o/r">o/r</a>' in page
    assert "site:" not in page and "__REPO" not in page


def test_fill_index_english_without_site_url_or_repo(cfg: Config) -> None:
    page = fill_index(PAGE, _with(cfg, locale="en", site_url=""), None)
    assert '<html lang="en">' in page
    assert 'name="description" content="starstrata collects GitHub trends' in page
    assert "og:url" not in page and "og:image" not in page
    assert "<a " not in page and "site:" not in page


def test_site_url_without_trailing_slash(cfg: Config) -> None:
    page = fill_index(PAGE, _with(cfg, site_url="https://u.github.io/s"), None)
    assert 'content="https://u.github.io/s/social-preview.png"' in page


def test_detect_repo_prefers_environment(tmp_path: Path) -> None:
    assert detect_repo({"GITHUB_REPOSITORY": "a/b"}, tmp_path) == "a/b"


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/a/b.git", "a/b"),
        ("git@github.com:a/b.git", "a/b"),
        ("https://github.com/a/b", "a/b"),
        ("https://gitlab.com/a/b.git", None),
    ],
)
def test_detect_repo_from_git_remote(tmp_path: Path, remote: str, expected: str | None) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin", remote], check=True)
    assert detect_repo({"GITHUB_REPOSITORY": "not a repo"}, tmp_path) == expected


def test_detect_repo_without_git(tmp_path: Path) -> None:
    assert detect_repo({}, tmp_path) is None
