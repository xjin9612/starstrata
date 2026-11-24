"""web/i18n.js 的語言檔檢查：兩種語言的 key 與變數一致，程式用到的 key 都存在。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

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


def test_keys_used_by_the_page_exist(dicts: dict[str, dict[str, str]]) -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    html = (WEB / "index.html").read_text(encoding="utf-8")
    used = set(re.findall(r"\bt\('([\w.]+)'[,)]", app))
    used |= set(re.findall(r"data-i18n(?:-aria|-placeholder)?=\"([\w.]+)\"", html))
    assert used, "沒有找到任何 key，檢查規則可能失效"
    assert sorted(used - set(dicts["zh-TW"])) == []
