from __future__ import annotations

import json
import os
import tarfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from engine.community.config import RepoDelivery
from engine.community.core.skills.layout_planner import (
    LAYOUT_CONTRACT_VERSION,
    LayoutIdentity,
    RuntimeLayoutContext,
    resolve_filesystem_skill_layout,
)
from engine.community.plugins.skills_pool import desktop_preparation
from engine.community.core.skills import skills_repo_download
from engine.community.plugins.skills_pool.desktop_preparation import (
    DesktopPreparationStatus,
    prepare_desktop_pool,
)
from engine.community.plugins.skills_pool.layout_probe import (
    RuntimeLayoutInspectionStatus,
    inspect_runtime_layout,
)
from engine.community.plugins.skills_pool.layout_activation import (
    PoolActivationStatus,
    SkillMapping,
    activate_claude_code_pool,
    activate_hermes_pool,
    activate_openclaw_pool,
)

FILESYSTEM_ENGINES = ("openclaw", "claude_code", "aicoding", "hermes")


def _target(path: Path) -> Path:
    target = path.readlink()
    if not target.is_absolute():
        target = path.parent / target
    return Path(os.path.abspath(target))


def _layout(home: Path, engine: str):
    return resolve_filesystem_skill_layout(
        LayoutIdentity(
            engine_type=engine,
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
        ),
        RuntimeLayoutContext(home=home),
    )


def _legacy_runtime(home: Path, engine: str):
    layout = _layout(home, engine)
    (layout.legacy_local / "registered").mkdir(parents=True)
    (layout.legacy_local / "registered" / "SKILL.md").write_text("registered")
    (layout.legacy_local / "handmade").mkdir()
    (layout.legacy_local / "handmade" / "SKILL.md").write_text("handmade")
    repo_source = home / ".openclaw/workspace/skills/skills-repo"
    (repo_source / "business/reviewer").mkdir(parents=True)
    (repo_source / "business/reviewer/SKILL.md").write_text("repo")

    layout.active_root.mkdir(parents=True, exist_ok=True)
    if layout.local_bridge != layout.legacy_local:
        layout.local_bridge.symlink_to(
            layout.legacy_local,
            target_is_directory=True,
        )
    if layout.legacy_repo != repo_source:
        layout.legacy_repo.parent.mkdir(parents=True, exist_ok=True)
        layout.legacy_repo.symlink_to(repo_source, target_is_directory=True)
    if (
        layout.repo_bridge != layout.legacy_repo
        and not layout.repo_bridge.exists()
        and not layout.repo_bridge.is_symlink()
    ):
        layout.repo_bridge.symlink_to(
            layout.legacy_repo,
            target_is_directory=True,
        )
    managed = layout.active_root / "registered"
    managed.symlink_to(
        layout.legacy_local / "registered",
        target_is_directory=True,
    )
    external_source = home / "external-skill"
    external_source.mkdir()
    external = layout.active_root / "external"
    external.symlink_to(external_source, target_is_directory=True)
    return layout, repo_source, managed, external


def _fresh_legacy_runtime(home: Path, engine: str):
    layout = _layout(home, engine)
    repo_source = home / ".openclaw/workspace/skills/skills-repo"
    repo_source.mkdir(parents=True)

    layout.active_root.mkdir(parents=True, exist_ok=True)
    if layout.local_bridge != layout.legacy_local:
        layout.local_bridge.symlink_to(
            layout.legacy_local,
            target_is_directory=True,
        )
    if layout.legacy_repo != repo_source:
        layout.legacy_repo.parent.mkdir(parents=True, exist_ok=True)
        layout.legacy_repo.symlink_to(repo_source, target_is_directory=True)
    if (
        layout.repo_bridge != layout.legacy_repo
        and not layout.repo_bridge.exists()
        and not layout.repo_bridge.is_symlink()
    ):
        layout.repo_bridge.symlink_to(
            layout.legacy_repo,
            target_is_directory=True,
        )
    return layout, repo_source


@pytest.mark.parametrize("engine", FILESYSTEM_ENGINES)
def test_preparation_creates_empty_legacy_local_for_fresh_desktop_runtime(
    tmp_path: Path,
    engine: str,
) -> None:
    home = tmp_path / "home/admin"
    layout, repo_source = _fresh_legacy_runtime(home, engine)
    assert not layout.legacy_local.exists()
    assert not layout.legacy_local.is_symlink()

    result = prepare_desktop_pool(
        engine=engine,
        repo_source=repo_source,
        home=home,
    )

    assert result.status is DesktopPreparationStatus.PREPARED
    assert layout.legacy_local.is_dir()
    assert not layout.legacy_local.is_symlink()
    assert layout.pool_local.is_dir()
    probe = inspect_runtime_layout(
        engine=engine,
        home=home,
        repo_delivery=RepoDelivery.DOWNLOAD,
    )
    assert probe.status is RuntimeLayoutInspectionStatus.READY
    assert probe.preparation_id == result.preparation_id


def test_preparation_does_not_replace_missing_legacy_local_symlink(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home/admin"
    layout, repo_source = _fresh_legacy_runtime(home, "openclaw")
    wrong = home / "wrong-local"
    layout.legacy_local.symlink_to(wrong, target_is_directory=True)

    result = prepare_desktop_pool(
        engine="openclaw",
        repo_source=repo_source,
        home=home,
    )

    assert result.status is DesktopPreparationStatus.FAILED
    assert layout.legacy_local.is_symlink()
    assert _target(layout.legacy_local) == wrong
    assert not wrong.exists()
    assert not layout.ready_marker.exists()


@pytest.mark.parametrize("engine", FILESYSTEM_ENGINES)
def test_preparation_reuses_downloaded_repo_and_preserves_legacy_layout(
    tmp_path: Path,
    engine: str,
) -> None:
    home = tmp_path / "home/admin"
    layout, repo_source, managed, external = _legacy_runtime(home, engine)
    managed_before = _target(managed)
    external_before = _target(external)

    result = prepare_desktop_pool(
        engine=engine,
        repo_source=repo_source,
        home=home,
    )

    assert result.status is DesktopPreparationStatus.PREPARED
    assert result.preparation_id is not None
    assert (layout.pool_local / "registered/SKILL.md").read_text() == "registered"
    assert (layout.pool_local / "handmade/SKILL.md").read_text() == "handmade"
    assert layout.pool_repo.is_dir()
    assert not layout.pool_repo.is_symlink()
    assert (layout.pool_repo / "business/reviewer/SKILL.md").read_text() == "repo"
    assert repo_source.is_symlink()
    assert _target(repo_source) == layout.pool_repo
    assert _target(managed) == managed_before
    assert _target(external) == external_before
    assert layout.legacy_local.is_dir() and not layout.legacy_local.is_symlink()
    assert layout.legacy_repo.is_symlink()
    assert _target(layout.legacy_repo) == layout.pool_repo
    if engine == "claude_code":
        assert layout.repo_bridge.is_symlink()
        assert _target(layout.repo_bridge) == layout.legacy_repo

    marker = json.loads(layout.ready_marker.read_text())
    assert marker["repo_delivery"] == "download"
    assert marker["repo_delivery_source"] == str(layout.pool_repo)
    probe = inspect_runtime_layout(
        engine=engine,
        home=home,
        repo_delivery=RepoDelivery.DOWNLOAD,
    )
    assert probe.status is RuntimeLayoutInspectionStatus.READY
    assert probe.preparation_id == result.preparation_id

    repeated = prepare_desktop_pool(
        engine=engine,
        repo_source=repo_source,
        home=home,
    )
    assert repeated.status is DesktopPreparationStatus.ALREADY_PREPARED
    assert repeated.preparation_id == result.preparation_id


@pytest.mark.parametrize(
    ("engine", "activate"),
    [
        ("openclaw", activate_openclaw_pool),
        ("claude_code", activate_claude_code_pool),
        ("hermes", activate_hermes_pool),
    ],
)
def test_downloaded_repo_cutover_leaves_corpus_outside_active_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    engine: str,
    activate,
) -> None:
    home = tmp_path / "home/admin"
    layout, repo_source, _managed, external = _legacy_runtime(home, engine)
    prepared = prepare_desktop_pool(
        engine=engine,
        repo_source=repo_source,
        home=home,
    )
    assert prepared.status is DesktopPreparationStatus.PREPARED
    monkeypatch.setenv("MAC_CONTAINER", "true")
    mapping = SkillMapping(
        source=str(layout.pool_repo / "business/reviewer"),
        target=str(layout.active_root / "reviewer"),
    )

    activated = activate(
        migration_generation="generation-1",
        preparation_id=prepared.preparation_id,
        registered_local_names=["registered"],
        mappings=[mapping],
        home=home,
    )

    assert activated.status is PoolActivationStatus.COMMITTED
    assert layout.pool_repo.is_dir() and not layout.pool_repo.is_symlink()
    assert (layout.active_root / "reviewer").is_symlink()
    assert _target(layout.active_root / "reviewer") == (
        layout.pool_repo / "business/reviewer"
    )
    assert external.is_symlink()
    if engine in {"openclaw", "claude_code"}:
        assert not layout.repo_bridge.exists()
        assert not layout.repo_bridge.is_symlink()
    else:
        assert layout.repo_bridge.is_symlink()
        assert _target(layout.repo_bridge) == layout.pool_repo

    refreshed = tmp_path / f"{engine}-repo-refresh"
    (refreshed / "business/reviewer").mkdir(parents=True)
    (refreshed / "business/reviewer/SKILL.md").write_text("refreshed")
    archive = tmp_path / f"{engine}-repo-refresh.tar.gz"
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(refreshed, arcname="skills-repo")
    monkeypatch.setattr(skills_repo_download, "TARGET_DIR", layout.pool_repo)
    monkeypatch.setattr(
        skills_repo_download,
        "BACKUP_DIR",
        layout.pool_root / ".skills-repo-backups",
    )

    assert skills_repo_download._extract_atomic(archive)
    assert (layout.active_root / "reviewer/SKILL.md").read_text() == ("refreshed")


@pytest.mark.parametrize("engine", ("teclaw", "unknown"))
def test_non_filesystem_engine_has_no_preparation_side_effect(
    tmp_path: Path,
    engine: str,
) -> None:
    home = tmp_path / "home/admin"
    repo_source = tmp_path / "repo"
    repo_source.mkdir()

    result = prepare_desktop_pool(
        engine=engine,
        repo_source=repo_source,
        home=home,
    )

    assert result.status is DesktopPreparationStatus.NOT_APPLICABLE
    assert not home.exists()


def test_invalid_existing_local_bridge_never_publishes_ready_marker(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home/admin"
    layout, repo_source, _managed, _external = _legacy_runtime(
        home,
        "claude_code",
    )
    layout.local_bridge.unlink()
    wrong = home / "wrong-local"
    wrong.mkdir()
    layout.local_bridge.symlink_to(wrong, target_is_directory=True)

    result = prepare_desktop_pool(
        engine="claude_code",
        repo_source=repo_source,
        home=home,
    )

    assert result.status is DesktopPreparationStatus.FAILED
    assert not layout.ready_marker.exists()
    assert _target(layout.local_bridge) == wrong


def test_preparation_preserves_pool_only_local_content(tmp_path: Path) -> None:
    home = tmp_path / "home/admin"
    layout, repo_source, _managed, _external = _legacy_runtime(
        home,
        "openclaw",
    )
    pool_only = layout.pool_local / "pool-only"
    pool_only.mkdir(parents=True)
    (pool_only / "SKILL.md").write_text("preserve")

    result = prepare_desktop_pool(
        engine="openclaw",
        repo_source=repo_source,
        home=home,
    )

    assert result.status is DesktopPreparationStatus.PREPARED
    assert (pool_only / "SKILL.md").read_text() == "preserve"


def test_concurrent_startup_preparation_converges_without_shared_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home/admin"
    layout, repo_source, _managed, _external = _legacy_runtime(
        home,
        "openclaw",
    )
    barrier = Barrier(2)
    original_mirror = desktop_preparation.mirror_local_tree

    def synchronized_mirror(**kwargs):
        barrier.wait()
        return original_mirror(**kwargs)

    monkeypatch.setattr(
        desktop_preparation,
        "mirror_local_tree",
        synchronized_mirror,
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: prepare_desktop_pool(
                    engine="openclaw",
                    repo_source=repo_source,
                    home=home,
                ),
                range(2),
            )
        )

    assert {result.status for result in results} <= {
        DesktopPreparationStatus.PREPARED,
        DesktopPreparationStatus.ALREADY_PREPARED,
    }
    assert layout.ready_marker.is_file()
    assert not list(layout.pool_root.glob(".preparation-staging-*"))
    probe = inspect_runtime_layout(
        engine="openclaw",
        home=home,
        repo_delivery=RepoDelivery.DOWNLOAD,
    )
    assert probe.status is RuntimeLayoutInspectionStatus.READY


def test_invalid_existing_claude_repo_bridge_never_publishes_ready_marker(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home/admin"
    layout, repo_source, _managed, _external = _legacy_runtime(
        home,
        "claude_code",
    )
    layout.repo_bridge.unlink()
    wrong = home / "wrong-repo"
    wrong.mkdir()
    layout.repo_bridge.symlink_to(wrong, target_is_directory=True)

    result = prepare_desktop_pool(
        engine="claude_code",
        repo_source=repo_source,
        home=home,
    )

    assert result.status is DesktopPreparationStatus.FAILED
    assert not layout.ready_marker.exists()
    assert _target(layout.repo_bridge) == wrong


@pytest.mark.parametrize(
    "engine",
    ("claude_code", "aicoding", "hermes"),
)
def test_wrong_legacy_repo_delivery_never_publishes_ready_marker(
    tmp_path: Path,
    engine: str,
) -> None:
    home = tmp_path / "home/admin"
    layout, repo_source, _managed, _external = _legacy_runtime(home, engine)
    layout.legacy_repo.unlink()
    wrong = home / "wrong-repo-delivery"
    wrong.mkdir()
    layout.legacy_repo.symlink_to(wrong, target_is_directory=True)

    result = prepare_desktop_pool(
        engine=engine,
        repo_source=repo_source,
        home=home,
    )

    assert result.status is DesktopPreparationStatus.FAILED
    assert not layout.ready_marker.exists()
    assert _target(layout.legacy_repo) == wrong


def test_two_independent_repo_corpora_fail_without_selecting_either(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home/admin"
    layout, repo_source = _fresh_legacy_runtime(home, "openclaw")
    (repo_source / "legacy.txt").write_text("legacy")
    layout.pool_repo.mkdir(parents=True)
    (layout.pool_repo / "pool.txt").write_text("pool")

    result = prepare_desktop_pool(
        engine="openclaw",
        repo_source=repo_source,
        home=home,
    )

    assert result.status is DesktopPreparationStatus.FAILED
    assert (repo_source / "legacy.txt").read_text() == "legacy"
    assert (layout.pool_repo / "pool.txt").read_text() == "pool"
    assert not layout.ready_marker.exists()


def test_repo_bridge_publish_failure_restores_legacy_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home/admin"
    layout, repo_source = _fresh_legacy_runtime(home, "openclaw")
    (repo_source / "legacy.txt").write_text("legacy")

    def fail_bridge(path: Path, source: Path) -> None:
        raise OSError("injected bridge publish failure")

    monkeypatch.setattr(
        desktop_preparation,
        "_publish_repo_delivery_bridge",
        fail_bridge,
    )
    result = prepare_desktop_pool(
        engine="openclaw",
        repo_source=repo_source,
        home=home,
    )

    assert result.status is DesktopPreparationStatus.FAILED
    assert repo_source.is_dir() and not repo_source.is_symlink()
    assert (repo_source / "legacy.txt").read_text() == "legacy"
    assert not layout.pool_repo.exists()
    assert not layout.ready_marker.exists()
