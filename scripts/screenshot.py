"""產生 README 用的 dashboard 截圖。

用 data/ 的快照建置網站，在本機起 HTTP server，以 Playwright 截取 Trending 頁面。
瀏覽器時鐘固定在資料收集完成後 2 小時，截圖不會出現資料過期的提示條。

    .venv/bin/python scripts/screenshot.py [--data data] [--out docs/screenshots/trending.png]
"""

from __future__ import annotations

import argparse
import json
import tempfile
import threading
from datetime import datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from starstrata.cli import main as starstrata

WIDTH, HEIGHT = 1440, 900


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass


def capture(site: Path, out: Path) -> None:
    meta = json.loads((site / "data" / "meta.json").read_text(encoding="utf-8"))
    collected = meta.get("collected_at") or meta["generated_at"]
    now = datetime.fromisoformat(collected) + timedelta(hours=2)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(
                viewport={"width": WIDTH, "height": HEIGHT}, color_scheme="light"
            )
            page.clock.set_fixed_time(now)
            page.goto(f"http://127.0.0.1:{server.server_address[1]}/")
            page.locator(".card").first.wait_for()
            page.wait_for_load_state("networkidle")
            out.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out))
            browser.close()
    finally:
        server.shutdown()
        server.server_close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, default=Path("data"), help="快照根目錄")
    parser.add_argument("--out", type=Path, default=Path("docs/screenshots/trending.png"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "site"
        code = starstrata(
            [
                "--data",
                str(args.data),
                "--db",
                str(Path(tmp) / "db.sqlite"),
                "site",
                "--out",
                str(site),
            ]
        )
        if code != 0:
            return code
        capture(site, args.out)
    print(f"已寫入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
