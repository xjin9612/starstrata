"""命令列入口。指令說明見 docs/DEVELOPMENT.md「命令列」。"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sqlite3
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from starstrata import db, notify, reports, tracking, translate
from starstrata.backfill import run_backfill
from starstrata.collect import collect, run_date
from starstrata.config import Config, ConfigError, ReportsConfig, load_config
from starstrata.github.http import GitHubClient
from starstrata.ingest import SyncResult, sync
from starstrata.messages import MESSAGES
from starstrata.notes import load_notes
from starstrata.site import build_site
from starstrata.site_page import SOCIAL_PREVIEW, detect_repo
from starstrata.snapshots import RepoMeta, write_text_atomic
from starstrata.telegram import TelegramError, TelegramSender

log = logging.getLogger("starstrata")

# 有資料來源失敗；已取得的資料照常寫入。
EXIT_PARTIAL = 1
# 設定錯誤或缺少必要的環境變數，沒有執行任何工作。
EXIT_USAGE = 2


class UsageError(Exception):
    pass


def _token(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise UsageError(f"缺少環境變數 {name}")
    return value


def _report_sync(result: SyncResult) -> None:
    if result.rebuilt:
        log.info("已重建 DB（%s），匯入 %d 天", result.rebuild_reason, len(result.ingested))
    elif result.ingested:
        log.info("匯入 %s", ", ".join(d.isoformat() for d in result.ingested))
    else:
        log.info("DB 已是最新")


def _tracking_inputs(
    args: argparse.Namespace, cfg: Config, day: date
) -> tuple[list[str], dict[str, RepoMeta], bool]:
    """回傳 (需要快照的 repo, 已知 meta, DB 是否為最新)。

    匯入失敗時仍盡量沿用既有 DB；DB 也無法讀取時回傳空清單，
    讓 collect 至少能抓到當天的 Trending 與 Search。
    """
    fresh = True
    try:
        _report_sync(sync(args.db, cfg, args.data))
    except Exception:
        log.exception("匯入快照失敗，改用既有的 DB 決定追蹤池")
        fresh = False
    try:
        conn = db.connect(args.db)
        try:
            return tracking.due_ids(conn, cfg.tracking, day), db.known_meta(conn), fresh
        finally:
            conn.close()
    except sqlite3.Error:
        log.exception("無法讀取 DB，這次只收集 Trending 與 Search")
        return [], {}, False


def cmd_collect(args: argparse.Namespace, cfg: Config) -> int:
    token = _token("GH_PAT")
    day = run_date(cfg.schedule.timezone)
    due, meta, fresh = _tracking_inputs(args, cfg, day)
    log.info("收集 %s，追蹤中需要快照的 repo：%d", day.isoformat(), len(due))
    with GitHubClient(token) as client:
        result = collect(client, cfg, day=day, data_root=args.data, due_ids=due, known_meta=meta)
    for name, source in result.run["sources"].items():
        log.info("%s：%s，%d 筆", name, source["status"], source["count"])
        for error in source["errors"]:
            log.warning("%s：%s", name, error)
    return 0 if result.ok and fresh else EXIT_PARTIAL


def cmd_ingest(args: argparse.Namespace, cfg: Config) -> int:
    _report_sync(sync(args.db, cfg, args.data))
    return 0


def cmd_rebuild(args: argparse.Namespace, cfg: Config) -> int:
    _report_sync(sync(args.db, cfg, args.data, force_rebuild=True))
    return 0


def cmd_notify(args: argparse.Namespace, cfg: Config) -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log.warning("未設定 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，略過通知")
        return 0
    _report_sync(sync(args.db, cfg, args.data))
    day = args.date or run_date(cfg.schedule.timezone)
    conn = db.connect(args.db)
    try:
        with TelegramSender(token, chat_id) as sender:
            result = notify.notify(
                conn,
                cfg.notify,
                day,
                args.data,
                lambda m: sender.send(m.text, silent=m.silent, buttons=m.buttons),
                messages=MESSAGES[cfg.i18n.locale],
                translations=_translations(args.data, cfg),
            )
    except TelegramError as e:
        log.error("Telegram 發送失敗：%s", e)
        return EXIT_PARTIAL
    finally:
        conn.close()
    log.info(
        "%s：摘要%s，警示 %d 則",
        day.isoformat(),
        "已發送" if result.summary_sent else "先前已發送",
        len(result.alerts_sent),
    )
    return 0


def cmd_backfill(args: argparse.Namespace, cfg: Config) -> int:
    token = _token("GH_PAT")
    _report_sync(sync(args.db, cfg, args.data))
    day = run_date(cfg.schedule.timezone)
    conn = db.connect(args.db)
    try:
        with GitHubClient(token) as client:
            result = run_backfill(client, conn, cfg.backfill, args.data, day)
    finally:
        conn.close()
    log.info(
        "回補完成 %d 個、失敗 %d 個，使用 %d 個請求%s",
        len(result.done),
        len(result.failed),
        result.requests,
        f"；提前結束：{result.stopped}" if result.stopped else "",
    )
    _report_sync(sync(args.db, cfg, args.data))
    return EXIT_PARTIAL if result.rate_limited else 0


def cmd_site(args: argparse.Namespace, cfg: Config) -> int:
    _report_sync(sync(args.db, cfg, args.data))
    conn = db.connect(args.db)
    try:
        stats = build_site(
            conn,
            cfg,
            args.out,
            args.web,
            datetime.now(UTC),
            _translations(args.data, cfg),
            data_root=args.data,
            github_repo=detect_repo(os.environ, Path.cwd()),
            notes=load_notes(args.notes),
            reports_root=args.reports,
        )
    finally:
        conn.close()
    for source, name in ((args.icon, "icon.svg"), (args.social_preview, SOCIAL_PREVIEW)):
        if source.is_file():
            shutil.copyfile(source, args.out / name)
    log.info(
        "dashboard 已產生到 %s：資料日期 %s，%d 個 repo",
        args.out,
        stats.latest.isoformat() if stats.latest else "無",
        stats.repos,
    )
    return 0


def _interpreter(cfg: ReportsConfig) -> reports.Interpreter | None:
    """依設定的供應商建立解讀函式；沒有對應的 API key 時回傳 None。"""
    if cfg.provider == "gemini":
        return _gemini_interpreter(cfg.models)
    return _claude_interpreter(cfg.model)


def _gemini_interpreter(models: Sequence[str]) -> reports.Interpreter | None:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None

    def interpret(
        system: str, prompt: str, expected: Sequence[str]
    ) -> dict[str, reports.Interpretation]:
        return reports.interpret_with_gemini(api_key, models, system, prompt, expected)

    return interpret


def _claude_interpreter(model: str) -> reports.Interpreter | None:
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return None
    import anthropic

    client = anthropic.Anthropic()

    def interpret(
        system: str, prompt: str, expected: Sequence[str]
    ) -> dict[str, reports.Interpretation]:
        try:
            return reports.interpret_with_claude(client, model, system, prompt, expected)
        except anthropic.APIStatusError as e:
            raise reports.InterpretError(f"API 回應 {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise reports.InterpretError("無法連線到 Claude API") from e

    return interpret


def _translations(data_root: Path, cfg: Config) -> dict[str, str]:
    """translation.target 的譯文；沒有設定翻譯時為空。"""
    target = cfg.translation.target
    return translate.load(data_root, target) if target else {}


def _translator(cfg: ReportsConfig, target: str) -> translate.Translator | None:
    """依 reports 的供應商建立翻成 target 的翻譯函式；沒有對應的 API key 時回傳 None。"""
    if cfg.provider == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not api_key:
            return None
        models = cfg.models
        return lambda batch: translate.translate_with_gemini(api_key, models, batch, target=target)
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return None
    import anthropic

    client = anthropic.Anthropic()

    def run(batch: Sequence[tuple[str, str]]) -> dict[str, str]:
        try:
            return translate.translate_with_claude(client, cfg.model, batch, target=target)
        except anthropic.APIStatusError as e:
            raise translate.TranslateError(f"API 回應 {e.status_code}") from e
        except anthropic.APIConnectionError as e:
            raise translate.TranslateError("無法連線到 Claude API") from e

    return run


def cmd_translate(args: argparse.Namespace, cfg: Config) -> int:
    """翻譯失敗只記警告並回傳 0：沒有譯文時各處顯示原文，下次執行再補。"""
    target = cfg.translation.target
    if not target:
        log.info("translation.target 為空字串，略過翻譯")
        return 0
    if cfg.translation.max_per_run == 0:
        log.info("translation.max_per_run 為 0，略過翻譯")
        return 0
    translator = _translator(cfg.reports, target)
    if translator is None:
        log.warning("未設定 %s 的 API key，略過翻譯", cfg.reports.provider)
        return 0
    _report_sync(sync(args.db, cfg, args.data))
    conn = db.connect(args.db)
    try:
        result = translate.translate_pending(
            conn,
            args.data,
            translator,
            lang=target,
            batch_size=cfg.translation.batch_size,
            max_per_run=cfg.translation.max_per_run,
            interval_seconds=cfg.translation.request_interval_seconds,
        )
    finally:
        conn.close()
    log.info(
        "翻譯 %d 則、失敗 %d 則，剩下 %d 則還沒有譯文",
        result.translated,
        result.failed,
        result.remaining,
    )
    if result.error:
        log.warning("連續失敗，提前停止：%s", result.error)
    return 0


def cmd_report(args: argparse.Namespace, cfg: Config) -> int:
    _report_sync(sync(args.db, cfg, args.data))
    run_day = args.date or run_date(cfg.schedule.timezone)
    if args.kind == "due":
        periods = []
        conn = db.connect(args.db)
        try:
            for period in reports.due_periods(run_day):
                path = reports.report_path(args.reports, period)
                if path.is_file() and not (
                    reports.has_missing(path.read_text(encoding="utf-8"))
                    and reports.should_retry(period, run_day)
                ):
                    continue
                if not reports.has_data(conn, period):
                    log.info("%s 沒有快照資料，不產生 %s", period.label, path)
                    continue
                periods.append(period)
        finally:
            conn.close()
    else:
        periods = [reports.period_before(args.kind, run_day)]
    if not periods:
        log.info("%s 沒有需要產生的報告", run_day.isoformat())
        return 0

    token = os.environ.get("GH_PAT", "").strip() or None
    interpreter = _interpreter(cfg.reports)
    messages = MESSAGES[cfg.i18n.locale]
    complete = True
    conn = db.connect(args.db)
    try:
        with GitHubClient(token) as client:
            for period in periods:
                text, ok = reports.generate(
                    conn,
                    period,
                    llm_top_n=cfg.reports.llm_top_n,
                    model=reports.model_label(
                        cfg.reports.models
                        if cfg.reports.provider == "gemini"
                        else [cfg.reports.model],
                        messages,
                    ),
                    base_lookback_days=cfg.tracking.dormant_snapshot_interval_days,
                    interpreter=interpreter,
                    fetch_readme=client.get_readme if token else None,
                    messages=messages,
                )
                path = reports.report_path(args.reports, period)
                if args.kind == "due" and path.is_file():
                    old = path.read_text(encoding="utf-8")
                    if reports.interpretation_count(text) < reports.interpretation_count(old):
                        log.warning("%s 這次的解讀比既有報告少，保留既有報告", path)
                        complete = False
                        continue
                write_text_atomic(path, text)
                log.info("已產生 %s%s", path, "" if ok else "（解讀缺漏）")
                complete = complete and ok
    finally:
        conn.close()
    return 0 if complete else EXIT_PARTIAL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="starstrata", description="GitHub 趨勢資料收集")
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--data", type=Path, default=Path("data"), help="快照根目錄")
    parser.add_argument("--db", type=Path, default=Path("starstrata.sqlite"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("collect", help="抓取所有資料來源，寫入當天快照（需要 GH_PAT）")
    sub.add_parser("ingest", help="把尚未匯入的快照寫進 SQLite")
    sub.add_parser("rebuild", help="從全部快照重建 SQLite")
    sub.add_parser("backfill", help="回補歷史 star 曲線（需要 GH_PAT）")
    p_report = sub.add_parser(
        "report",
        help="產生週報或月報（解讀需要 GEMINI_API_KEY 或 ANTHROPIC_API_KEY，README 需要 GH_PAT）",
    )
    p_report.add_argument(
        "kind",
        choices=["due", reports.WEEKLY, reports.MONTHLY],
        help="due：最近一個完整的週與月，報告不存在，或期間結束 3 天內標示缺漏時產生",
    )
    p_report.add_argument("--date", type=date.fromisoformat, help="執行日期，預設為今天")
    p_report.add_argument("--reports", type=Path, default=Path("reports"), help="報告根目錄")
    p_site = sub.add_parser("site", help="產生 dashboard 靜態網站")
    p_site.add_argument("--out", type=Path, default=Path("_site"), help="輸出目錄，會先清空")
    p_site.add_argument("--web", type=Path, default=Path("web"), help="前端靜態檔目錄")
    p_site.add_argument("--icon", type=Path, default=Path("assets/icon.svg"))
    p_site.add_argument("--notes", type=Path, default=Path("notes.toml"), help="作者短評")
    p_site.add_argument("--reports", type=Path, default=Path("reports"), help="報告根目錄")
    p_site.add_argument(
        "--social-preview", type=Path, default=Path("assets") / SOCIAL_PREVIEW, help="分享預覽圖"
    )
    sub.add_parser(
        "translate",
        help="翻譯 repo 說明（需要 GEMINI_API_KEY 或 ANTHROPIC_API_KEY）",
    )
    p_notify = sub.add_parser(
        "notify", help="發送每日摘要與爆紅警示（需要 TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID）"
    )
    p_notify.add_argument("--date", type=date.fromisoformat, help="預設為今天")
    return parser


_COMMANDS = {
    "collect": cmd_collect,
    "ingest": cmd_ingest,
    "rebuild": cmd_rebuild,
    "notify": cmd_notify,
    "backfill": cmd_backfill,
    "site": cmd_site,
    "report": cmd_report,
    "translate": cmd_translate,
}


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    # httpx 在 INFO 會記錄完整請求網址，Telegram 的網址含 bot token。
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
        return _COMMANDS[args.command](args, cfg)
    except (ConfigError, UsageError) as e:
        log.error("%s", e)
        return EXIT_USAGE
