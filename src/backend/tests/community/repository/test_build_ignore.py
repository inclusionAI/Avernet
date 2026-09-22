"""Real database coverage for build-rule isolation and atomic updates."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def repo(tmp_path):
    from agentclaw.community.core.repository.implementations.build_ignore import (
        BuildIgnoreRepository,
    )
    from agentclaw.community.core.service_bot.repository.build_ignore import (
        BuildIgnoreModel,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'rules.db'}")
    BuildIgnoreModel.__table__.create(engine)
    factory = sessionmaker(engine)

    class DB:
        @contextmanager
        def orm_session(self):
            with factory.begin() as session:
                yield session

    return BuildIgnoreRepository(DB())


KEY = dict(env="dev", entity_id="entity", bot_id="bot", engine_type="openclaw")


def test_idempotent_changes_and_isolation(repo):
    assert repo.get(**KEY) is None
    first, changed = repo.change(
        **KEY, operation="add", path="workspace/bin", modifier="owner"
    )
    assert (first.paths, first.revision, changed) == (("workspace/bin",), 1, True)
    again, changed = repo.change(
        **KEY, operation="add", path="workspace/bin", modifier="owner"
    )
    assert (again.revision, changed) == (1, False)
    for field in KEY:
        assert repo.get(**{**KEY, field: "different"}) is None
    empty, changed = repo.change(
        **KEY, operation="remove", path="workspace/bin", modifier="owner"
    )
    assert (empty.paths, empty.revision, changed) == ((), 2, True)
    assert repo.get(**KEY).revision == 2


def test_concurrent_first_writes_do_not_lose_rules(repo):
    def add(index):
        return repo.change(
            **KEY, operation="add", path=f"workspace/{index}", modifier="owner"
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(add, range(8)))
    record = repo.get(**KEY)
    assert set(record.paths) == {f"workspace/{i}" for i in range(8)}
    assert record.revision == 8


def test_concurrent_add_and_remove_preserve_other_rules(repo):
    from threading import Barrier

    for index in range(4):
        repo.change(**KEY, operation="add", path=f"old/{index}", modifier="owner")
    start = Barrier(4)

    def change(index):
        start.wait(timeout=5)
        operation = "remove" if index < 2 else "add"
        path = f"old/{index}" if index < 2 else f"new/{index}"
        return repo.change(**KEY, operation=operation, path=path, modifier="owner")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(change, range(4)))
    assert all(changed for _, changed in results)
    record = repo.get(**KEY)
    assert set(record.paths) == {"old/2", "old/3", "new/2", "new/3"}
    assert record.revision == 8


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/home/admin/bin",
        "../bin",
        "a/../b",
        "a//b",
        "a\nb",
        "a*b",
        "a?b",
        "#x",
        "!x",
        "a\\b",
    ],
)
def test_rejects_non_literal_paths(path):
    from agentclaw.community.kernel.build_ignore import (
        normalize_build_ignore_path,
        BuildIgnoreError,
    )

    with pytest.raises(BuildIgnoreError):
        normalize_build_ignore_path(path)


def test_normalizes_relative_paths():
    from agentclaw.community.kernel.build_ignore import normalize_build_ignore_path

    assert (
        normalize_build_ignore_path("././workspace/中文 空格/") == "workspace/中文 空格"
    )


def test_tenants_cannot_read_or_mutate_each_others_rules(repo):
    from agentclaw.community.utils.avernet_tenant import avernet_tenant_scope

    with avernet_tenant_scope("alpha"):
        repo.change(**KEY, operation="add", path="alpha", modifier="a")
    with avernet_tenant_scope("beta"):
        assert repo.get(**KEY) is None
        repo.change(**KEY, operation="add", path="beta", modifier="b")
        assert repo.get(**KEY).paths == ("beta",)
    with avernet_tenant_scope("alpha"):
        assert repo.get(**KEY).paths == ("alpha",)


def test_missing_remove_and_invalid_operation(repo):
    from agentclaw.community.kernel.build_ignore import BuildIgnoreError

    config, changed = repo.change(
        **KEY, operation="remove", path="absent", modifier="a"
    )
    assert (config.revision, changed) == (0, False)
    with pytest.raises(BuildIgnoreError, match="invalid_operation"):
        repo.change(**KEY, operation="replace", path="x", modifier="a")


def test_size_limit_is_atomic(repo, monkeypatch):
    from agentclaw.community.core.repository.implementations import build_ignore
    from agentclaw.community.kernel.build_ignore import BuildIgnoreError

    monkeypatch.setattr(build_ignore, "MAX_RULE_BYTES", 4)
    with pytest.raises(BuildIgnoreError, match="ignore_rules_too_large"):
        repo.change(**KEY, operation="add", path="large", modifier="a")
    assert repo.get(**KEY) is None
