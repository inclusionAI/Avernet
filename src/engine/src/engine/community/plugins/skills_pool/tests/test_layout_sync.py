from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from engine.community.plugins.skills_pool.layout_sync import (
    merge_post_cutover_changes,
    mirror_local_tree,
)


def _temporary_entries(root: Path) -> list[str]:
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.name.startswith(".nfs")
        or path.name.endswith(".pool-sync")
        or path.name.startswith(".post-sync-")
        or path.name.startswith(".final-sync-")
    )


def test_mirror_does_not_leave_publish_temporaries_in_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "legacy-local"
    source_file = source_root / "ask-matt" / "SKILL.md"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("legacy-window", encoding="utf-8")
    pool_root = tmp_path / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_local.mkdir(parents=True)
    staging_root = pool_root / ".final-sync-generation-1"
    real_unlink = Path.unlink

    def simulate_persistent_storage_unlink(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        if pool_local in path.parents and path.name.endswith(".pool-sync"):
            os.rename(path, path.with_name(".nfs-simulated"))
            return
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", simulate_persistent_storage_unlink)

    names, manifest = mirror_local_tree(
        source_root=source_root,
        pool_local=pool_local,
        staging_root=staging_root,
    )

    assert names == ["ask-matt"]
    assert manifest["ask-matt/SKILL.md"][0] == "file"
    assert (pool_local / "ask-matt" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "legacy-window"
    assert _temporary_entries(pool_root) == []


def test_new_pool_file_does_not_share_inode_with_quarantine_source(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "quarantine" / "skills-local"
    source_file = source_root / "ask-matt" / "late.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("legacy-window", encoding="utf-8")
    pool_root = tmp_path / "skills-pool"
    pool_local = pool_root / "skills-local"
    (pool_local / "ask-matt").mkdir(parents=True)

    result = merge_post_cutover_changes(
        source_root=source_root,
        pool_local=pool_local,
        baseline={"ask-matt": ("dir", "")},
        publish_root=pool_root / ".post-sync-generation-1",
    )

    target = pool_local / "ask-matt" / "late.txt"
    source_file.write_text("late-source-write", encoding="utf-8")
    assert result == {
        "applied": ["ask-matt/late.txt"],
        "conflicts_preserved_in_pool": [],
    }
    assert target.read_text(encoding="utf-8") == "legacy-window"
    assert target.stat().st_ino != source_file.stat().st_ino
    assert _temporary_entries(pool_root) == []


def test_merge_rejects_unowned_publish_staging_without_deleting_it(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "quarantine" / "skills-local"
    source_file = source_root / "ask-matt" / "late.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("legacy-window", encoding="utf-8")
    pool_root = tmp_path / "skills-pool"
    pool_local = pool_root / "skills-local"
    (pool_local / "ask-matt").mkdir(parents=True)
    publish_root = pool_root / ".post-sync-generation-1"
    publish_root.mkdir()
    sentinel = publish_root / "user-owned.txt"
    sentinel.write_text("do-not-delete", encoding="utf-8")

    with pytest.raises(OSError):
        merge_post_cutover_changes(
            source_root=source_root,
            pool_local=pool_local,
            baseline={"ask-matt": ("dir", "")},
            publish_root=publish_root,
        )

    assert sentinel.read_text(encoding="utf-8") == "do-not-delete"
    assert not (pool_local / "ask-matt" / "late.txt").exists()


def test_merge_replays_owned_staging_left_before_publish(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "quarantine" / "skills-local"
    source_file = source_root / "ask-matt" / "late.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("legacy-window", encoding="utf-8")
    pool_root = tmp_path / "skills-pool"
    pool_local = pool_root / "skills-local"
    (pool_local / "ask-matt").mkdir(parents=True)
    publish_root = pool_root / ".post-sync-generation-1"
    publish_root.mkdir()
    (publish_root / ".owner.json").write_text(
        json.dumps(
            {
                "schema": "skills-pool-layout-sync-publish.v1",
                "pool_local": str(pool_local),
                "publish_root": publish_root.name,
            }
        ),
        encoding="utf-8",
    )
    (publish_root / ".late.crashed.pool-sync").write_text(
        "partial-candidate",
        encoding="utf-8",
    )

    result = merge_post_cutover_changes(
        source_root=source_root,
        pool_local=pool_local,
        baseline={"ask-matt": ("dir", "")},
        publish_root=publish_root,
    )

    assert result["applied"] == ["ask-matt/late.txt"]
    assert (pool_local / "ask-matt" / "late.txt").read_text() == "legacy-window"
    assert not publish_root.exists()


def test_no_delta_merge_removes_owned_staging_without_recreating_it(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "quarantine" / "skills-local"
    source_root.mkdir(parents=True)
    pool_root = tmp_path / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_local.mkdir(parents=True)
    publish_root = pool_root / ".post-sync-generation-1"
    publish_root.mkdir()
    owner = {
        "schema": "skills-pool-layout-sync-publish.v1",
        "pool_local": str(pool_local),
        "publish_root": publish_root.name,
    }
    (publish_root / ".owner.json").write_text(
        json.dumps(owner),
        encoding="utf-8",
    )
    owner_bytes = json.dumps(
        owner,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    claim_root = publish_root.with_name(
        f"{publish_root.name}.owner-{hashlib.sha256(owner_bytes).hexdigest()[:16]}"
    )
    claim_root.mkdir()

    result = merge_post_cutover_changes(
        source_root=source_root,
        pool_local=pool_local,
        baseline={},
        publish_root=publish_root,
    )

    assert result == {
        "applied": [],
        "conflicts_preserved_in_pool": [],
    }
    assert not publish_root.exists()
    assert not claim_root.exists()


def test_no_delta_merge_preserves_unowned_publish_staging(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "quarantine" / "skills-local"
    source_root.mkdir(parents=True)
    pool_root = tmp_path / "skills-pool"
    pool_local = pool_root / "skills-local"
    pool_local.mkdir(parents=True)
    publish_root = pool_root / ".post-sync-generation-1"
    publish_root.mkdir()
    sentinel = publish_root / "user-owned.txt"
    sentinel.write_text("do-not-delete", encoding="utf-8")

    with pytest.raises(OSError):
        merge_post_cutover_changes(
            source_root=source_root,
            pool_local=pool_local,
            baseline={},
            publish_root=publish_root,
        )

    assert sentinel.read_text(encoding="utf-8") == "do-not-delete"


def test_merge_replays_owner_claim_left_before_marker_write(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "quarantine" / "skills-local"
    source_file = source_root / "ask-matt" / "late.txt"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("legacy-window", encoding="utf-8")
    pool_root = tmp_path / "skills-pool"
    pool_local = pool_root / "skills-local"
    (pool_local / "ask-matt").mkdir(parents=True)
    publish_root = pool_root / ".post-sync-generation-1"
    owner = {
        "schema": "skills-pool-layout-sync-publish.v1",
        "pool_local": str(pool_local),
        "publish_root": publish_root.name,
    }
    owner_bytes = json.dumps(
        owner,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    owner_digest = hashlib.sha256(owner_bytes).hexdigest()[:16]
    claim_root = publish_root.with_name(
        f"{publish_root.name}.owner-{owner_digest}"
    )
    claim_root.mkdir()

    result = merge_post_cutover_changes(
        source_root=source_root,
        pool_local=pool_local,
        baseline={"ask-matt": ("dir", "")},
        publish_root=publish_root,
    )

    assert result["applied"] == ["ask-matt/late.txt"]
    assert (pool_local / "ask-matt" / "late.txt").read_text() == "legacy-window"
    assert not claim_root.exists()
    assert not publish_root.exists()
