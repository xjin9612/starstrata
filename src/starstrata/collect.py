"""每日收集：抓取 Trending、Search 與 repo 快照，寫入當天的快照目錄。"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from starstrata.config import Config
from starstrata.github.http import GitHubClient, GitHubError, RateLimitExceeded
from starstrata.github.repos import fetch_by_ids, fetch_by_names
from starstrata.github.search import search_new_repos
from starstrata.github.trending import TrendingParseError, fetch_trending
from starstrata.snapshots import (
    REPOS_FILE,
    RUN_FILE,
    SEARCH_FILE,
    TRENDING_FILE,
    RepoMeta,
    RepoSnapshot,
    SearchHit,
    TrendingEntry,
    day_dir,
    gone_row,
    read_jsonl,
    repo_row,
    search_row,
    trending_row,
    write_json,
    write_jsonl,
)

log = logging.getLogger(__name__)

# 連續抓 Trending 頁面之間的間隔秒數。
TRENDING_PAGE_INTERVAL = 1.0

# GraphQL 連續這麼多批失敗時停止查詢，避免服務故障時逐批重試拖過 workflow 的逾時。
MAX_CONSECUTIVE_BATCH_FAILURES = 3
# Trending 連續這麼多頁失敗時停止抓取，理由同上。
MAX_CONSECUTIVE_PAGE_FAILURES = 3

OK = "ok"
PARTIAL = "partial"
FAILED = "failed"


@dataclass
class SourceStatus:
    status: str = OK
    count: int = 0
    errors: list[str] = field(default_factory=list)

    def settle(self) -> None:
        if self.errors:
            self.status = PARTIAL if self.count else FAILED

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "count": self.count, "errors": self.errors}


@dataclass(frozen=True)
class CollectResult:
    run: dict[str, Any]

    @property
    def ok(self) -> bool:
        return all(s["status"] == OK for s in self.run["sources"].values())


def run_date(tz: ZoneInfo, now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(tz).date()


def _collect_trending(
    client: GitHubClient, cfg: Config, sleep: Callable[[float], None]
) -> tuple[list[TrendingEntry], set[tuple[str | None, str]], SourceStatus]:
    """回傳上榜紀錄、成功抓取的 (語言, 期間) 頁面，以及狀態。"""
    status = SourceStatus()
    entries: list[TrendingEntry] = []
    fetched: set[tuple[str | None, str]] = set()
    pages = [(lang, p) for lang in (None, *cfg.trending.languages) for p in cfg.trending.periods]
    failures = 0
    for i, (language, period) in enumerate(pages):
        if i:
            sleep(TRENDING_PAGE_INTERVAL)
        label = f"{language or '總榜'}/{period}"
        try:
            page = fetch_trending(client, language=language, period=period)
        except Exception as e:  # 任何例外都只讓這一頁失敗，其他頁面與資料來源照常寫入。
            status.errors.append(f"{label}: {_describe(e)}")
            page = []
        else:
            if not page:
                status.errors.append(f"{label}: 解析結果為 0 筆")
        if not page:
            failures += 1
            if failures >= MAX_CONSECUTIVE_PAGE_FAILURES:
                status.errors.append("連續多頁失敗，停止抓取 Trending")
                break
            continue
        failures = 0
        entries.extend(page)
        fetched.add((language, period))
    status.count = len(entries)
    status.settle()
    return entries, fetched, status


def _collect_search(
    client: GitHubClient, cfg: Config, day: date
) -> tuple[list[SearchHit] | None, SourceStatus]:
    status = SourceStatus()
    try:
        hits = search_new_repos(client, cfg.search, day)
    except Exception as e:  # 任何例外都只讓 Search 失敗。
        status.errors.append(_describe(e))
        status.settle()
        return None, status
    status.count = len(hits)
    return hits, status


@dataclass
class _RepoResults:
    snapshots: dict[str, RepoSnapshot] = field(default_factory=dict)
    gone: set[str] = field(default_factory=set)
    # Trending 上的名稱 → GraphQL 回傳的正式名稱。
    canonical: dict[str, str] = field(default_factory=dict)
    missing_names: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)
    node_errors: list[str] = field(default_factory=list)


def _collect_repos(
    client: GitHubClient, trending_names: Sequence[str], node_ids: Sequence[str]
) -> tuple[_RepoResults, SourceStatus]:
    status = SourceStatus()
    results = _RepoResults()
    failures = 0
    try:
        for name_batch in fetch_by_names(client, trending_names):
            if name_batch.error:
                status.errors.append(f"以名稱查詢：{name_batch.error}")
                failures += 1
                if failures >= MAX_CONSECUTIVE_BATCH_FAILURES:
                    status.errors.append("連續多批失敗，停止查詢")
                    break
                continue
            failures = 0
            for name, snap in name_batch.snapshots.items():
                results.snapshots[snap.node_id] = snap
                results.canonical[name] = snap.full_name
            results.missing_names.extend(name_batch.missing)
            results.node_errors.extend(name_batch.node_errors)
    except RateLimitExceeded as e:
        status.errors.append(f"以名稱查詢：{_describe(e)}")
        return _settle_repos(results, status)
    except Exception as e:  # 非預期錯誤時保留已取得的批次，繼續以 node ID 查詢。
        status.errors.append(f"以名稱查詢：{_describe(e)}")

    remaining = [i for i in node_ids if i not in results.snapshots]
    try:
        if failures >= MAX_CONSECUTIVE_BATCH_FAILURES:
            remaining = []
        for id_batch in fetch_by_ids(client, remaining):
            if id_batch.error:
                status.errors.append(f"以 node ID 查詢：{id_batch.error}")
                failures += 1
                if failures >= MAX_CONSECUTIVE_BATCH_FAILURES:
                    status.errors.append("連續多批失敗，停止查詢")
                    break
                continue
            failures = 0
            for snap in id_batch.snapshots:
                results.snapshots[snap.node_id] = snap
            results.gone.update(id_batch.gone)
            results.unavailable.extend(id_batch.unavailable)
            results.node_errors.extend(id_batch.node_errors)
    except Exception as e:  # 額度用盡或非預期錯誤時，保留已取得的批次。
        status.errors.append(f"以 node ID 查詢：{_describe(e)}")
    return _settle_repos(results, status)


def _settle_repos(results: _RepoResults, status: SourceStatus) -> tuple[_RepoResults, SourceStatus]:
    if results.node_errors:
        status.errors.append(f"{len(results.node_errors)} 個 repo 查詢錯誤，見 node_errors")
    status.count = len(results.snapshots)
    status.settle()
    return results, status


def _describe(e: Exception) -> str:
    if isinstance(e, GitHubError | TrendingParseError):
        return str(e)
    return f"{type(e).__name__}: {e}"


def _existing(path: Path) -> list[dict[str, Any]]:
    return read_jsonl(path) if path.is_file() else []


def _previous_run(path: Path) -> dict[str, Any]:
    """同一天先前的 run.json；不存在、不是合法 JSON 或不是物件時回傳空物件。"""
    if not path.is_file():
        return {}
    try:
        run = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log.warning("%s 不是合法的 JSON，視為沒有先前的執行紀錄：%s", path, e)
        return {}
    if not isinstance(run, dict):
        log.warning("%s 不是 JSON 物件，視為沒有先前的執行紀錄", path)
        return {}
    return run


def _search_ok(previous: Mapping[str, Any]) -> bool:
    """同一天先前的執行是否有一次 Search 成功；沒有 merged 的 run.json 改看 sources。"""
    if "merged" in previous:
        return bool(previous["merged"]["search_ok"])
    return bool(previous.get("sources", {}).get("search", {}).get("status") == OK)


def _trending_missing(
    cfg: Config, kept: Sequence[Mapping[str, Any]], fetched: Sequence[TrendingEntry]
) -> list[dict[str, str | None]]:
    """設定的 (期間, 語言) 中，合併後仍沒有任何資料的組合，依設定順序排列。"""
    present = {(r["language"], r["period"]) for r in kept}
    present |= {(e.language, e.period) for e in fetched}
    return [
        {"period": period, "language": language}
        for language in (None, *cfg.trending.languages)
        for period in cfg.trending.periods
        if (language, period) not in present
    ]


def _trending_order(cfg: Config) -> Callable[[Mapping[str, Any]], tuple[int, int, int]]:
    """總榜在前，語言依設定順序；同一語言內依期間設定順序與排名。"""
    languages = {lang: i for i, lang in enumerate((None, *cfg.trending.languages))}
    periods = {p: i for i, p in enumerate(cfg.trending.periods)}

    def key(row: Mapping[str, Any]) -> tuple[int, int, int]:
        return (
            languages.get(row["language"], len(languages)),
            periods.get(row["period"], len(periods)),
            int(row["rank"]),
        )

    return key


def collect(
    client: GitHubClient,
    cfg: Config,
    *,
    day: date,
    data_root: Path,
    due_ids: Sequence[str],
    tracking_fresh: bool,
    known_meta: Mapping[str, RepoMeta],
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CollectResult:
    """抓取所有資料來源並寫入 day 的快照目錄。

    due_ids 是今天需要快照的追蹤中 repo；tracking_fresh 為 False 表示它不是由最新的 DB 算出
    （匯入失敗或 DB 無法讀取），記在 run.json，備援排程依此重新收集。
    known_meta 是各 repo 最近一次記錄的 meta，
    meta 與它不同或不在其中時才寫入 meta。
    同一天重跑時，這次失敗的 Trending 頁面與沒查到的 repo 保留該日期既有檔案中的資料；
    Search 失敗時保留既有的 search.jsonl。既有的列帶有 meta 時，新列也保留 meta，
    因為重跑前的 ingest 已把當天的 meta 寫進 known_meta。
    run.json 的 sources 只記這次執行；merged 記當天所有執行合併後還缺的資料。
    """
    started_at = clock()
    out = day_dir(data_root, day)
    trending, fetched_pages, trending_status = _collect_trending(client, cfg, sleep)
    kept_trending = [
        r
        for r in _existing(out / TRENDING_FILE)
        if (r["language"], r["period"]) not in fetched_pages
    ]

    def write_trending(canonical: Mapping[str, str]) -> None:
        rows = kept_trending + [
            trending_row(e) | {"full_name": canonical.get(e.full_name, e.full_name)}
            for e in trending
        ]
        if rows:
            write_jsonl(out / TRENDING_FILE, sorted(rows, key=_trending_order(cfg)))

    # 先寫一次：之後查 repo 若被中斷，當天的 Trending 與 Search 已經落地。
    write_trending({})
    hits, search_status = _collect_search(client, cfg, day)
    if hits is not None:
        write_jsonl(out / SEARCH_FILE, (search_row(h) for h in hits))

    trending_names = list(dict.fromkeys(e.full_name for e in trending))
    node_ids = list(dict.fromkeys([*due_ids, *(h.node_id for h in hits or [])]))
    repos, repos_status = _collect_repos(client, trending_names, node_ids)
    write_trending(repos.canonical)

    repo_rows = {r["node_id"]: r for r in _existing(out / REPOS_FILE)}
    for s in repos.snapshots.values():
        had_meta = "meta" in repo_rows.get(s.node_id, {})
        include = had_meta or known_meta.get(s.node_id) != s.meta
        repo_rows[s.node_id] = repo_row(s, include_meta=include)
    for node_id in repos.gone:
        repo_rows[node_id] = gone_row(node_id)
    if repo_rows:
        write_jsonl(out / REPOS_FILE, (repo_rows[k] for k in sorted(repo_rows)))

    previous = _previous_run(out / RUN_FILE)
    run: dict[str, Any] = {
        "date": day.isoformat(),
        "started_at": started_at.isoformat(),
        "finished_at": clock().isoformat(),
        "sources": {
            "trending": trending_status.as_dict(),
            "search": search_status.as_dict(),
            "repos": repos_status.as_dict() | {"gone": len(repos.gone)},
        },
        "unresolved_trending_repos": repos.missing_names,
        "unavailable_repos": repos.unavailable,
        "node_errors": repos.node_errors,
        "rate_remaining": dict(client.rate_remaining),
        "tracking_fresh": tracking_fresh,
        "merged": {
            "trending_missing": _trending_missing(cfg, kept_trending, trending),
            "search_ok": search_status.status == OK or _search_ok(previous),
            "repos_missing": sorted(
                (set(previous.get("merged", {}).get("repos_missing", [])) | set(node_ids))
                - repo_rows.keys()
            ),
        },
    }
    write_json(out / RUN_FILE, run)
    return CollectResult(run=run)
