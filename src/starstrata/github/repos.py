"""用 GraphQL 批次查詢 repo 快照。"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from starstrata.github.http import GitHubClient, GitHubError, GraphQLError, RateLimitExceeded
from starstrata.snapshots import RepoMeta, RepoSnapshot

# GraphQL nodes(ids:) 一次最多 100 個。
IDS_BATCH_SIZE = 100
# 以名稱查詢時每個 repo 是一個 alias，批次小一點以控制單次查詢的大小。
NAMES_BATCH_SIZE = 50

_FRAGMENT = """
fragment RepoFields on Repository {
  id
  nameWithOwner
  stargazerCount
  forkCount
  watchers { totalCount }
  issues(states: OPEN) { totalCount }
  description
  repositoryTopics(first: 20) { nodes { topic { name } } }
  primaryLanguage { name }
  isArchived
  createdAt
}
"""

_BY_IDS_QUERY = (
    _FRAGMENT + "query($ids: [ID!]!) { nodes(ids: $ids) { ... on Repository { ...RepoFields } } }"
)


@dataclass(frozen=True)
class IdsBatch:
    snapshots: list[RepoSnapshot]
    # 不再查詢的 node ID：GitHub 回報 NOT_FOUND 或 node 本身的錯誤（例如 FORBIDDEN），
    # 或 ID 不是 repo。
    gone: list[str]
    # gone 之中因 NOT_FOUND 以外的錯誤而停止查詢的 node 與錯誤訊息。
    unavailable: list[str] = field(default_factory=list)
    # 暫時查不到的 node 與原因：子欄位錯誤、沒有錯誤說明的 null、無法解析。不算 gone，下次照常查詢。
    node_errors: list[str] = field(default_factory=list)
    # 整批查詢失敗的原因；此時這批的 ID 既不在 snapshots 也不在 gone。
    error: str | None = None


@dataclass(frozen=True)
class NamesBatch:
    # key 是查詢時用的名稱，不一定與 snapshot.full_name 大小寫相同。
    snapshots: dict[str, RepoSnapshot]
    missing: list[str]
    node_errors: list[str] = field(default_factory=list)
    error: str | None = None


def _parse(node: dict[str, Any]) -> RepoSnapshot:
    topics = tuple(t["topic"]["name"] for t in node["repositoryTopics"]["nodes"])
    language = node["primaryLanguage"]["name"] if node["primaryLanguage"] else None
    return RepoSnapshot(
        node_id=node["id"],
        full_name=node["nameWithOwner"],
        stars=node["stargazerCount"],
        forks=node["forkCount"],
        watchers=node["watchers"]["totalCount"],
        open_issues=node["issues"]["totalCount"],
        meta=RepoMeta(
            description=node["description"],
            topics=topics,
            language=language,
            archived=node["isArchived"],
            created_at=node["createdAt"],
        ),
    )


def _try_parse(node: dict[str, Any]) -> RepoSnapshot | None:
    try:
        return _parse(node)
    except KeyError, TypeError:
        return None


def _batches(items: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def fetch_by_ids(client: GitHubClient, node_ids: Sequence[str]) -> Iterator[IdsBatch]:
    """每查完一批就 yield。

    單一批次失敗時 yield 帶 error 的批次並繼續下一批。node 的分類見 IdsBatch 各欄位。
    API 額度用盡時丟出 RateLimitExceeded。
    """
    for batch in _batches(node_ids, IDS_BATCH_SIZE):
        try:
            result = client.graphql(_BY_IDS_QUERY, {"ids": list(batch)})
            nodes = result.data.get("nodes")
            if not isinstance(nodes, list) or len(nodes) != len(batch):
                raise GraphQLError("nodes 數量與查詢不符")
            snapshots: list[RepoSnapshot] = []
            gone: list[str] = []
            unavailable: list[str] = []
            node_errors: list[str] = []
            for i, (requested, node) in enumerate(zip(batch, nodes, strict=True)):
                if node and "id" in node:
                    snap = _try_parse(node)
                    if snap is None:
                        node_errors.append(f"{requested}: 無法解析")
                    else:
                        snapshots.append(snap)
                elif node == {}:
                    # 不是 Repository 的 node 會回傳空物件。
                    gone.append(requested)
                elif ("nodes", i) in result.not_found_paths:
                    gone.append(requested)
                elif ("nodes", i) in result.field_errors:
                    # node 本身的錯誤視為持續性，停止查詢；再次上榜時恢復追蹤。
                    gone.append(requested)
                    unavailable.append(f"{requested}: {result.field_errors[('nodes', i)]}")
                else:
                    reason = result.error_under("nodes", i) or "沒有資料也沒有錯誤說明"
                    node_errors.append(f"{requested}: {reason}")
        except RateLimitExceeded:
            raise
        except (GitHubError, KeyError, TypeError, ValueError) as e:
            yield IdsBatch(snapshots=[], gone=[], error=f"{type(e).__name__}: {e}")
            continue
        yield IdsBatch(
            snapshots=snapshots, gone=gone, unavailable=unavailable, node_errors=node_errors
        )


def _by_names_query(count: int) -> str:
    params = ", ".join(f"$o{i}: String!, $n{i}: String!" for i in range(count))
    fields = " ".join(
        f"r{i}: repository(owner: $o{i}, name: $n{i}) {{ ...RepoFields }}" for i in range(count)
    )
    return f"{_FRAGMENT}query({params}) {{ {fields} }}"


def fetch_by_names(client: GitHubClient, full_names: Sequence[str]) -> Iterator[NamesBatch]:
    """full_names 的格式為 owner/name。錯誤處理與 fetch_by_ids 相同。"""
    for batch in _batches(full_names, NAMES_BATCH_SIZE):
        variables: dict[str, str] = {}
        for i, full_name in enumerate(batch):
            owner, _, name = full_name.partition("/")
            variables[f"o{i}"] = owner
            variables[f"n{i}"] = name
        try:
            result = client.graphql(_by_names_query(len(batch)), variables)
            snapshots: dict[str, RepoSnapshot] = {}
            missing: list[str] = []
            node_errors: list[str] = []
            for i, full_name in enumerate(batch):
                node = result.data.get(f"r{i}")
                snap = _try_parse(node) if node else None
                if snap is not None:
                    snapshots[full_name] = snap
                    continue
                missing.append(full_name)
                error = result.error_under(f"r{i}") or ("無法解析" if node else None)
                if error:
                    node_errors.append(f"{full_name}: {error}")
        except RateLimitExceeded:
            raise
        except (GitHubError, KeyError, TypeError, ValueError) as e:
            yield NamesBatch(snapshots={}, missing=[], error=f"{type(e).__name__}: {e}")
            continue
        yield NamesBatch(snapshots=snapshots, missing=missing, node_errors=node_errors)
