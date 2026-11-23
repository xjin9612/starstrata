"""用 star history API 取得 repo 過去每天新增的 star 數。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from starstrata.github.http import GitHubClient, GitHubError

# API 每頁最多 30 週，最多 100 頁。
PER_PAGE = 30
MAX_PAGES = 100


@dataclass(frozen=True)
class Week:
    # 週日（UTC）的日期。
    start: date
    # 週日起每天新增的 star 數，共 7 個。
    days: tuple[int, ...]


@dataclass(frozen=True)
class History:
    # 只含新增 star 數大於 0 的週，由舊到新。超過請求上限而沒有抓完時為 None。
    weeks: list[Week] | None
    requests: int


def estimate_requests(created_at: str | None, today: date) -> int:
    """依 repo 建立時間估計需要的請求數。不知道建立時間時估 1。"""
    if not created_at:
        return 1
    created = datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()
    weeks = max((today - created).days, 0) // 7 + 2
    return min(math.ceil(weeks / PER_PAGE), MAX_PAGES)


def fetch_history(client: GitHubClient, full_name: str, *, max_requests: int) -> History:
    """抓取完整歷史；需要的請求數超過 max_requests 時停止並回傳 weeks=None。

    翻頁期間有新的一週開始時，同一週會出現兩次，保留先抓到的。
    """
    weeks: dict[date, Week] = {}
    requests = 0
    for page in range(1, MAX_PAGES + 1):
        if requests >= max_requests:
            return History(weeks=None, requests=requests)
        items = client.rest_get(
            f"/repos/{full_name}/stargazers/history", {"per_page": PER_PAGE, "page": page}
        )
        requests += 1
        if not isinstance(items, list):
            raise GitHubError(f"{full_name} 的 star history 回應格式不符")
        for item in items:
            days = tuple(int(n) for n in item["days"])
            start = datetime.fromtimestamp(int(item["week"]), UTC).date()
            if sum(days) > 0 and start not in weeks:
                weeks[start] = Week(start=start, days=days)
        if len(items) < PER_PAGE:
            break
    return History(weeks=[weeks[k] for k in sorted(weeks)], requests=requests)


def expand(week: Week) -> list[tuple[date, int]]:
    """展開成 (日期, 新增 star 數)，略過 0。"""
    return [(week.start + timedelta(days=i), n) for i, n in enumerate(week.days) if n > 0]
