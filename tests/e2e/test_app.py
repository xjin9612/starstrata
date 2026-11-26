"""dashboard（web/）的瀏覽器測試。資料見 conftest.py 的 write_fixture_data()。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any

import pytest
from playwright.sync_api import Locator, Page, Route, expect

from tests.e2e.conftest import (
    COLLECTED_AT,
    GITHUB_REPO,
    SEARCH_ONLY_NEW,
    SITE_URL,
    Patch,
    store_seen,
)

pytestmark = pytest.mark.e2e
COLLECTED = datetime.fromisoformat(COLLECTED_AT)

# 介面文字：移除 repo 說明、名稱、作者短評、報告內容等資料後的文字與屬性。
_UI_TEXT_JS = """() => {
  const c = document.body.cloneNode(true);
  c.querySelectorAll('.desc,.pop,.d-desc,.note-box,.dd-snip,.topics,.name,.dd-name,td.repo,'
    + '.report,.rp-tops,.gh-link span:not(.sr)').forEach(e => e.remove());
  const attrs = [...c.querySelectorAll('[aria-label],[title],[placeholder]')].map(e =>
    ["aria-label", "title", "placeholder"].map(a => e.getAttribute(a)).join(" "));
  return c.innerText + ' ' + attrs.join(' ') + ' ' + document.title;
}"""
_CJK = re.compile(r"[㐀-鿿＀-￯　-〿぀-ヿ]")

ROUTES = [
    "#/trending",
    "#/tracked",
    "#/new",
    "#/daily",
    "#/alerts",
    "#/reports",
    "#/reports/weekly/2026-W47",
    "#/repo/hot/rocket",
    "#/repo/o/steady",
]


def _kpi(page: Page, label: str) -> str:
    return page.locator(".kpi", has=page.locator("dt", has_text=label)).locator("dd").inner_text()


def _cards(page: Page) -> list[str]:
    expect(page.locator(".card").first).to_be_visible()
    return page.locator(".card .name a").all_inner_texts()


def _card(page: Page, name: str) -> Locator:
    return page.locator(".card", has=page.locator(".name", has_text=name))


def _loaded(page: Page) -> None:
    expect(page.locator("#view h2").first).to_be_attached()
    page.wait_for_load_state("networkidle")


def _with_run(**run: Any) -> Any:
    base = {"trending_missing": [], "search_ok": True, "repos_missing": 0}
    return lambda meta: meta | {"run": base | run}


def test_trending_rank_changes(page: Page, base_url: str) -> None:
    page.goto(base_url)
    expect(page.locator(".view-head p")).to_have_text(
        "全部語言・今日・共 4 筆・依 GitHub Trending 的名次排序"
    )
    assert _cards(page) == ["o/steady", "hot/rocket", "late/single", "o/typed"]
    expect(_card(page, "o/steady").locator(".rk-chg")).to_have_count(0)
    entered = _card(page, "hot/rocket").locator(".rk-chg.in")
    expect(entered).to_contain_text("進榜")
    expect(entered).to_have_attribute("title", "11-23 未上榜")
    single = _card(page, "late/single")
    expect(single.locator(".rk-chg")).to_have_count(0)
    expect(single.locator(".flag.new")).to_have_count(1)
    down = _card(page, "o/typed").locator(".rk-chg.down")
    expect(down).to_have_text("↓2")
    expect(down).to_have_attribute("title", "較 11-23 下降 2 名")


def test_trending_filters_and_sort_in_url(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.locator('#filters [data-lang="python"]').click()
    expect(page).to_have_url(f"{base_url}#/trending?lang=python")
    expect(page.locator('#filters [data-lang="python"]')).to_have_attribute("aria-pressed", "true")
    assert _cards(page) == ["o/steady"]

    page.goto(f"{base_url}#/trending?sort=stars")
    expect(page.locator(".view-head p")).to_have_text("全部語言・今日・共 4 筆・依新增 star 排序")
    assert _cards(page) == ["hot/rocket", "o/steady", "o/typed", "late/single"]
    expect(page.locator(".rk-chg")).to_have_count(0)

    page.locator('#filters [data-period="weekly"]').click()
    expect(page).to_have_url(f"{base_url}#/trending?period=weekly&sort=stars")
    expect(page.locator(".view-head p")).to_have_text("全部語言・本週・共 1 筆・依新增 star 排序")


def test_trending_empty_filter(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/trending?lang=rust")
    expect(page.get_by_text("Rust 目前沒有上榜的 repo")).to_be_visible()
    page.get_by_role("button", name="顯示全部語言").click()
    expect(page.locator(".view-head p")).to_have_text(
        "全部語言・今日・共 4 筆・依 GitHub Trending 的名次排序"
    )


def test_trending_missing_board(page: Page, base_url: str, patch_json: Patch) -> None:
    patch_json("meta", _with_run(trending_missing=[{"period": "daily", "language": "rust"}]))
    page.goto(f"{base_url}#/trending?lang=rust")
    expect(page.get_by_text("今日・Rust 沒有收集到資料")).to_be_visible()
    expect(page.locator("#notice")).to_contain_text("有部分 Trending 榜沒有收集到資料：今日・Rust")


def test_data_exactly_36_hours_old_has_no_notice(page: Page, base_url: str) -> None:
    page.clock.set_fixed_time(COLLECTED + timedelta(hours=36))
    page.goto(base_url)
    expect(page.locator("#hdrUpd")).to_have_text("・07:38 更新")
    _loaded(page)
    expect(page.locator("#notice")).to_be_hidden()


def test_stale_and_partial_notice(page: Page, base_url: str, patch_json: Patch) -> None:
    patch_json("meta", _with_run(search_ok=False, repos_missing=3))
    page.clock.set_fixed_time(COLLECTED + timedelta(hours=36, minutes=1))
    page.goto(base_url)
    notice = page.locator("#notice")
    expect(notice).to_contain_text("資料超過一天未更新，最後更新 11-24 07:38。")
    expect(notice).to_contain_text("2026-11-24 的 GitHub Search 沒有成功，新上榜可能不完整。")
    expect(notice).to_contain_text("有 3 個 repo 在 2026-11-24 沒有收集到數字。")


def test_new_shows_search_only_and_more(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/new")
    search_only = _card(page, "s/new-00")
    expect(search_only.locator(".per.src")).to_have_text("來自 Search，沒有今日 Trending 數字")
    expect(search_only.locator(".delta")).to_have_count(0)

    total = SEARCH_ONLY_NEW + 1
    expect(page.locator(".card")).to_have_count(24)
    page.locator("#moreNew").click()
    expect(page.locator(".card")).to_have_count(total)
    expect(page.locator(".all-shown")).to_have_text(f"已顯示全部 {total} 筆")
    trending_only = _card(page, "late/single")
    expect(trending_only.locator(".delta")).to_have_text("—")
    expect(trending_only.locator(".per.src")).to_have_count(0)


def test_daily_ranks_by_gain(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/daily")
    assert _cards(page)[:2] == ["hot/rocket", "late/burst"]
    expect(page.locator(".card").first.locator(".delta")).to_have_text("+900")


def test_alerts_rule_and_rows(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/alerts")
    expect(page.locator(".rule")).to_have_text(
        "規則：日增 ≥ 近 7 日平均 × 3，且 ≥ 200 star；同一 repo 7 天內只警示一次；"
        "近 7 日資料（含歷史回補）不足時只看 200。"
    )
    rows = page.locator("table tbody tr")
    expect(rows).to_have_count(2)
    hot = rows.filter(has_text="hot/rocket")
    expect(hot.locator('td[data-label="近 7 日平均"]')).to_have_text("10")
    expect(hot.locator(".mult")).to_have_text("×90.0")
    burst = rows.filter(has_text="late/burst")
    expect(burst.locator('td[data-label="近 7 日平均"]')).to_have_text("資料不足")
    expect(burst.locator('td[data-label="倍數"]')).to_have_text("—")


def test_reports_list(page: Page, base_url: str) -> None:
    page.goto(base_url)
    expect(page.locator('#tabs a[href="#/reports"] .badge')).to_contain_text("新")
    page.locator('#tabs a[href="#/reports"]').click()
    expect(page.locator(".rp-year h3")).to_have_text(["2026", "2025"])
    items = page.locator(".rp-item")
    expect(items).to_have_count(3)
    expect(items.nth(0)).to_contain_text("2026-W47")
    expect(items.nth(0).locator(".flag.new")).to_have_count(1)
    expect(items.nth(1).locator(".flag.new")).to_have_count(0)
    expect(items.nth(0).locator(".rp-tops")).to_contain_text("hot/rocket")

    page.locator('[data-rkind="monthly"]').click()
    expect(page).to_have_url(f"{base_url}#/reports?kind=monthly")
    expect(page.locator(".rp-item")).to_have_count(1)


def test_report_reader(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/reports/weekly/2026-W47")
    report = page.locator(".report")
    expect(report.locator("h3").first).to_have_text("摘要")
    expect(report.locator('a[href="#/repo/hot/rocket"]')).to_have_count(1)
    expect(report.locator("code")).to_have_text("m")
    expect(page.locator(".rp-meta a")).to_have_attribute(
        "href", f"https://github.com/{GITHUB_REPO}/blob/HEAD/reports/weekly/2026-W47.md"
    )
    nav = page.locator(".rp-nav a")
    expect(nav).to_have_count(1)
    expect(nav).to_have_text("上一期 2026-W46")

    report.locator('a[href="#/repo/hot/rocket"]').click()
    expect(page.locator("h2")).to_contain_text("hot/rocket")
    page.locator("[data-back]").click()
    expect(page).to_have_url(f"{base_url}#/reports/weekly/2026-W47")


def test_report_not_found(page: Page, base_url: str, failed_requests: list[str]) -> None:
    page.goto(f"{base_url}#/reports/weekly/2099-W01")
    expect(page.get_by_text("找不到這份報告")).to_be_visible()
    assert failed_requests == [f"{base_url}data/reports/weekly/2099-W01.json"]
    failed_requests.clear()


def test_repo_with_backfill(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/repo/hot/rocket")
    expect(page.locator("h2")).to_contain_text("hot/rocket")
    assert _kpi(page, "總 star") == "1,970"
    assert _kpi(page, "近 7 日新增") == "+960"
    assert _kpi(page, "近 30 日新增") == "+975含回補"
    assert _kpi(page, "Trending 上榜天數") == "1天"
    expect(page.locator("#chartCum svg")).to_have_count(1)
    expect(page.locator("#chartDaily svg")).to_have_count(1)


def test_repo_with_estimated_days(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/repo/o/gappy")
    expect(page.locator("h2")).to_contain_text("o/gappy")
    assert _kpi(page, "近 7 日新增") == "+28含推算"
    expect(page.locator(".legend").first).to_contain_text("推算")


def test_repo_with_note_and_translation(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/repo/o/steady")
    note = page.locator(".note-box")
    expect(note).to_contain_text("穩定的參考實作。")
    expect(note).to_contain_text("更新於 2026-11-20")
    expect(note.locator("a")).to_have_attribute("href", "https://example.com/steady")
    expect(page.locator(".d-desc")).to_contain_text("穩定成長")
    expect(page.locator(".d-desc .orig")).to_contain_text("steady")
    assert _kpi(page, "Trending 上榜天數") == "9天"


def test_repo_with_single_snapshot(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/repo/late/single")
    expect(page.locator("h2")).to_contain_text("late/single")
    assert _kpi(page, "近 7 日新增") == "尚無資料"
    assert _kpi(page, "近 30 日新增") == "尚無資料"
    expect(page.get_by_text("歷史資料收集中")).to_be_visible()
    expect(page.locator(".note-box")).to_have_count(0)


def test_repo_not_found(page: Page, base_url: str, failed_requests: list[str]) -> None:
    page.goto(f"{base_url}#/repo/nobody/nothing")
    expect(page.get_by_text("找不到這個 repo")).to_be_visible()
    assert failed_requests == [f"{base_url}data/repos/nobody/nothing.json"]
    failed_requests.clear()


def test_cards_show_translation_note_and_spark(page: Page, base_url: str) -> None:
    page.goto(base_url)
    steady = _card(page, "o/steady")
    expect(steady.locator(".desc")).to_have_text("穩定成長")
    expect(steady.locator(".desc")).to_have_attribute("lang", "zh-TW")
    expect(steady.locator("button.ai")).to_have_count(1)
    expect(steady.locator(".note-ic")).to_have_count(1)
    expect(steady.locator("svg.spark")).to_have_count(1)
    expect(steady.get_by_text("近 7 日共新增 35 star")).to_have_count(1)
    rocket = _card(page, "hot/rocket")
    expect(rocket.locator(".desc")).to_have_text("desc")
    expect(rocket.locator("button.ai")).to_have_count(0)
    expect(rocket.locator(".note-ic")).to_have_count(0)


def test_unseen_marks_only_after_first_visit(page: Page, base_url: str) -> None:
    page.goto(base_url)
    expect(page.locator(".card")).to_have_count(4)
    expect(page.locator(".seen-dot")).to_have_count(0)

    store_seen(page, ["o/steady"])
    page.reload()
    expect(page.locator(".card")).to_have_count(4)
    expect(_card(page, "o/steady").locator(".seen-dot")).to_have_count(0)
    expect(_card(page, "o/typed").locator(".seen-dot")).to_have_count(1)


@pytest.mark.parametrize(
    ("term", "name", "snippet"),
    [
        ("rocket", "hot/rocket", None),
        ("rocketry", "hot/rocket", "#rocketry"),
        ("穩定", "o/steady", "穩定成長"),
    ],
)
def test_search(page: Page, base_url: str, term: str, name: str, snippet: str | None) -> None:
    page.goto(base_url)
    _loaded(page)
    page.locator("#q").fill(term)
    first = page.locator("#qList .dd-item").first
    expect(first.locator(".dd-name")).to_have_text(name)
    if snippet:
        expect(first).to_contain_text(snippet)
    page.locator("#q").press("Enter")
    expect(page).to_have_url(f"{base_url}#/repo/{name}")


def test_search_without_results(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.locator("#q").fill("zzz-none")
    expect(page.locator("#qPanel")).to_contain_text("找不到符合「zzz-none」的 repo")


def test_tracked_list_from_kpi(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/trending")
    page.locator(".kpi-link").click()
    expect(page).to_have_url(f"{base_url}#/tracked")
    expect(page.locator("#view h2")).to_have_text("追蹤中的 repo")
    expect(page.locator(".view-head.compact p")).to_have_text("全部語言・共 36 個・依今日新增排序")
    assert _cards(page)[:4] == ["hot/rocket", "late/burst", "o/steady", "o/typed"]
    expect(page.locator(".card")).to_have_count(24)
    page.locator("#moreTracked").click()
    expect(page.locator(".card")).to_have_count(36)
    expect(page.locator(".all-shown")).to_have_text("已顯示全部 36 筆")


def test_tracked_sort_and_filter(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/tracked")
    page.locator('#filters [data-sort="total"]').click()
    expect(page).to_have_url(f"{base_url}#/tracked?sort=total")
    expect(page.locator(".view-head.compact p")).to_have_text("全部語言・共 36 個・依總 star 排序")
    assert _cards(page)[:2] == ["hot/rocket", "s/new-00"]
    page.locator('#filters [data-sort="recent"]').click()
    expect(page).to_have_url(f"{base_url}#/tracked?sort=recent")
    expect(page.locator(".card").first.locator(".created")).to_have_text("首次出現 2026-11-24")
    page.locator('#filters [data-lang="typescript"]').click()
    expect(page).to_have_url(f"{base_url}#/tracked?lang=typescript&sort=recent")
    assert _cards(page) == ["o/typed"]
    page.locator('#filters [data-lang="rust"]').click()
    expect(page.get_by_text("沒有 Rust 的追蹤中 repo")).to_be_visible()
    page.locator('#view .btn[data-lang="all"]').click()
    expect(page).to_have_url(f"{base_url}#/tracked?sort=recent")


def test_tracked_back_restores_sort_and_shown(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/tracked?sort=total")
    page.locator("#moreTracked").click()
    expect(page.locator(".card")).to_have_count(36)
    page.locator(".card .name a", has_text="late/single").click()
    expect(page.locator("h2")).to_contain_text("late/single")
    page.locator("[data-back]").click()
    expect(page).to_have_url(f"{base_url}#/tracked?sort=total")
    expect(page.locator(".card")).to_have_count(36)


def test_tracked_url_is_kept_when_leaving_during_load(page: Page, base_url: str) -> None:
    held: list[Route] = []

    def hold(route: Route) -> None:
        held.append(route)

    page.route("**/data/tracked.json", hold)
    with page.expect_request("**/data/tracked.json"):
        page.goto(f"{base_url}#/tracked?sort=bogus")
    page.goto(f"{base_url}#/new")
    expect(page.locator("#view h2")).to_have_text("今日新上榜")
    with page.expect_response("**/data/tracked.json"):
        held[0].fallback()
    page.wait_for_load_state("networkidle")
    expect(page).to_have_url(f"{base_url}#/new")


def test_tracked_shows_zero_and_negative_gains(
    page: Page, base_url: str, patch_json: Patch
) -> None:
    def change(body: Any) -> Any:
        body["items"][0]["period_stars"] = -2
        body["items"][1]["period_stars"] = 0
        return body

    patch_json("tracked", change)
    page.goto(f"{base_url}#/tracked")
    # 今日新增 0 與負數排在正數之後、未知之前。
    assert _cards(page)[:5] == ["o/steady", "o/typed", "late/burst", "hot/rocket", "s/new-00"]
    deltas = page.locator(".card .delta")
    expect(deltas.nth(0)).to_have_text("+5")
    expect(deltas.nth(2)).to_have_text("0")
    expect(deltas.nth(3)).to_have_text("-2")


def test_tracked_matches_multi_word_language(page: Page, base_url: str, patch_json: Patch) -> None:
    def add_board(body: Any) -> Any:
        body["daily"]["jupyter-notebook"] = []
        return body

    def set_language(body: Any) -> Any:
        body["items"][0]["language"] = "Jupyter Notebook"
        return body

    patch_json("trending", add_board)
    patch_json("tracked", set_language)
    page.goto(f"{base_url}#/tracked?lang=jupyter-notebook")
    assert _cards(page) == ["hot/rocket"]


def test_back_restores_filter(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/trending?lang=python")
    page.locator(".card .name a", has_text="o/steady").click()
    expect(page.locator("h2")).to_contain_text("o/steady")
    page.locator("[data-back]").click()
    expect(page).to_have_url(f"{base_url}#/trending?lang=python")
    expect(page.locator('#filters [data-lang="python"]')).to_have_attribute("aria-pressed", "true")


def test_theme_persists_after_reload(page: Page, base_url: str) -> None:
    page.goto(base_url)
    html = page.locator("html")
    page.locator("#themeBtn").click()
    expect(html).to_have_attribute("data-theme", "light")
    page.locator("#themeBtn").click()
    expect(html).to_have_attribute("data-theme", "dark")
    page.reload()
    expect(html).to_have_attribute("data-theme", "dark")


def test_page_meta_and_footer(page: Page, base_url: str) -> None:
    page.goto(base_url)
    expect(page.locator("html")).to_have_attribute("lang", "zh-Hant")
    expect(page.locator('meta[property="og:image"]')).to_have_attribute(
        "content", f"{SITE_URL}social-preview.png"
    )
    link = page.locator(".gh-link")
    expect(link).to_have_attribute("href", f"https://github.com/{GITHUB_REPO}")
    expect(link).to_contain_text(GITHUB_REPO)


@pytest.mark.parametrize("route", ROUTES)
def test_english_interface_has_no_chinese(page: Page, base_url_en: str, route: str) -> None:
    page.goto(base_url_en + route)
    _loaded(page)
    expect(page.locator("html")).to_have_attribute("lang", "en")
    assert _CJK.findall(page.evaluate(_UI_TEXT_JS)) == []


def test_report_body_keeps_its_own_language(page: Page, base_url_en: str) -> None:
    page.goto(f"{base_url_en}#/reports/weekly/2026-W47")
    expect(page.locator(".report")).to_have_attribute("lang", "zh-Hant")


@pytest.mark.parametrize("width", [360, 768, 1440, 2560])
@pytest.mark.parametrize("route", ROUTES)
def test_no_horizontal_scroll(page: Page, base_url: str, width: int, route: str) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(base_url + route)
    _loaded(page)
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0


@pytest.mark.parametrize("width", [360, 1440])
@pytest.mark.parametrize("route", ROUTES)
def test_english_no_horizontal_scroll(page: Page, base_url_en: str, width: int, route: str) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(base_url_en + route)
    _loaded(page)
    assert page.evaluate("document.documentElement.scrollWidth - window.innerWidth") <= 0


@pytest.mark.parametrize(("data_date", "fresh"), [("2026-11-29", 1), ("2026-11-30", 0)])
def test_report_new_mark_boundary(
    page: Page, base_url: str, patch_json: Patch, data_date: str, fresh: int
) -> None:
    patch_json("meta", lambda meta: meta | {"data_date": data_date})
    page.goto(f"{base_url}#/reports")
    # 2026-W47 結束於 11-22：距 11-29 是 7 天，距 11-30 是 8 天。
    expect(page.locator(".rp-item").first.locator(".flag.new")).to_have_count(fresh)


def test_seen_store_keeps_newest_entries(page: Page, base_url: str) -> None:
    store_seen(page, [f"old/{n}" for n in range(5000)])
    page.goto(base_url)
    expect(page.locator(".card")).to_have_count(4)
    _loaded(page)
    seen = page.evaluate("JSON.parse(localStorage.getItem('starstrata-seen'))")
    assert len(seen) == 5000
    assert {"o/steady", "hot/rocket", "late/single", "o/typed"} <= set(seen)


def test_english_missing_board_name(page: Page, base_url_en: str, patch_json: Patch) -> None:
    patch_json("meta", _with_run(trending_missing=[{"period": "weekly", "language": "rust"}]))
    page.goto(base_url_en)
    expect(page.locator("#notice")).to_contain_text("This week · Rust")


def test_late_repo_response_does_not_affect_next_page(page: Page, base_url: str) -> None:
    """repo 詳細檔晚到時，已換到的下一頁不能被它影響（例如找不到圖表元素而出錯）。"""
    held: dict[str, Route] = {}
    page.route("**/data/repos/**", lambda route: held.__setitem__("repo", route))
    page.route("**/data/trending.json", lambda route: held.__setitem__("trending", route))
    with page.expect_request("**/data/repos/**"):
        page.goto(f"{base_url}#/repo/hot/rocket")
    with page.expect_request("**/data/trending.json"):
        page.evaluate("location.hash = '#/trending'")
    with page.expect_response("**/data/repos/**"):
        held["repo"].continue_()
    # 等舊的 render 處理完晚到的詳細檔，再放行下一頁的資料。
    page.evaluate("new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    held["trending"].continue_()
    expect(page.locator(".card")).to_have_count(4)
    page.wait_for_load_state("networkidle")
    expect(page.locator("#chartCum")).to_have_count(0)


def test_skip_link_moves_focus_without_changing_route(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/repo/o/steady")
    _loaded(page)
    history_length = page.evaluate("history.length")
    page.keyboard.press("Tab")
    expect(page.locator(".skip")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#main")).to_be_focused()
    expect(page).to_have_url(f"{base_url}#/repo/o/steady")
    expect(page.locator("#view h2")).to_contain_text("o/steady")
    assert page.evaluate("history.length") == history_length


@pytest.mark.parametrize(
    "path", ["../meta", "o/..", "%2e%2e/meta", "o%2F..%2F../meta", "o//steady", "o/steady/"]
)
def test_repo_path_with_dot_segments_is_not_found(page: Page, base_url: str, path: str) -> None:
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url) if "/data/" in r.url else None)
    page.goto(f"{base_url}#/repo/{path}")
    expect(page.get_by_text("找不到這個 repo")).to_be_visible()
    page.wait_for_load_state("networkidle")
    assert requests == [f"{base_url}data/meta.json"]
