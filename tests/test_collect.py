from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from starstrata.collect import TRENDING_PAGE_INTERVAL, collect, run_date
from starstrata.config import Config, TrendingConfig, load_config
from starstrata.snapshots import RepoMeta, read_jsonl
from tests.conftest import FakeGitHub, repo_node, trending_html

DAY = date(2026, 11, 22)
REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"


@pytest.fixture
def cfg() -> Config:
    base = load_config(REPO_CONFIG)
    return replace(base, trending=TrendingConfig(languages=("rust",), periods=("daily",)))


def _clock() -> datetime:
    return datetime(2026, 11, 22, 0, 30, tzinfo=UTC)


def _run(fake: FakeGitHub, cfg: Config, root: Path, **kwargs: object) -> dict[str, object]:
    params: dict[str, object] = {"due_ids": [], "tracking_fresh": True, "known_meta": {}} | kwargs
    result = collect(
        fake.client(),
        cfg,
        day=DAY,
        data_root=root,
        sleep=fake.sleeps.append,
        clock=_clock,
        **params,  # type: ignore[arg-type]
    )
    return result.run


def _day(root: Path) -> Path:
    return root / "2026" / "11" / "22"


def _setup_basic(fake: FakeGitHub) -> None:
    fake.trending_pages["/trending?since=daily"] = trending_html(
        [("owner/alpha", "100 stars today"), ("owner/beta", "50 stars today")]
    )
    fake.trending_pages["/trending/rust?since=daily"] = trending_html(
        [("owner/alpha", "100 stars today")]
    )
    fake.search_items = [{"node_id": "R_new", "full_name": "x/new"}]
    fake.repos = [
        repo_node("R_alpha", "Owner/Alpha", stars=1000),
        repo_node("R_beta", "owner/beta", stars=500),
        repo_node("R_new", "x/new", stars=300),
        repo_node("R_old", "y/old", stars=50),
    ]


def test_run_date_uses_configured_timezone() -> None:
    now = datetime(2026, 11, 22, 17, 0, tzinfo=UTC)
    assert run_date(ZoneInfo("Asia/Taipei"), now) == date(2026, 11, 23)
    assert run_date(ZoneInfo("UTC"), now) == date(2026, 11, 22)


def test_full_run_writes_all_files(fake_github: FakeGitHub, cfg: Config, tmp_path: Path) -> None:
    _setup_basic(fake_github)
    run = _run(fake_github, cfg, tmp_path, due_ids=["R_old", "R_gone"])
    out = _day(tmp_path)

    trending = read_jsonl(out / "trending.jsonl")
    assert [(r["language"], r["rank"], r["full_name"]) for r in trending] == [
        (None, 1, "Owner/Alpha"),
        (None, 2, "owner/beta"),
        ("rust", 1, "Owner/Alpha"),
    ]
    assert read_jsonl(out / "search.jsonl") == [
        {"rank": 1, "node_id": "R_new", "full_name": "x/new"}
    ]

    repos = read_jsonl(out / "repos.jsonl")
    assert [r["node_id"] for r in repos] == ["R_alpha", "R_beta", "R_gone", "R_new", "R_old"]
    assert repos[2] == {"node_id": "R_gone", "status": "gone"}
    assert repos[0]["stars"] == 1000

    assert run["sources"] == {
        "trending": {"status": "ok", "count": 3, "errors": []},
        "search": {"status": "ok", "count": 1, "errors": []},
        "repos": {"status": "ok", "count": 4, "errors": [], "gone": 1},
    }
    assert json.loads((out / "run.json").read_text(encoding="utf-8")) == run
    assert fake_github.sleeps == [TRENDING_PAGE_INTERVAL]


def test_meta_is_written_only_when_new_or_changed(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    unchanged = RepoMeta(
        description="desc",
        topics=(),
        language="Python",
        archived=False,
        created_at="2026-01-01T00:00:00Z",
    )
    changed = replace(unchanged, description="old description")
    _run(
        fake_github,
        cfg,
        tmp_path,
        due_ids=["R_old"],
        known_meta={"R_alpha": unchanged, "R_beta": changed},
    )
    repos = {r["node_id"]: r for r in read_jsonl(_day(tmp_path) / "repos.jsonl")}
    assert "meta" not in repos["R_alpha"]
    assert repos["R_beta"]["meta"]["description"] == "desc"
    assert "meta" in repos["R_new"]
    assert "meta" in repos["R_old"]


def test_failed_trending_page_is_partial(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    fake_github.trending_status["/trending/rust?since=daily"] = 404
    run = _run(fake_github, cfg, tmp_path)
    status = run["sources"]["trending"]  # type: ignore[index]
    assert status["status"] == "partial"
    assert status["errors"][0].startswith("rust/daily:")
    assert len(read_jsonl(_day(tmp_path) / "trending.jsonl")) == 2


def test_empty_trending_page_counts_as_failure(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    del fake_github.trending_pages["/trending/rust?since=daily"]
    run = _run(fake_github, cfg, tmp_path)
    errors = run["sources"]["trending"]["errors"]  # type: ignore[index]
    assert errors == ["rust/daily: 解析結果為 0 筆"]


def test_all_trending_pages_failing_writes_no_trending_file(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    fake_github.trending_pages.clear()
    run = _run(fake_github, cfg, tmp_path)
    assert run["sources"]["trending"]["status"] == "failed"  # type: ignore[index]
    assert not (_day(tmp_path) / "trending.jsonl").exists()
    assert (_day(tmp_path) / "search.jsonl").exists()


def test_search_failure_keeps_existing_file(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    out = _day(tmp_path)
    out.mkdir(parents=True)
    (out / "search.jsonl").write_text('{"previous":true}\n', encoding="utf-8")
    fake_github.search_status = 500
    run = _run(fake_github, cfg, tmp_path)
    assert run["sources"]["search"]["status"] == "failed"  # type: ignore[index]
    assert read_jsonl(out / "search.jsonl") == [{"previous": True}]
    repos = {r["node_id"] for r in read_jsonl(out / "repos.jsonl")}
    assert "R_new" not in repos


def test_zero_search_results_is_success(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    fake_github.search_items = []
    run = _run(fake_github, cfg, tmp_path)
    assert run["sources"]["search"]["status"] == "ok"  # type: ignore[index]
    assert read_jsonl(_day(tmp_path) / "search.jsonl") == []


def test_rate_limit_during_id_lookup_keeps_name_results(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    fake_github.graphql_rate_limited_from = 2
    run = _run(fake_github, cfg, tmp_path, due_ids=["R_old"])
    status = run["sources"]["repos"]  # type: ignore[index]
    assert status["status"] == "partial"
    assert status["errors"][0].startswith("以 node ID 查詢：")
    ids = [r["node_id"] for r in read_jsonl(_day(tmp_path) / "repos.jsonl")]
    assert ids == ["R_alpha", "R_beta"]


def test_unresolved_trending_names_are_reported(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    fake_github.repos = [r for r in fake_github.repos if r["id"] != "R_beta"]
    run = _run(fake_github, cfg, tmp_path)
    assert run["unresolved_trending_repos"] == ["owner/beta"]
    names = [r["full_name"] for r in read_jsonl(_day(tmp_path) / "trending.jsonl")]
    assert "owner/beta" in names


def test_repo_found_by_name_is_not_queried_again_by_id(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    _run(fake_github, cfg, tmp_path, due_ids=["R_alpha"])
    id_queries = [
        json.loads(r.content)["variables"]["ids"]
        for r in fake_github.requests
        if r.url.path == "/graphql" and "nodes(ids:" in json.loads(r.content)["query"]
    ]
    assert id_queries == [["R_new"]]


def test_rerun_keeps_rows_of_failed_pages_and_missing_repos(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    _run(fake_github, cfg, tmp_path, due_ids=["R_old"])
    first_trending = read_jsonl(_day(tmp_path) / "trending.jsonl")

    rerun = FakeGitHub()
    _setup_basic(rerun)
    rerun.trending_status["/trending/rust?since=daily"] = 500
    rerun.trending_pages["/trending?since=daily"] = trending_html(
        [("owner/beta", "70 stars today")]
    )
    rerun.graphql_rate_limited_from = 2
    _run(rerun, cfg, tmp_path, due_ids=["R_old"])

    trending = read_jsonl(_day(tmp_path) / "trending.jsonl")
    assert [(r["language"], r["full_name"]) for r in trending] == [
        (None, "owner/beta"),
        ("rust", "Owner/Alpha"),
    ]
    assert trending[1] == first_trending[2]
    repos = {r["node_id"]: r for r in read_jsonl(_day(tmp_path) / "repos.jsonl")}
    assert set(repos) == {"R_alpha", "R_beta", "R_new", "R_old"}


def test_trending_rows_follow_config_order(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    cfg2 = replace(cfg, trending=TrendingConfig(languages=("rust",), periods=("weekly", "daily")))
    fake_github.trending_pages["/trending?since=weekly"] = trending_html(
        [("owner/beta", "1 star this week")]
    )
    fake_github.trending_pages["/trending/rust?since=weekly"] = trending_html(
        [("owner/beta", "1 star this week")]
    )
    _run(fake_github, cfg2, tmp_path)
    rows = read_jsonl(_day(tmp_path) / "trending.jsonl")
    assert [(r["language"], r["period"], r["rank"]) for r in rows] == [
        (None, "weekly", 1),
        (None, "daily", 1),
        (None, "daily", 2),
        ("rust", "weekly", 1),
        ("rust", "daily", 1),
    ]


def test_unexpected_error_in_search_keeps_other_sources(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    original = fake_github.handle

    def html_search(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/repositories":
            return httpx.Response(200, text="<html>maintenance</html>")
        return original(request)

    fake_github.handle = html_search  # type: ignore[method-assign]
    run = _run(fake_github, cfg, tmp_path)
    assert run["sources"]["search"]["status"] == "failed"  # type: ignore[index]
    assert (_day(tmp_path) / "trending.jsonl").exists()
    assert (_day(tmp_path) / "repos.jsonl").exists()


def test_unavailable_repo_is_gone_and_transient_error_is_partial(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    fake_github.forbidden_ids = {"R_old"}
    run = _run(fake_github, cfg, tmp_path, due_ids=["R_old"])
    assert run["unavailable_repos"] == ["R_old: FORBIDDEN: dmca"]
    assert run["sources"]["repos"]["status"] == "ok"  # type: ignore[index]
    repos = {r["node_id"]: r for r in read_jsonl(_day(tmp_path) / "repos.jsonl")}
    assert repos["R_old"] == {"node_id": "R_old", "status": "gone"}

    flaky = FakeGitHub()
    _setup_basic(flaky)
    flaky.subfield_error_ids = {"R_old"}
    run2 = _run(flaky, cfg, tmp_path / "second", due_ids=["R_old"])
    assert run2["node_errors"] == ["R_old: SERVICE_UNAVAILABLE: timedout"]
    assert run2["sources"]["repos"]["status"] == "partial"  # type: ignore[index]
    repos2 = {r["node_id"] for r in read_jsonl(_day(tmp_path / "second") / "repos.jsonl")}
    assert "R_old" not in repos2


def test_consecutive_trending_failures_stop_fetching(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    from starstrata.collect import MAX_CONSECUTIVE_PAGE_FAILURES

    _setup_basic(fake_github)
    many = replace(
        cfg, trending=TrendingConfig(languages=("rust", "go", "java"), periods=("daily",))
    )
    for key in ("/trending?since=daily", "/trending/rust?since=daily", "/trending/go?since=daily"):
        fake_github.trending_status[key] = 404
    run = _run(fake_github, many, tmp_path)
    pages = [r for r in fake_github.requests if r.url.path.startswith("/trending")]
    assert len(pages) == MAX_CONSECUTIVE_PAGE_FAILURES
    assert run["sources"]["trending"]["errors"][-1] == "連續多頁失敗，停止抓取 Trending"  # type: ignore[index]


def test_empty_trending_pages_count_as_consecutive_failures(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    del fake_github.trending_pages["/trending?since=daily"]
    langs = ("rust", "go", "java", "python", "c")
    many = replace(cfg, trending=TrendingConfig(languages=langs, periods=("daily",)))
    fake_github.trending_status["/trending/java?since=daily"] = 404
    run = _run(fake_github, many, tmp_path)
    pages = [r.url.path for r in fake_github.requests if r.url.path.startswith("/trending")]
    # 總榜空頁後 rust 成功，計數歸零；go 空頁、java 404、python 空頁連續 3 次後停止
    assert pages == [
        "/trending",
        "/trending/rust",
        "/trending/go",
        "/trending/java",
        "/trending/python",
    ]
    assert run["sources"]["trending"]["errors"][-1] == "連續多頁失敗，停止抓取 Trending"  # type: ignore[index]


def test_consecutive_batch_failures_stop_querying(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    from starstrata.collect import MAX_CONSECUTIVE_BATCH_FAILURES
    from starstrata.github.repos import IDS_BATCH_SIZE

    _setup_basic(fake_github)
    fake_github.trending_pages.clear()
    fake_github.search_items = []
    ids = [f"R_{i:04d}" for i in range(IDS_BATCH_SIZE * 6)]
    fake_github.repos = [repo_node(i, f"o/{i}") for i in ids]
    fake_github.graphql_status_on_call = {n: 400 for n in range(1, 10)}
    run = _run(fake_github, cfg, tmp_path, due_ids=ids)
    assert fake_github.graphql_calls == MAX_CONSECUTIVE_BATCH_FAILURES
    assert run["sources"]["repos"]["errors"][-1] == "連續多批失敗，停止查詢"  # type: ignore[index]


def test_rerun_after_ingest_keeps_meta(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    _run(fake_github, cfg, tmp_path)
    first = {r["node_id"]: r for r in read_jsonl(_day(tmp_path) / "repos.jsonl")}
    assert "meta" in first["R_alpha"]
    # 重跑前的 ingest 已把當天的 meta 寫進 DB，known_meta 與這次查到的相同。
    known = {
        "R_alpha": RepoMeta(
            description="desc",
            topics=(),
            language="Python",
            archived=False,
            created_at="2026-01-01T00:00:00Z",
        )
    }
    rerun = FakeGitHub()
    _setup_basic(rerun)
    _run(rerun, cfg, tmp_path, known_meta=known)
    second = {r["node_id"]: r for r in read_jsonl(_day(tmp_path) / "repos.jsonl")}
    assert second["R_alpha"]["meta"] == first["R_alpha"]["meta"]


def test_trending_and_search_are_written_before_repo_queries(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    original = fake_github.handle

    def killed_during_graphql(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            raise KeyboardInterrupt
        return original(request)

    fake_github.handle = killed_during_graphql  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        _run(fake_github, cfg, tmp_path)
    assert len(read_jsonl(_day(tmp_path) / "trending.jsonl")) == 3
    assert len(read_jsonl(_day(tmp_path) / "search.jsonl")) == 1


@pytest.mark.parametrize("fresh", [True, False])
def test_run_json_records_tracking_freshness(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path, fresh: bool
) -> None:
    _setup_basic(fake_github)
    run = _run(fake_github, cfg, tmp_path, tracking_fresh=fresh)
    assert run["tracking_fresh"] is fresh


def _merged(root: Path) -> dict[str, object]:
    run = json.loads((_day(root) / "run.json").read_text(encoding="utf-8"))
    merged: dict[str, object] = run["merged"]
    return merged


def _setup_failures(fake: FakeGitHub) -> None:
    _setup_basic(fake)
    fake.trending_status["/trending/rust?since=daily"] = 500
    fake.search_status = 500
    fake.graphql_rate_limited_from = 2


def test_merged_status_lists_what_is_still_missing(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_failures(fake_github)
    _run(fake_github, cfg, tmp_path, due_ids=["R_old"])
    assert _merged(tmp_path) == {
        "trending_missing": [{"period": "daily", "language": "rust"}],
        "search_ok": False,
        "repos_missing": ["R_old"],
    }

    rerun = FakeGitHub()
    _setup_basic(rerun)
    _run(rerun, cfg, tmp_path, due_ids=["R_old"])
    assert _merged(tmp_path) == {"trending_missing": [], "search_ok": True, "repos_missing": []}


def test_failed_rerun_keeps_earlier_success_in_merged_status(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    _run(fake_github, cfg, tmp_path, due_ids=["R_old"])

    rerun = FakeGitHub()
    _setup_failures(rerun)
    run = _run(rerun, cfg, tmp_path, due_ids=["R_old"])
    assert run["sources"]["search"]["status"] == "failed"  # type: ignore[index]
    assert _merged(tmp_path) == {"trending_missing": [], "search_ok": True, "repos_missing": []}


def test_missing_repos_from_earlier_run_stay_missing(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_failures(fake_github)
    _run(fake_github, cfg, tmp_path, due_ids=["R_old"])

    # 重跑時 R_old 不在 due_ids（例如已轉為 dormant），仍要記得它今天沒有資料。
    rerun = FakeGitHub()
    _setup_failures(rerun)
    _run(rerun, cfg, tmp_path)
    assert _merged(tmp_path)["repos_missing"] == ["R_old"]


@pytest.mark.parametrize(("status", "expected"), [("ok", True), ("failed", False)])
def test_search_ok_from_run_json_without_merged(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path, status: str, expected: bool
) -> None:
    out = _day(tmp_path)
    out.mkdir(parents=True)
    legacy = {"date": DAY.isoformat(), "sources": {"search": {"status": status}}}
    (out / "run.json").write_text(json.dumps(legacy), encoding="utf-8")
    _setup_failures(fake_github)
    _run(fake_github, cfg, tmp_path)
    assert _merged(tmp_path)["search_ok"] is expected


def test_rate_limit_during_name_lookup_stops_repos(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path
) -> None:
    _setup_basic(fake_github)
    fake_github.graphql_rate_limited_from = 1
    run = _run(fake_github, cfg, tmp_path, due_ids=["R_old"])
    assert fake_github.graphql_calls == 1
    assert run["sources"]["repos"]["errors"] == ["以名稱查詢：GraphQL 額度用盡"]  # type: ignore[index]


def _many_trending_names(fake: FakeGitHub, count: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """總榜有 count 個 repo，以名稱查詢時每批 1 個。"""
    from starstrata.github import repos

    monkeypatch.setattr(repos, "NAMES_BATCH_SIZE", 1)
    names = [f"o/n{i}" for i in range(count)]
    fake.trending_pages["/trending?since=daily"] = trending_html(
        [(n, "1 star today") for n in names]
    )
    fake.trending_pages["/trending/rust?since=daily"] = trending_html([(names[0], "1 star today")])
    fake.repos = [repo_node(f"R_n{i}", n) for i, n in enumerate(names)]
    fake.repos.append(repo_node("R_old", "y/old"))
    fake.search_items = []


def test_consecutive_name_batch_failures_skip_id_lookup(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from starstrata.collect import MAX_CONSECUTIVE_BATCH_FAILURES

    _many_trending_names(fake_github, MAX_CONSECUTIVE_BATCH_FAILURES + 1, monkeypatch)
    fake_github.graphql_status_on_call = {n: 400 for n in range(1, 10)}
    run = _run(fake_github, cfg, tmp_path, due_ids=["R_old"])
    assert fake_github.graphql_calls == MAX_CONSECUTIVE_BATCH_FAILURES
    assert run["sources"]["repos"]["errors"][-1] == "連續多批失敗，停止查詢"  # type: ignore[index]


def test_successful_name_batch_resets_failure_count(
    fake_github: FakeGitHub, cfg: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _many_trending_names(fake_github, 5, monkeypatch)
    # 失敗、失敗、成功、失敗、失敗：連續失敗數不到上限，之後照常以 node ID 查詢。
    fake_github.graphql_status_on_call = {1: 400, 2: 400, 4: 400, 5: 400}
    run = _run(fake_github, cfg, tmp_path, due_ids=["R_old"])
    assert fake_github.graphql_calls == 6
    ids = [r["node_id"] for r in read_jsonl(_day(tmp_path) / "repos.jsonl")]
    assert ids == ["R_n2", "R_old"]
    assert "連續多批失敗，停止查詢" not in run["sources"]["repos"]["errors"]  # type: ignore[index]
