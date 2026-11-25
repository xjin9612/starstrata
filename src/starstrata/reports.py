"""產生週報與月報，重點專案附上 AI 模型的解讀。文字依 i18n.locale 取自 messages。"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from starstrata import gemini
from starstrata.messages import MESSAGES, Messages

log = logging.getLogger(__name__)

WEEKLY = "weekly"
MONTHLY = "monthly"

# 各語言的段落標題與行首標記。報告可能以先前的 locale 產生，解析時每種語言都要認得。
GAINERS_HEADINGS = tuple(m.gainers_report_heading for m in MESSAGES.values())
_INTERPRETATION_HEADINGS = tuple(f"## {m.interpretation_heading}" for m in MESSAGES.values())
_MISSING_MARKERS = tuple(m.missing_marker for m in MESSAGES.values())

# 報告列出的排行數量。
TOP_GAINERS = 20
TOP_NEW = 10
# 送給模型的 README 字元上限。
README_LIMIT = 4000

# 期間結束後幾天內，`report due` 才重新產生標示缺漏的報告。
RETRY_DAYS = 3


@dataclass(frozen=True)
class Period:
    kind: str
    start: date
    end: date

    @property
    def label(self) -> str:
        if self.kind == WEEKLY:
            year, week, _ = self.start.isocalendar()
            return f"{year}-W{week:02d}"
        return f"{self.start.year}-{self.start.month:02d}"

    def title(self, m: Messages) -> str:
        name = m.weekly if self.kind == WEEKLY else m.monthly
        return m.report_title.format(name=name, label=self.label, start=self.start, end=self.end)


def period_before(kind: str, run_day: date) -> Period:
    """run_day 之前最近一個完整的週（週一到週日）或月。"""
    if kind == WEEKLY:
        this_monday = run_day - timedelta(days=run_day.weekday())
        start = this_monday - timedelta(days=7)
        return Period(kind, start, start + timedelta(days=6))
    first = run_day.replace(day=1)
    end = first - timedelta(days=1)
    return Period(kind, end.replace(day=1), end)


def due_periods(run_day: date) -> list[Period]:
    """run_day 之前最近一個完整的週與月。

    每天都檢查這兩期：報告不存在時產生，讓漏跑的週一或 1 日之後自動補上；
    標示缺漏的報告只在 should_retry 成立時重新產生。
    """
    return [period_before(WEEKLY, run_day), period_before(MONTHLY, run_day)]


def should_retry(period: Period, run_day: date) -> bool:
    return (run_day - period.end).days <= RETRY_DAYS


def interpretation_count(text: str) -> int:
    """報告中已有解讀的專案數。"""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line in _INTERPRETATION_HEADINGS), None)
    if start is None:
        return 0
    count = 0
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        count += line.startswith("### ")
    return count


def has_missing(text: str) -> bool:
    return any(line.startswith(_MISSING_MARKERS) for line in text.splitlines())


def report_locale(text: str) -> str | None:
    """依「Star 增加最多」段落的標題判斷報告的語言；認不出時回傳 None。"""
    lines = set(text.splitlines())
    return next(
        (loc for loc, m in MESSAGES.items() if f"## {m.gainers_report_heading}" in lines), None
    )


def has_data(conn: sqlite3.Connection, period: Period) -> bool:
    row = conn.execute(
        "SELECT 1 FROM ingested_dates WHERE date BETWEEN ? AND ? LIMIT 1",
        (period.start.isoformat(), period.end.isoformat()),
    ).fetchone()
    return row is not None


def report_path(root: Path, period: Period) -> Path:
    return root / period.kind / f"{period.label}.md"


def parse_period(kind: str, label: str) -> Period | None:
    """report_path 檔名的反向：由種類與 label（2026-W47、2026-11）還原期間。

    label 不是 Period.label 的正規寫法時回傳 None，避免兩個檔名對應到同一期。
    """
    try:
        if kind == WEEKLY:
            year, week = label.split("-W")
            start = date.fromisocalendar(int(year), int(week), 1)
            period = Period(kind, start, start + timedelta(days=6))
        elif kind == MONTHLY:
            year, month = label.split("-")
            start = date(int(year), int(month), 1)
            end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
            period = Period(kind, start, end)
        else:
            return None
    except ValueError:
        return None
    return period if period.label == label else None


@dataclass(frozen=True)
class Gainer:
    node_id: str
    full_name: str
    description: str | None
    topics: list[str]
    language: str | None
    stars: int
    gained: int


@dataclass(frozen=True)
class Interpretation:
    what: str
    why: str


def gainers(
    conn: sqlite3.Connection, period: Period, limit: int, base_lookback_days: int
) -> list[Gainer]:
    """期間內 star 增加最多的 repo。

    增加量 = 期間內最後一筆快照 − 期間開始前 base_lookback_days 天內最後一筆快照；
    這段時間沒有快照時，改用期間內第一筆，避免把期間外的增加量算進來。
    """
    start, end = period.start.isoformat(), period.end.isoformat()
    lookback = (period.start - timedelta(days=base_lookback_days)).isoformat()
    rows = conn.execute(
        """
        WITH last AS (
          SELECT node_id, max(date) AS d FROM repo_daily
          WHERE date BETWEEN :start AND :end GROUP BY node_id
        ),
        base AS (
          SELECT l.node_id, coalesce(
            (SELECT max(date) FROM repo_daily b
             WHERE b.node_id = l.node_id AND b.date >= :lookback AND b.date < :start),
            (SELECT min(date) FROM repo_daily b
             WHERE b.node_id = l.node_id AND b.date BETWEEN :start AND :end)
          ) AS d
          FROM last l
        )
        SELECT r.node_id, r.full_name, r.description, r.topics, r.language,
               e.stars AS stars, e.stars - s.stars AS gained
        FROM last l
        JOIN base b ON b.node_id = l.node_id
        JOIN repo_daily e ON e.node_id = l.node_id AND e.date = l.d
        JOIN repo_daily s ON s.node_id = b.node_id AND s.date = b.d
        JOIN repos r ON r.node_id = l.node_id
        WHERE e.stars - s.stars > 0
        ORDER BY gained DESC, r.full_name
        LIMIT :limit
        """,
        {"start": start, "end": end, "lookback": lookback, "limit": limit},
    )
    return [
        Gainer(
            node_id=r["node_id"],
            full_name=r["full_name"],
            description=r["description"],
            topics=json.loads(r["topics"]) if r["topics"] else [],
            language=r["language"],
            stars=int(r["stars"]),
            gained=int(r["gained"]),
        )
        for r in rows
    ]


def _cell(text: object) -> str:
    """Markdown 表格儲存格：壓成單行並跳脫直線符號。"""
    return " ".join(str(text).split()).replace("|", "\\|") if text is not None else "—"


_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#!|~])")


_URL_CHARS = r"[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]"
_URL = re.compile(rf"(?i)(?:[a-z][a-z0-9+.-]*://|www\.){_URL_CHARS}+")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _plain(text: str, m: Messages) -> str:
    """把模型的輸出當純文字寫進 Markdown：壓成單行、移除網址與 email、跳脫 Markdown 符號。

    解讀內容來自含 README 的 prompt，不能讓它在報告中產生連結、圖片或 HTML。
    跳脫 < 與 > 後 HTML 標籤只會顯示成文字。網址只比對 ASCII 字元，不吃掉後面的中文。
    """
    # GitHub 會把純文字網址與 email 自動轉成連結。
    text = _URL.sub(m.url_removed, text)
    text = _EMAIL.sub(m.email_removed, text)
    return _MARKDOWN_SPECIAL.sub(r"\\\1", " ".join(text.split()))


def _link(full_name: str) -> str:
    return f"[{full_name}](https://github.com/{full_name})"


def model_label(models: Sequence[str], m: Messages) -> str:
    """報告中的模型說明，例如 `a`，或 `a`（無法使用時改用 `b`）。"""
    label = f"`{models[0]}`"
    if len(models) > 1:
        fallbacks = m.list_separator.join(f"`{name}`" for name in models[1:])
        label += m.model_fallback.format(models=fallbacks)
    return label


def render(
    conn: sqlite3.Connection,
    period: Period,
    top: list[Gainer],
    interpretations: dict[str, Interpretation] | None,
    note: str | None,
    model: str,
    m: Messages,
) -> str:
    """model 是 model_label 的結果。

    note 不為 None 時，在解讀段落開頭加上這一行（以 missing_marker 或 skipped_marker 開頭）。
    """
    start, end = period.start.isoformat(), period.end.isoformat()
    # 數字只用期間內的資料；repo 名稱、語言與解讀取自產生當下。
    observed = conn.execute(
        "SELECT count(DISTINCT node_id) FROM repo_daily WHERE date BETWEEN ? AND ?",
        (start, end),
    ).fetchone()[0]
    # 第一個匯入日是建立基準的日子，當天出現的 repo 不算新上榜。
    baseline_day = conn.execute("SELECT min(date) FROM ingested_dates").fetchone()[0]
    new = list(
        conn.execute(
            "SELECT r.full_name, r.first_seen, r.language, "
            "(SELECT s.stars FROM repo_daily s WHERE s.node_id = r.node_id AND s.date <= ? "
            " ORDER BY s.date DESC LIMIT 1) AS stars "
            "FROM repos r WHERE r.first_seen BETWEEN ? AND ? AND r.first_seen IS NOT ? "
            "ORDER BY coalesce(stars, 0) DESC, r.full_name",
            (end, start, end, baseline_day),
        )
    )
    baseline_note = (
        m.baseline_excluded.format(day=baseline_day)
        if baseline_day is not None and start <= baseline_day <= end
        else ""
    )
    alerts = list(
        conn.execute(
            "SELECT a.date, a.daily_stars, r.full_name FROM alerts a "
            "JOIN repos r ON r.node_id = a.node_id WHERE a.date BETWEEN ? AND ? "
            "ORDER BY a.date, a.daily_stars DESC",
            (start, end),
        )
    )

    lines = [f"# {period.title(m)}", "", f"## {m.summary_heading}", ""]
    lines.append("- " + m.observed.format(n=f"{observed:,}"))
    lines.append("- " + m.new_count.format(n=f"{len(new):,}", note=baseline_note))
    lines.append("- " + m.alert_count.format(n=f"{len(alerts):,}"))

    lines += ["", f"## {m.gainers_report_heading}", ""]
    if top:
        lines += [m.gainers_table, "|---:|---|---|---:|---:|"]
        lines += [
            f"| {i} | {_link(g.full_name)} | {_cell(g.language)} | +{g.gained:,} | {g.stars:,} |"
            for i, g in enumerate(top, start=1)
        ]
    else:
        lines.append(m.no_data)

    lines += ["", f"## {m.interpretation_heading}", ""]
    if note is not None:
        lines.append(note)
    if interpretations:
        if note is not None:
            lines.append("")
        lines.append("> " + m.disclaimer.format(model=model))
        for i, g in enumerate([g for g in top if g.full_name in interpretations], start=1):
            item = interpretations[g.full_name]
            lines += [
                "",
                f"### {i}. {_link(g.full_name)}",
                "",
                "- " + m.what.format(text=_plain(item.what, m)),
                "- " + m.why.format(text=_plain(item.why, m)),
            ]
    elif note is None:
        lines.append(m.no_interpretation)

    lines += ["", f"## {m.new_heading}", ""]
    if new:
        lines += [m.new_table, "|---|---|---|---:|"]
        lines += [
            f"| {_link(r['full_name'])} | {r['first_seen']} | {_cell(r['language'])} | "
            f"{(r['stars'] or 0):,} |"
            for r in new[:TOP_NEW]
        ]
        if len(new) > TOP_NEW:
            lines.append("\n" + m.more_new.format(n=f"{len(new) - TOP_NEW:,}"))
    else:
        lines.append(m.no_new)

    lines += ["", f"## {m.alerts_heading}", ""]
    if alerts:
        lines += [m.alerts_table, "|---|---|---:|"]
        lines += [
            f"| {a['date']} | {_link(a['full_name'])} | +{a['daily_stars']:,} |" for a in alerts
        ]
    else:
        lines.append(m.no_alerts)
    return "\n".join(lines) + "\n"


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "full_name": {"type": "string"},
                    "what": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["full_name", "what", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}


class InterpretError(Exception):
    """模型沒有回傳可用的解讀。"""


def _data(text: str) -> str:
    """repo 作者寫的內容不能結束或開啟 <repo> 標籤。"""
    return re.sub(r"(?i)<\s*/?\s*repo\s*>", "[repo]", text)


def build_prompt(
    period: Period, top: Sequence[Gainer], readmes: dict[str, str], m: Messages
) -> str:
    parts = [m.prompt_intro.format(start=period.start, end=period.end)]
    for g in top:
        readme = _data(readmes.get(g.full_name, "")[:README_LIMIT])
        parts.append(
            "<repo>\n"
            f"full_name: {g.full_name}\n"
            f"language: {g.language or m.prompt_unknown}\n"
            f"topics: {', '.join(g.topics) or m.prompt_none}\n"
            f"stars: {m.prompt_gained.format(stars=g.stars, gained=g.gained)}\n"
            f"description: {_data(g.description or m.prompt_none)}\n"
            f"readme:\n{readme or m.prompt_none}\n"
            "</repo>"
        )
    return "\n\n".join(parts)


def interpret_with_claude(
    client: Any, model: str, system: str, prompt: str, expected: Sequence[str]
) -> dict[str, Interpretation]:
    """呼叫 Claude 取得每個專案的解讀，只保留 expected 內的 full_name。

    client 是 anthropic.Anthropic。模型拒答時改由伺服器端 fallback 模型處理；
    整條 fallback 都拒答、輸出被截斷或格式不符時丟出 InterpretError。
    """
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        output_config={
            "effort": "medium",
            "format": {"type": "json_schema", "schema": _SCHEMA},
        },
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason != "end_turn":
        raise InterpretError(f"Claude 回應中止：{response.stop_reason}")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise InterpretError("Claude 回應沒有文字內容")
    return parse_interpretations(text, expected, "Claude")


def interpret_with_gemini(
    api_key: str,
    models: Sequence[str],
    system: str,
    prompt: str,
    expected: Sequence[str],
    *,
    transport: Any = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Interpretation]:
    """呼叫 Gemini 取得每個專案的解讀，只保留 expected 內的 full_name。

    被擋下、輸出被截斷、HTTP 錯誤或格式不符時丟出 InterpretError。
    """
    try:
        text = gemini.generate_json_with_fallback(
            api_key, models, system, prompt, _SCHEMA, transport=transport, sleep=sleep
        )
    except gemini.GeminiError as e:
        raise InterpretError(str(e)) from e
    return parse_interpretations(text, expected, "Gemini")


def parse_interpretations(
    text: str, expected: Sequence[str], source: str
) -> dict[str, Interpretation]:
    """解析 {"items": [...]}，只保留 expected 內的 full_name 與格式正確的項目。"""
    try:
        items = json.loads(text)["items"]
    except (ValueError, KeyError, TypeError) as e:
        raise InterpretError(f"{source} 回應格式不符：{e}") from e
    if not isinstance(items, list):
        raise InterpretError(f"{source} 回應格式不符：items 不是陣列")
    wanted = set(expected)
    result: dict[str, Interpretation] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("full_name") not in wanted:
            continue
        what, why = item.get("what"), item.get("why")
        if isinstance(what, str) and isinstance(why, str):
            result[item["full_name"]] = Interpretation(what=what, why=why)
    return result


# (system prompt, prompt, 預期的 full_name) → 解讀。
Interpreter = Callable[[str, str, Sequence[str]], dict[str, Interpretation]]
ReadmeFetcher = Callable[[str], str | None]


def generate(
    conn: sqlite3.Connection,
    period: Period,
    *,
    llm_top_n: int,
    model: str,
    base_lookback_days: int,
    interpreter: Interpreter | None,
    fetch_readme: ReadmeFetcher | None,
    messages: Messages,
) -> tuple[str, bool]:
    """回傳 (報告內容, 解讀是否完整)。interpreter 為 None 表示未設定 API key。

    README 抓取失敗只少了該 repo 的 README，不影響報告產生。
    """
    m = messages
    top = gainers(conn, period, TOP_GAINERS, base_lookback_days)
    focus = top[:llm_top_n]
    if not focus:
        return render(conn, period, top, {}, None, model, m), True
    if interpreter is None:
        note = m.skipped_marker + m.skipped_no_key
        return render(conn, period, top, None, note, model, m), True
    readmes: dict[str, str] = {}
    if fetch_readme is not None:
        for g in focus:
            try:
                text = fetch_readme(g.full_name)
            except Exception as e:  # README 只是補充資料，任何錯誤都略過這個 repo 的 README。
                log.warning("無法取得 %s 的 README：%s", g.full_name, e)
                continue
            if text:
                readmes[g.full_name] = text
    try:
        result = interpreter(
            m.system_prompt, build_prompt(period, focus, readmes, m), [g.full_name for g in focus]
        )
    except InterpretError as e:
        log.warning("解讀失敗：%s", e)
        note = m.missing_marker + m.interpret_failed.format(error=e)
        return render(conn, period, top, None, note, model, m), False
    missing = [g.full_name for g in focus if g.full_name not in result]
    if not missing:
        return render(conn, period, top, result, None, model, m), True
    log.warning("模型沒有回傳這些 repo 的解讀：%s", ", ".join(missing))
    names = m.list_separator.join(missing)
    note = m.missing_marker + m.interpret_missing.format(names=names)
    return render(conn, period, top, result, note, model, m), False
