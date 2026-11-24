"""瀏覽器測試的共用環境：以 fixture 快照產生網站，在本機起 HTTP server，用 Playwright 開啟。"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import date, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from starstrata.backfill import backfill_path
from starstrata.cli import main
from starstrata.snapshots import write_jsonl
from starstrata.translate import save, text_key
from tests.snapshot_builder import meta, repo, search, trending, write_day

REPO_ROOT = Path(__file__).resolve().parents[2]
FIRST_DAY = date(2026, 11, 16)
LAST_DAY = date(2026, 11, 24)
SEARCH_ONLY_NEW = 30


def _trending(full_name: str, rank: int, period: str = "daily", **kwargs: Any) -> dict[str, Any]:
    return trending(full_name, rank, **kwargs) | {"period": period}


def write_fixture_data(root: Path) -> None:
    """9 天的快照，涵蓋各頁面需要的情況。

    - hot/rocket：每天 +10，最後一天 +900，觸發有基準的警示；另有回補資料。
    - o/steady：Python，每天上 Trending，最後一天也在本週總榜；說明有繁中譯文。
    - o/typed：TypeScript，最後一天上 TypeScript 榜。
    - late/burst：倒數第二天才出現，最後一天 +300，觸發基準不足的警示。
    - late/single：最後一天才出現，只有一個快照；Trending 上沒有顯示新增數。
    - s/new-NN：最後一天只出現在 Search 的新 repo，數量超過新上榜頁一次顯示的筆數。
    Rust 沒有任何上榜，用來測篩選後的空狀態。
    """
    days = [FIRST_DAY + timedelta(days=i) for i in range((LAST_DAY - FIRST_DAY).days + 1)]
    for i, day in enumerate(days):
        last = day == LAST_DAY
        repos = [
            repo(
                "R_hot", 1000 + 10 * i + (890 if last else 0), full_name="hot/rocket", meta=meta()
            ),
            repo("R_steady", 500 + 5 * i, full_name="o/steady", meta=meta(description="steady")),
            repo("R_typed", 300 + i, full_name="o/typed", meta=meta(language="TypeScript")),
        ]
        rows = [_trending("o/steady", 1, stars=50), _trending("o/steady", 1, language="python")]
        searches: list[dict[str, Any]] = []
        if day >= LAST_DAY - timedelta(days=1):
            repos.append(
                repo("R_burst", 100 + (300 if last else 0), full_name="late/burst", meta=meta())
            )
        if last:
            repos.append(repo("R_single", 80, full_name="late/single", meta=meta()))
            rows += [
                _trending("hot/rocket", 2, stars=900),
                _trending("late/single", 3, stars=None),
                _trending("o/typed", 1, language="typescript", stars=7),
                _trending("o/steady", 1, "weekly", stars=300),
            ]
            for n in range(SEARCH_ONLY_NEW):
                node = f"R_new{n:02d}"
                repos.append(repo(node, 1000 - n, full_name=f"s/new-{n:02d}", meta=meta()))
                searches.append(search(node, f"s/new-{n:02d}", rank=n + 1))
        write_day(root, day, repos=repos, trending_rows=rows, search_rows=searches)
    save(root, "zh-TW", {text_key("steady"): "穩定成長"})
    write_jsonl(
        backfill_path(root, "R_hot"),
        [{"week": "2026-10-25", "days": [5, 0, 0, 3, 0, 0, 2]}],
    )


@pytest.fixture(scope="session")
def site_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("e2e")
    data = work / "data"
    write_fixture_data(data)
    out = work / "_site"
    code = main(
        [
            "--config",
            str(REPO_ROOT / "config.toml"),
            "--data",
            str(data),
            "--db",
            str(work / "db.sqlite"),
            "site",
            "--out",
            str(out),
            "--web",
            str(REPO_ROOT / "web"),
            "--icon",
            str(REPO_ROOT / "assets" / "icon.svg"),
        ]
    )
    assert code == 0
    return out


@pytest.fixture(scope="session")
def base_url(site_dir: Path) -> Iterator[str]:
    handler = partial(_QuietHandler, directory=str(site_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        b = p.chromium.launch()
        try:
            yield b
        finally:
            b.close()


@pytest.fixture
def failed_requests() -> list[str]:
    """回應 4xx、5xx 的網址。預期會 404 的測試，檢查後自行清空。"""
    return []


@pytest.fixture
def page(browser: Browser, base_url: str, failed_requests: list[str]) -> Iterator[Page]:
    """每個測試一個新的 context。

    測試結束時不能有 JS 錯誤、失敗的請求，也不能連到本機以外的網址。
    """
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = context.new_page()
    errors: list[str] = []
    outside: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    # 失敗的請求由 failed_requests 記錄，瀏覽器另外印的 console 錯誤不重複計入。
    pg.on(
        "console",
        lambda m: (
            errors.append(m.text)
            if m.type == "error" and not m.text.startswith("Failed to load resource")
            else None
        ),
    )
    pg.on("response", lambda r: failed_requests.append(r.url) if r.status >= 400 else None)
    host = urlsplit(base_url).netloc

    def guard(route: Any) -> None:
        url = route.request.url
        if urlsplit(url).netloc == host:
            route.continue_()
        else:
            outside.append(url)
            route.abort()

    pg.route("**/*", guard)
    try:
        yield pg
    finally:
        context.close()
    assert errors == []
    assert failed_requests == []
    assert outside == []
