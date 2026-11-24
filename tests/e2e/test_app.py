"""dashboard（web/app.js）的瀏覽器測試。資料見 conftest.py 的 write_fixture_data()。"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from tests.e2e.conftest import SEARCH_ONLY_NEW

pytestmark = pytest.mark.e2e


def _kpi(page: Page, label: str) -> str:
    return page.locator(".kpi", has=page.locator("dt", has_text=label)).locator("dd").inner_text()


def _cards(page: Page) -> list[str]:
    return page.locator(".card .name a").all_inner_texts()


def test_trending_default_and_filters(page: Page, base_url: str) -> None:
    page.goto(base_url)
    expect(page.locator(".view-head p")).to_have_text("全部語言・今日・共 3 筆")
    assert _cards(page) == ["o/steady", "hot/rocket", "late/single"]
    expect(page.locator("#hdrDate")).to_have_text("2026-11-24")

    page.locator('#filters [data-lang="Python"]').click()
    expect(page).to_have_url(f"{base_url}#/trending?lang=Python")
    expect(page.locator('#filters [data-lang="Python"]')).to_have_attribute("aria-pressed", "true")
    assert _cards(page) == ["o/steady"]

    page.locator('#filters [data-lang="全部"]').click()
    page.locator('#filters [data-period="weekly"]').click()
    expect(page.locator(".view-head p")).to_have_text("全部語言・本週・共 1 筆")


def test_trending_filter_without_results(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/trending?lang=Rust")
    expect(page.get_by_text("Rust 目前沒有上榜的 repo")).to_be_visible()
    page.get_by_role("button", name="顯示全部語言").click()
    expect(page.locator(".view-head p")).to_have_text("全部語言・今日・共 3 筆")


def test_new_shows_search_only_and_more(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/new")
    search_only = page.locator(".card", has=page.locator(".name", has_text="s/new-00"))
    expect(search_only.locator(".per.src")).to_have_text("來自 Search，沒有今日 Trending 數字")
    expect(search_only.locator(".delta")).to_have_count(0)

    total = SEARCH_ONLY_NEW + 1
    expect(page.locator(".card")).to_have_count(24)
    page.locator("#moreNew").click()
    expect(page.locator(".card")).to_have_count(total)
    expect(page.locator(".all-shown")).to_have_text(f"已顯示全部 {total} 筆")
    # 在 Trending 上但沒有新增數的 repo 不標成來自 Search。
    trending_only = page.locator(".card", has=page.locator(".name", has_text="late/single"))
    expect(trending_only.locator(".delta")).to_have_text("—")
    expect(trending_only.locator(".per.src")).to_have_count(0)


def test_daily_ranks_by_gain(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/daily")
    expect(page.locator(".card").first.locator(".name")).to_have_text("hot/rocket")
    expect(page.locator(".card").first.locator(".delta")).to_have_text("+900")


def test_alerts_with_and_without_baseline(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/alerts")
    rows = page.locator("table tbody tr")
    expect(rows).to_have_count(2)
    hot = rows.filter(has_text="hot/rocket")
    expect(hot.locator('td[data-label="近 7 日平均"]')).to_have_text("10")
    expect(hot.locator(".mult-bar")).to_have_count(1)
    burst = rows.filter(has_text="late/burst")
    expect(burst.locator('td[data-label="近 7 日平均"]')).to_have_text("資料不足")
    expect(burst.locator('td[data-label="倍數"]')).to_have_text("—")


def test_repo_with_backfill(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/repo/hot/rocket")
    expect(page.locator("h2")).to_contain_text("hot/rocket")
    assert _kpi(page, "總 star") == "1,970"
    assert _kpi(page, "近 7 日新增").startswith("+960")
    assert _kpi(page, "近 30 日新增").startswith("+975")
    assert _kpi(page, "Trending 上榜天數") == "1天"
    expect(page.locator("#chartCum svg")).to_have_count(1)
    expect(page.locator("#chartDaily svg")).to_have_count(1)


def test_repo_with_single_snapshot(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/repo/late/single")
    expect(page.locator("h2")).to_contain_text("late/single")
    assert _kpi(page, "近 7 日新增") == "尚無資料"
    assert _kpi(page, "近 30 日新增") == "尚無資料"


def test_repo_not_found(page: Page, base_url: str, failed_requests: list[str]) -> None:
    page.goto(f"{base_url}#/repo/nobody/nothing")
    expect(page.get_by_text("找不到這個 repo")).to_be_visible()
    assert failed_requests == [f"{base_url}data/repos/nobody/nothing.json"]
    failed_requests.clear()


def test_search_opens_repo(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.locator("#q").fill("rocket")
    expect(page.locator("#qList .dd-item")).to_have_count(1)
    page.locator("#q").press("ArrowDown")
    page.locator("#q").press("Enter")
    expect(page).to_have_url(f"{base_url}#/repo/hot/rocket")

    page.locator("#q").fill("zzz-none")
    expect(page.locator("#qPanel")).to_contain_text("找不到符合「zzz-none」的 repo")


def test_back_restores_filter(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}#/trending?lang=Python")
    page.locator(".card .name a", has_text="o/steady").click()
    expect(page.locator("h2")).to_contain_text("o/steady")
    # 每天同時在總榜與 Python 榜，最後一天另有本週總榜，上榜天數只算日期。
    assert _kpi(page, "Trending 上榜天數") == "9天"
    page.locator("[data-back]").click()
    expect(page).to_have_url(f"{base_url}#/trending?lang=Python")
    expect(page.locator('#filters [data-lang="Python"]')).to_have_attribute("aria-pressed", "true")


def test_theme_persists_after_reload(page: Page, base_url: str) -> None:
    page.goto(base_url)
    html = page.locator("html")
    page.locator("#themeBtn").click()
    expect(html).to_have_attribute("data-theme", "light")
    page.locator("#themeBtn").click()
    expect(html).to_have_attribute("data-theme", "dark")
    page.reload()
    expect(html).to_have_attribute("data-theme", "dark")
    page.locator("#themeBtn").click()
    expect(html).not_to_have_attribute("data-theme", "dark")


@pytest.mark.parametrize("width", [360, 768, 1440, 2560])
@pytest.mark.parametrize("route", ["#/trending", "#/new", "#/alerts", "#/repo/hot/rocket"])
def test_no_horizontal_scroll(page: Page, base_url: str, width: int, route: str) -> None:
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(base_url + route)
    expect(page.locator("#view > *").first).to_be_visible()
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    assert overflow <= 0
