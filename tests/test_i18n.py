"""web/i18n.js 的語言檔檢查：兩種語言的 key 與變數一致，程式用到的 key 都存在。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from starstrata.db import ACTIVE, DORMANT, GONE

WEB = Path(__file__).resolve().parent.parent / "web"
_LOAD = (
    "global.window = {}; require(process.argv[1]);"
    " process.stdout.write(JSON.stringify(window.I18N));"
)


@pytest.fixture(scope="module")
def dicts() -> dict[str, dict[str, str]]:
    node = shutil.which("node")
    if node is None:
        pytest.fail("找不到 node；web/ 的測試需要 Node.js，見 docs/DEVELOPMENT.md")
    result = subprocess.run(
        [node, "-e", _LOAD, str(WEB / "i18n.js")], capture_output=True, text=True, check=True
    )
    loaded: dict[str, dict[str, str]] = json.loads(result.stdout)
    return loaded


def test_languages_have_same_keys_and_variables(dicts: dict[str, dict[str, str]]) -> None:
    zh, en = dicts["zh-TW"], dicts["en"]
    assert set(zh) == set(en)
    variables = {k: set(re.findall(r"\{(\w+)\}", v)) for k, v in zh.items()}
    assert {k: set(re.findall(r"\{(\w+)\}", v)) for k, v in en.items()} == variables


@pytest.fixture(scope="module")
def app() -> str:
    return (WEB / "app.js").read_text(encoding="utf-8")


def _js_array(app: str, name: str) -> list[str]:
    m = re.search(rf"const {name} = \[([^\]]*)\]", app)
    assert m, name
    return re.findall(r"'([^']*)'", m.group(1))


def _dynamic_keys(app: str) -> dict[str, list[str]]:
    """app.js 以「字面前綴 + 變數」組成的 key：前綴（或以 . 開頭的後綴）對應變數所有可能的值。"""
    routes = [*_js_array(app, "ROUTES"), "tracked"]
    periods = _js_array(app, "PERIOD_ORDER")
    tracked_sorts = _js_array(app, "TRACKED_SORTS")
    kinds = _js_array(app, "REPORT_KINDS")
    ranges = re.search(r"const RANGES = \{([^}]*)\}", app)
    assert ranges
    return {
        "period.": periods,
        "unit.": periods,
        "route.": routes,
        "sort.": ["rank", "stars", *tracked_sorts],
        "tracked.by.": tracked_sorts,
        "state.": [ACTIVE, DORMANT, GONE],
        "chart.r": re.findall(r"'?(\w+)'?:", ranges.group(1)),
        "theme.": _js_array(app, "THEMES"),
        "reports.kind.": ["all", *kinds],
        "reports.title.": kinds,
        # skeletonHtml：trending、alerts、reports、report、repo 以外的頁面
        ".title": ["new", "daily", "tracked"],
    }


def test_dynamic_keys_are_all_listed(app: str) -> None:
    prefixes = set(re.findall(r"\bt\('([\w.]+)'\s*\+", app))
    suffixes = set(re.findall(r"\bt\([\w.]+\s*\+\s*'(\.[\w.]+)'\)", app))
    assert prefixes, "沒有找到任何動態 key，檢查規則可能失效"
    assert prefixes | suffixes == set(_dynamic_keys(app))


def test_keys_used_by_the_page_exist(dicts: dict[str, dict[str, str]], app: str) -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    # 寫成字面的 key，包含 t() 的參數、三元運算式的兩支、陣列與物件裡的 key；不含動態 key 的前綴。
    used = set(re.findall(r"'([a-z]\w*(?:\.\w+)+)'(?!\s*\+)", app))
    used |= set(re.findall(r"data-i18n(?:-aria|-placeholder)?=\"([\w.]+)\"", html))
    for affix, values in _dynamic_keys(app).items():
        used |= {v + affix if affix.startswith(".") else affix + v for v in values}
    assert {"card.upSr", "kpi.new", "chart.measured", "period.weekly", "chart.rall"} <= used
    assert sorted(used - set(dicts["zh-TW"])) == []
