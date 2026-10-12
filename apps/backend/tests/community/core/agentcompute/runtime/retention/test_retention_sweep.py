"""Artifact TTL sweeper unit tests (task 12.9: `AgentComputeRetentionSweeper`).

Contract under test, per its design notes and D38:
* store-scoped sweep over tmp artifact roots — no DB, no tenancy;
* `{run_id}/{agent_id}/result` keys only; foreign keys are skipped;
* TTL from `artifact_ttl_days`, age by store mtime, strictly older;
* injected clock for determinism; dry-run lists without touching;
* max_batch caps one invocation; per-key failures log and continue;
* never deletes anything outside `artifact_store_root`.
"""
from __future__ import annotations

import os

import pytest

from agentclaw.community.core.agentcompute.config.settings import (
    AgentComputeRetentionConfig,
)
from agentclaw.community.core.agentcompute.plugins.artifact_store.filesystem import (
    FilesystemArtifactStore,
    FilesystemRetentionScanner,
)
from agentclaw.community.core.agentcompute.runtime.retention import (
    AgentComputeRetentionSweeper,
)

CLOCK = 1_000_000.0
TTL_DAYS = 7
CUTOFF = CLOCK - TTL_DAYS * 86400.0
OLDER_THAN_TTL = 100.0
NEWER_THAN_TTL = CLOCK - 100.0


class _FixedClock:
    def __call__(self) -> float:
        return CLOCK


def _mk_sweeper(root) -> tuple[AgentComputeRetentionSweeper, FilesystemArtifactStore]:
    store = FilesystemArtifactStore(str(root))
    config = AgentComputeRetentionConfig(artifact_ttl_days=TTL_DAYS)
    return (
        AgentComputeRetentionSweeper(
            store, store.retention_scanner(), config, clock=_FixedClock()
        ),
        store,
    )


def _age(store: FilesystemArtifactStore, key: str, mtime: float) -> None:
    store.put(key, f"payload-{key}")
    os.utime(store.root.joinpath(*key.split("/")), (mtime, mtime))


class TestSweeperContract:

    def test_deletes_old_keeps_recent_untouched(self, tmp_path):
        sweeper, store = _mk_sweeper(tmp_path / "artifacts")
        old_key = "run-1/agent-a/result"
        new_key = "run-1/agent-b/result"
        _age(store, old_key, OLDER_THAN_TTL)
        _age(store, new_key, NEWER_THAN_TTL)

        outcome = sweeper.sweep()

        assert outcome.deleted == (old_key,)
        assert outcome.errors == ()
        assert store.get(old_key) is None
        assert store.get(new_key) == f"payload-{new_key}".encode()

    def test_ttl_boundary_is_exclusive(self, tmp_path):
        sweeper, store = _mk_sweeper(tmp_path / "artifacts")
        boundary_key = "run-1/agent-a/result"
        _age(store, boundary_key, CUTOFF)

        outcome = sweeper.sweep()

        assert outcome.would_delete == ()
        assert outcome.deleted == ()
        assert store.get(boundary_key) is not None

    def test_dry_run_lists_without_deleting(self, tmp_path):
        sweeper, store = _mk_sweeper(tmp_path / "artifacts")
        old_key = "run-1/agent-a/result"
        _age(store, old_key, OLDER_THAN_TTL)

        outcome = sweeper.sweep(dry_run=True)

        assert outcome.would_delete == (old_key,)
        assert outcome.deleted == ()
        assert outcome.errors == ()
        assert store.get(old_key) is not None

        outcome_real = sweeper.sweep()
        assert outcome_real.deleted == (old_key,)
        assert store.get(old_key) is None

    def test_non_conformant_keys_are_skipped_not_deleted(self, tmp_path):
        sweeper, store = _mk_sweeper(tmp_path / "artifacts")
        foreign = (
            "loose-file.tmp",
            "run-3/result",
            "run-1/agent-a/result.inflight",
            "run-4/agent-a/result/extra-segment",
            "run-2/agent-a/other-kind",
        )
        for key in foreign:
            _age(store, key, OLDER_THAN_TTL)
        _age(store, "run-1/agent-a/result", OLDER_THAN_TTL)

        outcome = sweeper.sweep()

        assert outcome.deleted == ("run-1/agent-a/result",)
        assert sorted(outcome.skipped) == sorted(foreign)
        for key in foreign:
            assert store.get(key) is not None, f"{key} must not be touched"

    def test_escape_attempt_is_error_never_crosses_root(self, tmp_path):
        sweeper, store = _mk_sweeper(tmp_path / "artifacts")
        outside = tmp_path / "secrets.txt"
        outside.write_text("do not touch", encoding="utf-8")
        os.utime(outside, (OLDER_THAN_TTL, OLDER_THAN_TTL))

        class _HostileScanner:
            def __init__(self, real: FilesystemRetentionScanner) -> None:
                self._real = real

            def list_keys(self) -> list[str]:
                return [
                    "../secrets.txt",
                    "../secrets.txt/result",
                    "run-1/agent-a/result",
                ]

            def key_mtime(self, key: str) -> float:
                return self._real.key_mtime(key)

        hostile = _HostileScanner(store.retention_scanner())
        config = AgentComputeRetentionConfig(artifact_ttl_days=TTL_DAYS)
        rigged = AgentComputeRetentionSweeper(
            store, hostile, config, clock=_FixedClock()
        )
        _age(store, "run-1/agent-a/result", OLDER_THAN_TTL)

        outcome = rigged.sweep()

        assert outcome.deleted == ("run-1/agent-a/result",)
        assert len(outcome.errors) == 1
        assert "../secrets.txt/result" in outcome.errors[0]
        assert "../secrets.txt" in outcome.skipped
        assert outside.read_text(encoding="utf-8") == "do not touch"

    def test_single_file_error_is_logged_and_sweep_continues(self, tmp_path):
        sweeper, store = _mk_sweeper(tmp_path / "artifacts")
        (store.root / "run-1" / "agent-a").mkdir(parents=True, exist_ok=True)
        broken = store.root / "run-1" / "agent-a" / "result"
        try:
            os.symlink("missing-target", broken)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable on this platform")
        os.utime(broken, (OLDER_THAN_TTL, OLDER_THAN_TTL), follow_symlinks=False)
        other_key = "run-1/agent-b/result"
        _age(store, other_key, OLDER_THAN_TTL)

        outcome = sweeper.sweep()

        assert outcome.deleted == (other_key,)
        assert len(outcome.errors) == 1
        assert "run-1/agent-a/result" in outcome.errors[0]
        assert store.get(other_key) is None
        assert broken.is_symlink()

    def test_max_batch_bounds_one_invocation(self, tmp_path):
        sweeper, store = _mk_sweeper(tmp_path / "artifacts")
        keys = [f"run-{i}/agent-a/result" for i in range(3)]
        for key in keys:
            _age(store, key, OLDER_THAN_TTL)

        first = sweeper.sweep(max_batch=2)

        assert len(first.deleted) == 2
        remaining = [key for key in keys if store.get(key) is not None]
        assert len(remaining) == 1

        second = sweeper.sweep(max_batch=2)
        assert len(second.deleted) == 1
        assert all(store.get(key) is None for key in keys)

    def test_max_batch_counts_dry_run_matches_too(self, tmp_path):
        sweeper, store = _mk_sweeper(tmp_path / "artifacts")
        keys = [f"run-{i}/agent-a/result" for i in range(3)]
        for key in keys:
            _age(store, key, OLDER_THAN_TTL)

        outcome = sweeper.sweep(dry_run=True, max_batch=2)

        assert len(outcome.would_delete) == 2
        assert outcome.deleted == ()
        assert all(store.get(key) is not None for key in keys)

    def test_empty_root_sweeps_clean(self, tmp_path):
        sweeper, store = _mk_sweeper(tmp_path / "artifacts")
        outcome = sweeper.sweep()
        assert outcome.would_delete == ()
        assert outcome.deleted == ()
        assert outcome.skipped == ()
        assert outcome.errors == ()

    def test_recent_and_conformant_pass_noop(self, tmp_path):
        sweeper, store = _mk_sweeper(tmp_path / "artifacts")
        _age(store, "run-1/agent-a/result", NEWER_THAN_TTL)
        outcome = sweeper.sweep()
        assert outcome.would_delete == ()
        assert outcome.skipped == ()
        assert outcome.errors == ()


class TestFilesystemRetentionScanner:

    def test_lists_relative_posix_keys_sorted(self, tmp_path):
        store = FilesystemArtifactStore(str(tmp_path / "artifacts"))
        scanner = store.retention_scanner()
        for key in ("run-2/agent-b/result", "run-1/agent-a/result", "loose.bin"):
            store.put(key, "x")
        (store.root / "run-1" / "agent-a" / "empty-dir").mkdir()
        assert scanner.list_keys() == [
            "loose.bin",
            "run-1/agent-a/result",
            "run-2/agent-b/result",
        ]

    def test_key_mtime_refuses_keys_outside_root(self, tmp_path):
        store = FilesystemArtifactStore(str(tmp_path / "artifacts"))
        scanner = store.retention_scanner()
        with pytest.raises(ValueError):
            scanner.key_mtime("../outside.txt")

    def test_scanner_satisfies_the_retention_protocol(self, tmp_path):
        from agentclaw.community.core.agentcompute.spi.artifact import (
            RetentionScanner,
        )

        store = FilesystemArtifactStore(str(tmp_path / "artifacts"))
        scanner = store.retention_scanner()
        assert isinstance(scanner, RetentionScanner)
        assert isinstance(scanner, FilesystemRetentionScanner)