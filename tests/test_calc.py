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
  // JSON 會把 Infinity、NaN 寫成 null；以字串輸出才能和 null 區分
  const finite =(k, v) => (typeof v === 'number' && !Number.isFinite(v) ? String(v) : v);
  process.stdout.write(JSON.stringify(calc[fn](...args), finite));
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
    # 補齊的點不是回補資料，標為 i；有快照的日子仍是 s。
    assert [src for _, _, _, src in got] == ["s", "i", "i", "s"]


@pytest.mark.parametrize(
    ("first", "last", "gains"),
    [(130, 100, [-10, -10, -10]), (130, 129, [0, -1, 0])],
)
def test_snapshot_gap_with_fewer_stars_keeps_negative_gains(
    first: int, last: int, gains: list[int]
) -> None:
    out = call("buildSeries", {"points": [["2026-11-01", first, "s"], ["2026-11-04", last, "s"]]})
    assert [p["n"] for p in out] == [None, *gains]
    assert call("sumLast", out, 7, out[-1]["t"]) == last - first


DAY_MS = 86_400_000


def _points(*gains: int | None) -> list[dict[str, Any]]:
    return [{"t": i * DAY_MS, "n": n} for i, n in enumerate(gains)]


def test_sum_last_ignores_unknown_days() -> None:
    points = _points(None, 5, 7)
    end = 2 * DAY_MS
    assert call("sumLast", points, 7, end) == 12
    assert call("sumLast", points, 2, end) == 12
    assert call("sumLast", points, 1, end) == 7


def test_sum_last_all_unknown_is_null() -> None:
    assert call("sumLast", _points(None), 7, 0) is None


def test_sum_last_counts_days_up_to_end() -> None:
    points = _points(1, 2, 4)
    # 序列在 end 前兩天就結束：只算 end 往前 k 天內的點。
    assert call("sumLast", points, 3, 4 * DAY_MS) == 4
    assert call("sumLast", points, 2, 4 * DAY_MS) is None
    assert call("sumLast", points, 7, 30 * DAY_MS) is None


@pytest.mark.parametrize(
    ("baseline", "expected"),
    [(None, None), (0, None), (-3.5, None), (0.5, 600.0), (100, 3.0)],
)
def test_alert_ratio(baseline: float | None, expected: float | None) -> None:
    assert call("alertRatio", {"added": 300, "baseline": baseline}) == expected
