"""瀏覽器測試的共用環境：以 fixture 快照產生網站，在本機起 HTTP server，用 Playwright 開啟。

同一份資料產生 zh-TW 與 en 兩個網站，分別放在 server 根目錄的 zh/ 與 en/。
"""

from __future__ import annotations

import json
import os
import re
import threading
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, Page, Route, sync_playwright

from starstrata.backfill import backfill_path
from starstrata.cli import main
from starstrata.reports import GAINERS_HEADING
from starstrata.snapshots import RUN_FILE, day_dir, write_json, write_jsonl
from starstrata.translate import save, text_key
from tests.snapshot_builder import meta, repo, search, trending, write_day

REPO_ROOT = Path(__file__).resolve().parents[2]
FIRST_DAY = date(2026, 11, 16)
LAST_DAY = date(2026, 11, 24)
SEARCH_ONLY_NEW = 30
GITHUB_REPO = "me/site"
SITE_URL = "https://me.example/site/"
# 最後一天的收集完成時間（台北 07:38）；page fixture 把瀏覽器時鐘固定在它之後 2 小時。
COLLECTED_AT = "2026-11-23T23:38:00+00:00"
NOW = datetime.fromisoformat(COLLECTED_AT) + timedelta(hours=2)
NOTES = """["o/steady"]
note = "穩定的參考實作。"
article = "https://example.com/steady"
updated = 2026-11-20
"""


def _trending(full_name: str, rank: int, period: str = "daily", **kwargs: Any) -> dict[str, Any]:
    return trending(full_name, rank, **kwargs) | {"period": period}


def write_fixture_data(root: Path) -> None:
    """9 天的快照，涵蓋各頁面需要的情況。

    - hot/rocket：每天 +10，最後一天 +900，觸發有基準的警示；另有回補資料；最後一天才上總榜。
    - o/steady：Python，每天都是總榜與 Python 榜第 1 名，最後一天也在本週總榜；說明有繁中譯文。
    - o/typed：TypeScript，倒數第二天總榜第 2 名、最後一天第 4 名；最後一天上 TypeScript 榜。
    - o/gappy：只有第 1、5、9 天有快照，詳細頁有推算的日子。
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
                "R_hot",
                1000 + 10 * i + (890 if last else 0),
                full_name="hot/rocket",
                meta=meta(topics=["rocketry"]),
            ),
            repo("R_steady", 500 + 5 * i, full_name="o/steady", meta=meta(description="steady")),
            repo("R_typed", 300 + i, full_name="o/typed", meta=meta(language="TypeScript")),
        ]
        if i in (0, 4, 8):
            repos.append(repo("R_gappy", 40 + 4 * i, full_name="o/gappy", meta=meta()))
        rows = [_trending("o/steady", 1, stars=50), _trending("o/steady", 1, language="python")]
        searches: list[dict[str, Any]] = []
        if day == LAST_DAY - timedelta(days=1):
            rows.append(_trending("o/typed", 2, stars=3))
        if day >= LAST_DAY - timedelta(days=1):
            repos.append(
                repo("R_burst", 100 + (300 if last else 0), full_name="late/burst", meta=meta())
            )
        if last:
            repos.append(repo("R_single", 80, full_name="late/single", meta=meta()))
            rows += [
                _trending("hot/rocket", 2, stars=900),
                _trending("late/single", 3, stars=None),
                _trending("o/typed", 4, stars=1),
                _trending("o/typed", 1, language="typescript", stars=7),
                _trending("o/steady", 1, "weekly", stars=300),
            ]
            for n in range(SEARCH_ONLY_NEW):
                node = f"R_new{n:02d}"
                repos.append(repo(node, 1000 - n, full_name=f"s/new-{n:02d}", meta=meta()))
                searches.append(search(node, f"s/new-{n:02d}", rank=n + 1))
        write_day(root, day, repos=repos, trending_rows=rows, search_rows=searches)
    merged = {"trending_missing": [], "search_ok": True, "repos_missing": []}
    write_json(
        day_dir(root, LAST_DAY) / RUN_FILE,
        {"date": LAST_DAY.isoformat(), "finished_at": COLLECTED_AT, "merged": merged},
    )
    save(root, "zh-TW", {text_key("steady"): "穩定成長"})
    write_jsonl(
        backfill_path(root, "R_hot"),
        [{"week": "2026-10-25", "days": [5, 0, 0, 3, 0, 0, 2]}],
    )


def _report(title: str, top: str) -> str:
    return (
        f"# {title}\n\n## 摘要\n\n- 本期有快照的 repo：3 個\n\n## {GAINERS_HEADING}\n\n"
        "| # | Repo | 本期新增 ★ |\n|---:|---|---:|\n"
        f"| 1 | [{top}](https://github.com/{top}) | +100 |\n\n"
        "## 重點專案解讀\n\n> 由 AI 模型（`m`）依 repo 描述與 README 產生，可能有誤。\n"
    )


def write_reports(root: Path) -> None:
    for kind, label, top in [
        ("weekly", "2026-W46", "o/steady"),
        ("weekly", "2026-W47", "hot/rocket"),
        ("monthly", "2025-12", "o/typed"),
    ]:
        (root / kind).mkdir(parents=True, exist_ok=True)
        text = _report(f"starstrata 報告 {label}", top)
        (root / kind / f"{label}.md").write_text(text, encoding="utf-8")


def _config(locale: str) -> str:
    """repo 的 config.toml 換上測試用的網址、介面語言與翻譯目標，不受 fork 的設定影響。"""
    text = (REPO_ROOT / "config.toml").read_text(encoding="utf-8")
    for key, value in [("site_url", SITE_URL), ("locale", locale), ("target", "zh-TW")]:
        text, count = re.subn(rf"(?m)^{key} = .*$", f'{key} = "{value}"', text)
        assert count == 1, key
    return text


def _build(work: Path, data: Path, out: Path, locale: str) -> None:
    config = work / f"config-{locale}.toml"
    config.write_text(_config(locale), encoding="utf-8")
    with mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": GITHUB_REPO}):
        code = main(
            [
                "--config",
                str(config),
                "--data",
                str(data),
                "--db",
                str(work / f"db-{locale}.sqlite"),
                "site",
                "--out",
                str(out),
                "--web",
                str(REPO_ROOT / "web"),
                "--icon",
                str(REPO_ROOT / "assets" / "icon.svg"),
                "--social-preview",
                str(REPO_ROOT / "assets" / "social-preview.png"),
                "--notes",
                str(work / "notes.toml"),
                "--reports",
                str(work / "reports"),
            ]
        )
    assert code == 0


@pytest.fixture(scope="session")
def site_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    work = tmp_path_factory.mktemp("e2e")
    data = work / "data"
    write_fixture_data(data)
    write_reports(work / "reports")
    (work / "notes.toml").write_text(NOTES, encoding="utf-8")
    root = work / "site"
    _build(work, data, root / "zh", "zh-TW")
    _build(work, data, root / "en", "en")
    return root


@pytest.fixture(scope="session")
def server_url(site_root: Path) -> Iterator[str]:
    handler = partial(_QuietHandler, directory=str(site_root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="session")
def base_url(server_url: str) -> str:
    return server_url + "zh/"


@pytest.fixture(scope="session")
def base_url_en(server_url: str) -> str:
    return server_url + "en/"


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
def page(browser: Browser, server_url: str, failed_requests: list[str]) -> Iterator[Page]:
    """每個測試一個新的 context，瀏覽器時鐘固定在 NOW。

    測試結束時不能有 JS 錯誤、失敗的請求，也不能連到本機以外的網址。
    """
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    context.set_default_timeout(5000)
    pg = context.new_page()
    pg.clock.set_fixed_time(NOW)
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
    host = urlsplit(server_url).netloc

    def guard(route: Route) -> None:
        url = route.request.url
        if urlsplit(url).netloc == host:
            route.fallback()
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


Patch = Callable[[str, Callable[[Any], Any]], None]


@pytest.fixture
def patch_json(page: Page) -> Patch:
    """改寫某個 data/<name>.json 的內容，用來呈現網站資料以外的狀態。"""

    def apply(name: str, change: Callable[[Any], Any]) -> None:
        def handler(route: Route) -> None:
            body = route.fetch().json()
            route.fulfill(json=change(body))

        page.route(f"**/data/{name}.json", handler)

    return apply


def store_seen(page: Page, names: list[str]) -> None:
    """模擬先前造訪時看過 names。"""
    seen = json.dumps({n: "2026-11-23" for n in names})
    page.add_init_script(f"localStorage.setItem('starstrata-seen', {json.dumps(seen)})")
