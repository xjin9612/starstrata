"""產生並發送每日摘要與爆紅警示。"""

from __future__ import annotations

import html
import json
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import quote

from starstrata.config import NotifyConfig
from starstrata.ingest import freeze_alerts
from starstrata.messages import Messages
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
from starstrata.snapshots import RUN_FILE, day_dir, write_json
from starstrata.telegram import Button
from starstrata.translate import lookup

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
    # 說明的鍵 → translation.target 的譯文，見 translate.text_key。
    translations: Mapping[str, str] = field(default_factory=dict)


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


def _trending_lines(data: SummaryData, m: Messages) -> list[str]:
    lines = ["", f"<b>{m.trending_heading}</b>"]
    for i, t in enumerate(data.trending):
        gain = f"<b>+{t.gain:,}</b>" if t.gain is not None else ""
        facts = _facts(t.language, t.stars)
        if i < len(MEDALS):
            lines.append(f"{MEDALS[i]} {_link(t.full_name)}{_marks(t.node_id, data)}")
            detail = [m.gain_today.format(gain=gain)] if gain else []
            lines.append(INDENT + " · ".join(detail + facts))
            description = lookup(data.translations, t.description) or t.description
            if description:
                lines.append(INDENT + _description(description, TOP_DESCRIPTION_LIMIT))
        else:
            head = f"{i + 1}. {_link(t.full_name)}{_marks(t.node_id, data)}"
            lines.append(f"{head}  {gain}" if gain else head)
            if facts:
                lines.append(INDENT + " · ".join(facts))
    return lines


def format_summary(day: date, data: SummaryData | None, m: Messages) -> str:
    """data 為 None 表示當天沒有匯入快照。"""
    lines = [f"<b>{m.summary_title}</b>"]
    if data is None:
        lines += [day.isoformat(), "", m.no_snapshot]
        return "\n".join(lines)

    lines.append(m.tracking.format(day=day.isoformat(), n=f"{data.active:,}"))
    if data.first_day:
        lines += ["", m.first_day]
    if data.trending:
        lines += _trending_lines(data, m)
    if data.gainers:
        lines += ["", f"<b>{m.gainers_heading}</b>{m.gainers_scope}"]
        for i, repo in enumerate(data.gainers, start=1):
            stars = f" · ★{short_count(repo.stars)}" if repo.stars is not None else ""
            lines.append(
                f"{i}. {_link(repo.full_name)}{_marks(repo.node_id, data)}"
                f"  <b>+{repo.delta:,}</b>{stars}"
            )
    stats = [] if data.first_day else [m.stat_new.format(n=len(data.new_ids))]
    stats.append(m.stat_alerts.format(n=len(data.alert_ids)))
    lines += ["", " · ".join(stats)]
    return "\n".join(lines)


def summary_message(cfg: NotifyConfig, day: date, data: SummaryData | None, m: Messages) -> Message:
    buttons = (Button(m.open_dashboard, cfg.site_url),) if cfg.site_url else ()
    return Message(format_summary(day, data, m), silent=True, buttons=buttons)


def repo_page_url(site_url: str, full_name: str) -> str:
    """dashboard 的 repo 詳細頁網址，格式與 web/app.js 的 repoHref 一致。"""
    return f"{site_url}#/repo/" + "/".join(quote(part, safe="") for part in full_name.split("/"))


def alert_message(
    cfg: NotifyConfig,
    day: date,
    alert: AlertLine,
    m: Messages,
    translations: Mapping[str, str] | None = None,
) -> Message:
    buttons = [Button("GitHub", f"https://github.com/{quote(alert.repo.full_name)}")]
    if cfg.site_url:
        buttons.append(Button(m.repo_page, repo_page_url(cfg.site_url, alert.repo.full_name)))
    return Message(format_alert(day, alert, m, translations), silent=False, buttons=tuple(buttons))


def format_alert(
    day: date, alert: AlertLine, m: Messages, translations: Mapping[str, str] | None = None
) -> str:
    repo = alert.repo
    baseline = (
        m.alert_no_baseline
        if alert.baseline is None
        else m.alert_baseline.format(value=f"{alert.baseline:+,.1f}")
    )
    lines = [
        f"🔥 <b>{m.alert_title}</b> {day.isoformat()}",
        _link(repo.full_name),
        m.alert_daily.format(n=f"{alert.daily_stars:,}", baseline=baseline),
    ]
    detail = []
    if repo.stars is not None:
        detail.append(m.alert_total.format(n=f"{repo.stars:,}"))
    if repo.language:
        detail.append(html.escape(repo.language))
    if detail:
        lines.append(" · ".join(detail))
    description = lookup(translations or {}, repo.description) or repo.description
    if description:
        lines.append(_description(description))
    return "\n".join(lines)


def _repos_complete(data_root: Path, day: date) -> bool:
    """當天 run.json 合併後沒有缺少快照的 repo；沒有 run.json 或沒有 merged 時視為不完整。"""
    path = day_dir(data_root, day) / RUN_FILE
    if not path.is_file():
        return False
    merged = json.loads(path.read_text(encoding="utf-8")).get("merged")
    return isinstance(merged, dict) and merged.get("repos_missing") == []


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
    *,
    messages: Messages,
    translations: Mapping[str, str] | None = None,
) -> NotifyResult:
    """發送 day 的摘要與每則警示。

    當天的 repos 收集完整時，發送前先凍結當天的警示（ingest.freeze_alerts），之後重跑或重建都不會
    改變已發出的警示。不完整時不凍結，重跑補齊資料後重新判定，新的警示照常發送。
    已發送的項目記在當天快照目錄的 notified.json，同一天重跑不會重複發送。
    每發送成功一則就更新紀錄，中途失敗時已發送的項目不會再發。
    當天尚未匯入快照時發送的提醒不記錄，補上快照後重跑會發送正式摘要。
    """
    log_path = day_dir(data_root, day) / NOTIFIED_FILE
    summary_sent, sent_alerts = _load_log(log_path)
    result = NotifyResult()

    def save() -> None:
        write_json(log_path, {"summary": summary_sent, "alerts": sent_alerts})

    if _repos_complete(data_root, day):
        with conn:
            freeze_alerts(conn, data_root, day)
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
                translations=translations or {},
            )
            if ingested
            else None
        )
        send(summary_message(cfg, day, data, messages))
        result.summary_sent = True
        if ingested:
            summary_sent = True
            save()
    for alert in alerts:
        if alert.repo.node_id in sent_alerts:
            continue
        send(alert_message(cfg, day, alert, messages, translations))
        sent_alerts.append(alert.repo.node_id)
        result.alerts_sent.append(alert.repo.node_id)
        save()
    return result
