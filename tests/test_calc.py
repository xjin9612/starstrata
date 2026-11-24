"""web/calc.js 的測試，以 node 執行。"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

CALC_JS = Path(__file__).resolve().parent.parent / "web" / "calc.js"

_DRIVER = """
const calc = require(process.argv[1]);
let input = '';
process.stdin.on('data', c => { input += c; });
process.stdin.on('end', () => {
  const { fn, args } = JSON.parse(input);
  process.stdout.write(JSON.stringify(calc[fn](...args)));
});
"""


def call(fn: str, *args: Any) -> Any:
    node = shutil.which("node")
    if node is None:
        pytest.fail("找不到 node；web/ 的測試需要 Node.js，見 docs/DEVELOPMENT.md")
    result = subprocess.run(
        [node, "-e", _DRIVER, str(CALC_JS)],
        input=json.dumps({"fn": fn, "args": args}),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def series(
    points: list[list[Any]], daily: list[list[Any]]
) -> list[tuple[str, int, int | None, str]]:
    out = call("buildSeries", {"points": points, "daily": daily})
    return [(p["date"], p["c"], p["n"], p["s"]) for p in out]


def test_no_points_gives_null() -> None:
    assert call("buildSeries", {"points": [], "daily": []}) is None


def test_first_snapshot_without_history_has_unknown_gain() -> None:
    assert series([["2026-11-24", 8583, "s"]], []) == [("2026-11-24", 8583, None, "s")]


def test_consecutive_snapshots_use_daily() -> None:
    got = series(
        [["2026-11-01", 100, "s"], ["2026-11-02", 130, "s"]],
        [["2026-11-02", 30, "s"]],
    )
    assert got == [("2026-11-01", 100, None, "s"), ("2026-11-02", 130, 30, "s")]


def test_backfill_gap_days_are_zero() -> None:
    got = series(
        [["2026-11-01", 10, "b"], ["2026-11-05", 20, "b"], ["2026-11-06", 25, "s"]],
        [["2026-11-01", 10, "b"], ["2026-11-05", 10, "b"]],
    )
    assert got == [
        ("2026-11-01", 10, 10, "b"),
        ("2026-11-02", 10, 0, "b"),
        ("2026-11-03", 10, 0, "b"),
        ("2026-11-04", 10, 0, "b"),
        ("2026-11-05", 20, 10, "b"),
        ("2026-11-06", 25, None, "s"),
    ]


def test_snapshot_gap_is_interpolated_and_conserves_total() -> None:
    got = series(
        [["2026-11-01", 100, "s"], ["2026-11-04", 130, "s"]],
        [],
    )
    assert [c for _, c, _, _ in got] == [100, 110, 120, 130]
    assert [n for _, _, n, _ in got] == [None, 10, 10, 10]


def test_sum_last_ignores_unknown_days() -> None:
    points = [{"n": None}, {"n": 5}, {"n": 7}]
    assert call("sumLast", points, 7) == 12
    assert call("sumLast", points, 2) == 12
    assert call("sumLast", points, 1) == 7


def test_sum_last_all_unknown_is_null() -> None:
    assert call("sumLast", [{"n": None}], 7) is None


@pytest.mark.parametrize(
    ("baseline", "expected"),
    [(None, None), (0, None), (-3.5, None), (0.5, 600.0), (100, 3.0)],
)
def test_alert_ratio(baseline: float | None, expected: float | None) -> None:
    assert call("alertRatio", {"added": 300, "baseline": baseline}) == expected
