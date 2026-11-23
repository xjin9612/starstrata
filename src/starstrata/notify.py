"""產生並發送每日摘要與爆紅警示。"""

from __future__ import annotations

import html
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from starstrata.config import NotifyConfig
from starstrata.queries import (
    AlertLine,
    RepoLine,
    day_alerts,
    is_ingested,
    new_repos,
    top_gainers,
)
from starstrata.snapshots import day_dir, write_json

NOTIFIED_FILE = "notified.json"
DESCRIPTION_LIMIT = 120


@dataclass
class NotifyResult:
    summary_sent: bool = False
    alerts_sent: list[str] = field(default_factory=list)


def _link(full_name: str) -> str:
    url = f"https://github.com/{html.escape(full_name, quote=True)}"
    return f'<a href="{url}">{html.escape(full_name)}</a>'


def _description(text: str | None) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) > DESCRIPTION_LIMIT:
        text = text[: DESCRIPTION_LIMIT - 1] + "…"
    return html.escape(text)


def format_summary(
    cfg: NotifyConfig,
    day: date,
    *,
    ingested: bool,
    new: list[RepoLine],
    gainers: list[RepoLine],
    alert_count: int,
) -> str:
    lines = [f"<b>starstrata 每日摘要 {day.isoformat()}</b>"]
    if not ingested:
        lines += ["", "今天沒有匯入任何快照，請檢查 daily workflow。"]
        return "\n".join(lines)

    lines += ["", f"<b>新上榜</b>（共 {len(new)} 個）"]
    for repo in new[: cfg.new_repos_limit]:
        stars = f" ★{repo.stars:,}" if repo.stars is not None else ""
        language = f" · {html.escape(repo.language)}" if repo.language else ""
        lines.append(f"• {_link(repo.full_name)}{stars}{language}")
        if repo.description:
            lines.append(f"  {_description(repo.description)}")
    if len(new) > cfg.new_repos_limit:
        lines.append(f"…另有 {len(new) - cfg.new_repos_limit} 個")
    if not new:
        lines.append("（無）")

    lines += ["", f"<b>日增 star 前 {cfg.top_gainers}</b>"]
    for i, repo in enumerate(gainers, start=1):
        lines.append(f"{i}. {_link(repo.full_name)} +{repo.delta:,}（★{repo.stars:,}）")
    if not gainers:
        lines.append("（無）")

    lines += ["", f"<b>爆紅警示</b>：{alert_count} 則" + ("，另發訊息" if alert_count else "")]
    if cfg.site_url:
        lines += ["", f'<a href="{html.escape(cfg.site_url, quote=True)}">開啟 dashboard</a>']
    return "\n".join(lines)


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
    send: Callable[[str], None],
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
        send(
            format_summary(
                cfg,
                day,
                ingested=ingested,
                new=new_repos(conn, day),
                gainers=top_gainers(conn, day, cfg.top_gainers),
                alert_count=len(alerts),
            )
        )
        result.summary_sent = True
        if ingested:
            summary_sent = True
            save()
    for alert in alerts:
        if alert.repo.node_id in sent_alerts:
            continue
        send(format_alert(day, alert))
        sent_alerts.append(alert.repo.node_id)
        result.alerts_sent.append(alert.repo.node_id)
        save()
    return result
