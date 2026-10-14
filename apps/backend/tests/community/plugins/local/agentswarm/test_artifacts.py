"""Path-containment, atomicity, and size-cap pins for FilesystemArtifactStore.

The escape guard must be a path-component relation: a sibling directory
whose path merely starts with the root's string must not pass. Writes
must be atomic: staged in a same-directory temp file, renamed on top,
so a reader never sees partial content and a crash leaves the old file.
Writes must be bounded: a subagent result larger than the inline
threshold spills here, and an engine reply is untrusted in size, so a
payload over ``_MAX_ARTIFACT_BYTES`` must raise before any filesystem
mutation instead of consuming unbounded disk.
"""
from __future__ import annotations

import os
import shutil

import pytest

from agentclaw.community.plugins.local.agentswarm import artifacts as artifacts_mod
from agentclaw.community.plugins.local.agentswarm.artifacts import (
    FilesystemArtifactStore,
)


class TestKeyContainment:

    def test_sibling_with_root_prefix_is_rejected(self, tmp_path):
        root = tmp_path / "art"
        evil = tmp_path / "art-evil"
        evil.mkdir()
        (evil / "x").write_bytes(b"escaped")

        store = FilesystemArtifactStore(root=str(root))
        with pytest.raises(ValueError):
            store.put("../art-evil/x", b"nope")
        with pytest.raises(ValueError):
            store.get("../art-evil/x")
        with pytest.raises(ValueError):
            store.delete("../art-evil/x")

        assert (evil / "x").read_bytes() == b"escaped"

    def test_prefix_sibling_without_existing_directory_is_rejected(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        with pytest.raises(ValueError):
            store.put("../art-evil/x", b"nope")
        assert not (tmp_path / "art-evil").exists()

    def test_plain_escape_outside_root_is_rejected(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        with pytest.raises(ValueError):
            store.put("../escape", b"x")

    def test_nested_key_within_root_works(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        assert store.put("run1/agent1/result", b"payload")
        assert store.get("run1/agent1/result") == b"payload"


class TestAtomicWrites:

    def _temp_residue(self, store: FilesystemArtifactStore) -> list:
        return [p for p in store.root.rglob("*")
                if p.name.startswith(artifacts_mod._TEMP_PREFIX)]

    def test_put_content_complete_without_temp_residue(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        assert store.put("run1/a1/result", b"complete-payload")
        assert store.get("run1/a1/result") == b"complete-payload"
        assert store.put("run1/a1/notes", "text-payload")
        assert store.get("run1/a1/notes") == b"text-payload"
        assert self._temp_residue(store) == []

    def test_put_over_existing_key_hides_old_content_until_replace(self,
                                                                    tmp_path,
                                                                    monkeypatch):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        key = "run1/a1/result"
        store.put(key, b"old-content")

        observed_at_replace = []
        real_replace = os.replace

        def _probing_replace(src, dst):
            observed_at_replace.append(store.get(key))
            real_replace(src, dst)

        monkeypatch.setattr(artifacts_mod.os, "replace", _probing_replace)

        assert store.put(key, b"new-content") is True

        assert observed_at_replace == [b"old-content"]
        assert store.get(key) == b"new-content"
        assert self._temp_residue(store) == []

    def test_failed_put_leaves_old_content_and_no_residue(self, tmp_path,
                                                            monkeypatch):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        key = "run1/a1/result"
        store.put(key, b"old-content")

        def _boom(src, dst):
            raise RuntimeError("disk went away")

        monkeypatch.setattr(artifacts_mod.shutil, "copyfileobj", _boom)

        with pytest.raises(RuntimeError, match="disk went away"):
            store.put_file(key, str(_write_source(tmp_path, b"new")))

        assert store.get(key) == b"old-content"
        assert self._temp_residue(store) == []

    def test_put_file_streams_source_atomically(self, tmp_path, monkeypatch):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        key = "run1/a1/result"
        store.put(key, b"old-content")
        new_payload = bytes(range(256)) * 4096
        source = _write_source(tmp_path, new_payload)

        observed_mid_copy = []
        real_copy = shutil.copyfileobj

        def _probing_copy(src, dst):
            real_copy(src, dst, 4096)
            observed_mid_copy.append(store.get(key))
            real_copy(src, dst)

        monkeypatch.setattr(artifacts_mod.shutil, "copyfileobj", _probing_copy)

        assert store.put_file(key, str(source)) is True

        assert observed_mid_copy == [b"old-content"]
        assert store.get(key) == new_payload
        assert self._temp_residue(store) == []

    def test_put_file_replaces_missing_target_atomically(self, tmp_path,
                                                          monkeypatch):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        key = "run1/a1/fresh"
        new_payload = b"fresh-payload"
        source = _write_source(tmp_path, new_payload)

        observed_mid_copy = []
        real_copy = shutil.copyfileobj

        def _probing_copy(src, dst):
            real_copy(src, dst, 4096)
            observed_mid_copy.append(store.get(key))
            real_copy(src, dst)

        monkeypatch.setattr(artifacts_mod.shutil, "copyfileobj", _probing_copy)

        assert store.put_file(key, str(source)) is True

        assert observed_mid_copy == [None]
        assert store.get(key) == new_payload


class TestSizeCap:
    """Oversize writes must raise before any filesystem mutation.

    A result spills here only after exceeding the inline threshold, and
    an ``avernet``-kind engine reply is untrusted in size — the guard is
    what keeps one run from exhausting the disk under the artifact root.
    The runtime catches the raise and keeps the result inline, so raising
    is the safe contract.
    """

    def _temp_residue(self, store: FilesystemArtifactStore) -> list:
        return [p for p in store.root.rglob("*")
                if p.name.startswith(artifacts_mod._TEMP_PREFIX)]

    def test_put_at_cap_round_trips(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        payload = b"x" * artifacts_mod._MAX_ARTIFACT_BYTES
        assert store.put("run1/a1/at-cap", payload) is True
        assert store.get("run1/a1/at-cap") == payload

    def test_put_below_cap_str_round_trips(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        text = "y" * (artifacts_mod._MAX_ARTIFACT_BYTES - 1)
        assert store.put("run1/a1/just-under", text) is True
        assert store.get("run1/a1/just-under") == text.encode("utf-8")

    def test_put_above_cap_raises_and_writes_nothing(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        payload = b"x" * (artifacts_mod._MAX_ARTIFACT_BYTES + 1)
        with pytest.raises(ValueError, match="artifact exceeds"):
            store.put("run1/a1/oversize", payload)
        assert not (store.root / "run1" / "a1" / "oversize").exists()
        # Rejected before mkdir: not even the parent directory appears.
        assert not (store.root / "run1").exists()
        assert self._temp_residue(store) == []

    def test_put_str_encoded_length_above_cap_raises(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        # Within the cap in characters, over it once encoded to UTF-8.
        text = "é" * (artifacts_mod._MAX_ARTIFACT_BYTES // 2 + 1)
        with pytest.raises(ValueError, match="artifact exceeds"):
            store.put("run1/a1/oversize-utf8", text)
        assert not (store.root / "run1").exists()

    def test_put_file_oversize_source_raises_and_writes_nothing(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        source = _write_source(
            tmp_path, b"z" * (artifacts_mod._MAX_ARTIFACT_BYTES + 1))
        with pytest.raises(ValueError, match="artifact exceeds"):
            store.put_file("run1/a1/oversize-file", str(source))
        assert not (store.root / "run1" / "a1" / "oversize-file").exists()
        assert not (store.root / "run1").exists()
        assert self._temp_residue(store) == []

    def test_put_file_oversize_source_keeps_existing_target(self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        key = "run1/a1/result"
        store.put(key, b"old-content")
        source = _write_source(
            tmp_path, b"z" * (artifacts_mod._MAX_ARTIFACT_BYTES + 1))
        with pytest.raises(ValueError, match="artifact exceeds"):
            store.put_file(key, str(source))
        assert store.get(key) == b"old-content"
        assert self._temp_residue(store) == []

    def test_oversize_payload_with_escaping_key_raises_containment_first(
            self, tmp_path):
        store = FilesystemArtifactStore(root=str(tmp_path / "art"))
        payload = b"x" * (artifacts_mod._MAX_ARTIFACT_BYTES + 1)
        with pytest.raises(ValueError, match="key escapes root"):
            store.put("../escape", payload)


def _write_source(tmp_path, payload: bytes):
    source = tmp_path / "source.bin"
    source.write_bytes(payload)
    return source