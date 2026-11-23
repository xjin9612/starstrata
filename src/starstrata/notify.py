"""產生並發送每日摘要與爆紅警示。"""

from __future__ import annotations

import html
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import quote

from starstrata.config import NotifyConfig
from starstrata.queries import (
    AlertLine,
    RepoLine,
    TrendingLine,
    active_count,
    day_alerts,
    is_first_day,
    is_ingested,
    new_repos,
    top_gainers,
    trending_top,
)
from starstrata.snapshots import day_dir, write_json
from starstrata.telegram import Button

NOTIFIED_FILE = "notified.json"
DESCRIPTION_LIMIT = 120
# 今日新增最多的前 3 名附說明，長度上限較短。
TOP_DESCRIPTION_LIMIT = 50
MEDALS = ("🥇", "🥈", "🥉")
INDENT = "      "


@dataclass
class NotifyResult:
    summary_sent: bool = False
    alerts_sent: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Message:
    text: str
    # True 時收訊者不會收到通知音。
    silent: bool
    buttons: tuple[Button, ...] = ()


@dataclass(frozen=True)
class SummaryData:
    active: int
    # day 之前沒有匯入過任何日期；新上榜與日增還沒有比較基準。
    first_day: bool
    trending: list[TrendingLine]
    gainers: list[RepoLine]
    new_ids: frozenset[str]
    alert_ids: frozenset[str]


def _link(full_name: str) -> str:
    url = f"https://github.com/{html.escape(full_name, quote=True)}"
    return f'<a href="{url}">{html.escape(full_name)}</a>'


def _description(text: str | None, limit: int = DESCRIPTION_LIMIT) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return html.escape(text)


def short_count(n: int) -> str:
    """1234 → 1.2k、1234567 → 1.2M，千以下原樣。"""
    for unit, size in (("M", 1_000_000), ("k", 1_000)):
        if n >= size or (unit == "M" and round(n / 1_000, 1) >= 1_000):
            return f"{n / size:.1f}".removesuffix(".0") + unit
    return str(n)


def _marks(node_id: str | None, data: SummaryData) -> str:
    if node_id is None:
        return ""
    marks = ""
    if node_id in data.new_ids and not data.first_day:
        marks += " 🆕"
    if node_id in data.alert_ids:
        marks += " 🔥"
    return marks


def _facts(language: str | None, stars: int | None) -> list[str]:
    facts = [html.escape(language)] if language else []
    if stars is not None:
        facts.append(f"★{short_count(stars)}")
    return facts


def _trending_lines(data: SummaryData) -> list[str]:
    lines = ["", "<b>📈 今日新增最多</b>"]
    for i, t in enumerate(data.trending):
        gain = f"<b>+{t.gain:,}</b>" if t.gain is not None else ""
        facts = _facts(t.language, t.stars)
        if i < len(MEDALS):
            lines.append(f"{MEDALS[i]} {_link(t.full_name)}{_marks(t.node_id, data)}")
            detail = [f"{gain} 今日"] if gain else []
            lines.append(INDENT + " · ".join(detail + facts))
            if t.description:
                lines.append(INDENT + _description(t.description, TOP_DESCRIPTION_LIMIT))
        else:
            head = f"{i + 1}. {_link(t.full_name)}{_marks(t.node_id, data)}"
            lines.append(f"{head}  {gain}" if gain else head)
            if facts:
                lines.append(INDENT + " · ".join(facts))
    return lines


def format_summary(day: date, data: SummaryData | None) -> str:
    """data 為 None 表示當天沒有匯入快照。"""
    lines = ["<b>⭐ starstrata 每日摘要</b>"]
    if data is None:
        lines += [day.isoformat(), "", "今天沒有匯入任何快照，請檢查 daily workflow。"]
        return "\n".join(lines)

    lines.append(f"{day.isoformat()} · 追蹤 {data.active:,} 個 repo")
    if data.first_day:
        lines += ["", "首日建立基準，新上榜與日增明天開始計算。"]
    if data.trending:
        lines += _trending_lines(data)
    if data.gainers:
        lines += ["", "<b>🚀 日增最多</b>（追蹤中的 repo）"]
        for i, repo in enumerate(data.gainers, start=1):
            stars = f" · ★{short_count(repo.stars)}" if repo.stars is not None else ""
            lines.append(
                f"{i}. {_link(repo.full_name)}{_marks(repo.node_id, data)}"
                f"  <b>+{repo.delta:,}</b>{stars}"
            )
    stats = [] if data.first_day else [f"新上榜 {len(data.new_ids)}"]
    stats.append(f"爆紅 {len(data.alert_ids)}")
    lines += ["", " · ".join(stats)]
    return "\n".join(lines)


def summary_message(cfg: NotifyConfig, day: date, data: SummaryData | None) -> Message:
    buttons = (Button("開啟 dashboard", cfg.site_url),) if cfg.site_url else ()
    return Message(format_summary(day, data), silent=True, buttons=buttons)


def repo_page_url(site_url: str, node_id: str) -> str:
    """dashboard 的 repo 詳細頁網址，格式與 web/app.js 的路由一致。"""
    return f"{site_url}#/repo/{quote(node_id, safe='')}"


def alert_message(cfg: NotifyConfig, day: date, alert: AlertLine) -> Message:
    buttons = [Button("GitHub", f"https://github.com/{quote(alert.repo.full_name)}")]
    if cfg.site_url:
        buttons.append(Button("詳細頁", repo_page_url(cfg.site_url, alert.repo.node_id)))
    return Message(format_alert(day, alert), silent=False, buttons=tuple(buttons))


def format_alert(day: date, alert: AlertLine) -> str:
    repo = alert.repo
    baseline = "基準資料不足" if alert.baseline is None else f"近期平均 +{alert.baseline:,.1f}/日"
    lines = [
        f"🔥 <b>爆紅警示</b> {day.isoformat()}",
        _link(repo.full_name),
        f"日增 ★+{alert.daily_stars:,}，{baseline}",
    ]
    detail = []
    if repo.stars is not None:
        detail.append(f"目前 ★{repo.stars:,}")
    if repo.language:
        detail.append(html.escape(repo.language))
    if detail:
        lines.append(" · ".join(detail))
    if repo.description:
        lines.append(_description(repo.description))
    return "\n".join(lines)


def _load_log(path: Path) -> tuple[bool, list[str]]:
    """回傳 (摘要是否已發送, 已發送警示的 node_id)。"""
    if not path.is_file():
        return False, []
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return bool(loaded.get("summary")), [str(a) for a in loaded.get("alerts", [])]


def notify(
    conn: sqlite3.Connection,
    cfg: NotifyConfig,
    day: date,
    data_root: Path,
    send: Callable[[Message], None],
) -> NotifyResult:
    """發送 day 的摘要與每則警示。

    已發送的項目記在當天快照目錄的 notified.json，同一天重跑不會重複發送。
    每發送成功一則就更新紀錄，中途失敗時已發送的項目不會再發。
    當天尚未匯入快照時發送的提醒不記錄，補上快照後重跑會發送正式摘要。
    """
    log_path = day_dir(data_root, day) / NOTIFIED_FILE
    summary_sent, sent_alerts = _load_log(log_path)
    result = NotifyResult()

    def save() -> None:
        write_json(log_path, {"summary": summary_sent, "alerts": sent_alerts})

    alerts = day_alerts(conn, day)
    if not summary_sent:
        ingested = is_ingested(conn, day)
        data = (
            SummaryData(
                active=active_count(conn),
                first_day=is_first_day(conn, day),
                trending=trending_top(conn, day, cfg.trending_top),
                gainers=top_gainers(conn, day, cfg.top_gainers),
                new_ids=frozenset(r.node_id for r in new_repos(conn, day)),
                alert_ids=frozenset(a.repo.node_id for a in alerts),
            )
            if ingested
            else None
        )
        send(summary_message(cfg, day, data))
        result.summary_sent = True
        if ingested:
            summary_sent = True
            save()
    for alert in alerts:
        if alert.repo.node_id in sent_alerts:
            continue
        send(alert_message(cfg, day, alert))
        sent_alerts.append(alert.repo.node_id)
        result.alerts_sent.append(alert.repo.node_id)
        save()
    return result
