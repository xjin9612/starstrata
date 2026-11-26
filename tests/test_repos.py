from __future__ import annotations

import json

import pytest

from starstrata.github.http import RateLimitExceeded
from starstrata.github.repos import IDS_BATCH_SIZE, NAMES_BATCH_SIZE, fetch_by_ids, fetch_by_names
from starstrata.snapshots import RepoMeta
from tests.conftest import FakeGitHub, repo_node


def test_snapshot_fields_are_parsed(fake_github: FakeGitHub) -> None:
    fake_github.repos = [
        repo_node(
            "R_a",
            "Owner/Repo",
            stars=1234,
            forks=56,
            watchers=7,
            open_issues=8,
            description="hello",
            topics=["cli", "go"],
            language="Go",
            archived=True,
            created_at="2020-05-06T07:08:09Z",
        )
    ]
    [batch] = list(fetch_by_ids(fake_github.client(), ["R_a"]))
    [snap] = batch.snapshots
    assert (snap.node_id, snap.full_name) == ("R_a", "Owner/Repo")
    assert (snap.stars, snap.forks, snap.watchers, snap.open_issues) == (1234, 56, 7, 8)
    assert snap.meta == RepoMeta(
        description="hello",
        topics=("cli", "go"),
        language="Go",
        archived=True,
        created_at="2020-05-06T07:08:09Z",
    )


def test_repo_without_language_or_description(fake_github: FakeGitHub) -> None:
    fake_github.repos = [repo_node("R_a", "o/a", language=None, description=None)]
    [batch] = list(fetch_by_ids(fake_github.client(), ["R_a"]))
    assert batch.snapshots[0].meta.language is None
    assert batch.snapshots[0].meta.description is None


def test_missing_ids_are_reported_gone(fake_github: FakeGitHub) -> None:
    fake_github.repos = [repo_node("R_a", "o/a")]
    [batch] = list(fetch_by_ids(fake_github.client(), ["R_gone", "R_a"]))
    assert [s.node_id for s in batch.snapshots] == ["R_a"]
    assert batch.gone == ["R_gone"]


def test_ids_are_split_into_batches(fake_github: FakeGitHub) -> None:
    ids = [f"R_{i}" for i in range(IDS_BATCH_SIZE + 1)]
    fake_github.repos = [repo_node(i, f"o/{i}") for i in ids]
    batches = list(fetch_by_ids(fake_github.client(), ids))
    assert [len(b.snapshots) for b in batches] == [IDS_BATCH_SIZE, 1]


def test_earlier_batches_survive_a_later_failure(fake_github: FakeGitHub) -> None:
    ids = [f"R_{i}" for i in range(IDS_BATCH_SIZE + 1)]
    fake_github.repos = [repo_node(i, f"o/{i}") for i in ids]
    fake_github.graphql_rate_limited_from = 2
    received = []
    with pytest.raises(RateLimitExceeded):
        for batch in fetch_by_ids(fake_github.client(), ids):
            received.append(batch)
    assert len(received) == 1


def test_names_are_queried_with_variables(fake_github: FakeGitHub) -> None:
    fake_github.repos = [repo_node("R_a", "Owner/Repo")]
    [batch] = list(fetch_by_names(fake_github.client(), ["owner/repo", "ghost/none"]))
    assert batch.snapshots["owner/repo"].full_name == "Owner/Repo"
    assert batch.missing == ["ghost/none"]
    body = json.loads(fake_github.requests[0].content)
    assert body["variables"] == {"o0": "owner", "n0": "repo", "o1": "ghost", "n1": "none"}
    assert "ghost" not in body["query"]


def test_names_are_split_into_batches(fake_github: FakeGitHub) -> None:
    names = [f"o/r{i}" for i in range(NAMES_BATCH_SIZE + 1)]
    fake_github.repos = [repo_node(f"R_{n}", n) for n in names]
    batches = list(fetch_by_names(fake_github.client(), names))
    assert [len(b.snapshots) for b in batches] == [NAMES_BATCH_SIZE, 1]


def test_node_errors_are_classified(fake_github: FakeGitHub) -> None:
    ids = ["R_ok", "R_forbidden", "R_timeout", "R_silent", "R_missing"]
    fake_github.repos = [repo_node(i, f"o/{i}") for i in ids if i != "R_missing"]
    fake_github.forbidden_ids = {"R_forbidden"}
    fake_github.subfield_error_ids = {"R_timeout"}
    fake_github.silent_null_ids = {"R_silent"}
    [batch] = list(fetch_by_ids(fake_github.client(), ids))
    assert [s.node_id for s in batch.snapshots] == ["R_ok"]
    assert batch.gone == ["R_forbidden", "R_missing"]
    assert batch.unavailable == ["R_forbidden: FORBIDDEN: dmca"]
    assert batch.node_errors == [
        "R_timeout: SERVICE_UNAVAILABLE: timedout",
        "R_silent: 沒有資料也沒有錯誤說明",
    ]


def test_failed_batch_does_not_stop_later_batches(fake_github: FakeGitHub) -> None:
    ids = [f"R_{i}" for i in range(IDS_BATCH_SIZE * 2 + 1)]
    fake_github.repos = [repo_node(i, f"o/{i}") for i in ids]
    fake_github.graphql_status_on_call = {1: 400}
    batches = list(fetch_by_ids(fake_github.client(), ids))
    assert [b.error is not None for b in batches] == [True, False, False]
    assert sum(len(b.snapshots) for b in batches) == IDS_BATCH_SIZE + 1


def test_unparseable_node_is_reported_not_gone(fake_github: FakeGitHub) -> None:
    broken = repo_node("R_a", "o/a")
    del broken["stargazerCount"]
    fake_github.repos = [broken, repo_node("R_b", "o/b")]
    [batch] = list(fetch_by_ids(fake_github.client(), ["R_a", "R_b"]))
    assert [s.node_id for s in batch.snapshots] == ["R_b"]
    assert batch.gone == [] and batch.node_errors == ["R_a: 無法解析"]


@pytest.mark.parametrize("error_type", [None, "SERVICE_UNAVAILABLE", "INTERNAL"])
def test_node_error_other_than_forbidden_is_retried(
    fake_github: FakeGitHub, error_type: str | None
) -> None:
    fake_github.repos = [repo_node("R_a", "o/a")]
    fake_github.node_error_types = {"R_a": error_type}
    [batch] = list(fetch_by_ids(fake_github.client(), ["R_a"]))
    assert batch.gone == [] and batch.unavailable == []
    assert batch.node_errors == [f"R_a: {error_type}: failed"]
