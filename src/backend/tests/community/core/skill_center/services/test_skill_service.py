"""Tests for agentclaw.community.core.services.skill_service.SkillService."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentclaw.community.core.skill_center.services.skill_service import SkillService


# ── helpers ──────────────────────────────────────────────────────────


def _lenient_skill_repo_sync():
    """Stub SkillRepoSyncPlugin whose get_scan_target mirrors the
    LocalSkillRepoSyncPlugin contract: return the default_fallback
    unchanged. Most sync tests don't care about the scan-target
    strictness — only that the service can resolve *something*."""
    stub = MagicMock()
    stub.get_scan_target.side_effect = lambda default: default
    return stub


def _make_skill_dir(base: Path, rel_path: str, *, skill_md_content: str = "") -> Path:
    """Create a skill directory with a SKILL.md inside *base*."""
    d = base / rel_path
    d.mkdir(parents=True, exist_ok=True)
    content = skill_md_content or (
        "---\n"
        f"name: {d.name}\n"
        "description: A test skill\n"
        "category: general\n"
        "---\n"
    )
    (d / "SKILL.md").write_text(content, encoding="utf-8")
    return d


# ── TestSkillServiceLinkName ─────────────────────────────────────────


class TestSkillServiceLinkName:
    """get_link_name / get_relative_path_from_link_name"""

    def test_get_link_name_simple(self):
        assert SkillService.get_link_name("infra/demo/skill") == "infra_demo_skill"

    def test_get_link_name_strip_slashes(self):
        assert SkillService.get_link_name("/infra/demo/") == "infra_demo"

    def test_get_link_name_single(self):
        assert SkillService.get_link_name("my-skill") == "my-skill"

    def test_get_relative_path_from_link_name(self):
        assert SkillService.get_relative_path_from_link_name("infra_demo_skill") == "infra/demo/skill"


# ── TestSkillServiceActivation ───────────────────────────────────────


class TestSkillServiceActivation:
    """activate_skill / deactivate_skill"""

    @pytest.mark.asyncio
    async def test_activate_git_skill(self, skill_dirs, mock_skill_repo):
        repo_dir = skill_dirs["repo_dir"]
        _make_skill_dir(repo_dir, "infra/demo")

        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=repo_dir,
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        result = await svc.activate_skill("git://infra/demo")
        # R2 改造后, activate_skill 不再 pathlib 本地建链;
        # 软链建立由 device_sync → adapter bindpath 单方面负责。
        # 这里只断言返回成功标记, 不再检查本地文件系统副作用。
        assert result is True

    @pytest.mark.asyncio
    async def test_activate_reserved_name_rejected(self, skill_dirs, mock_skill_repo):
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        result = await svc.activate_skill("git://skills-repo")
        assert result is False

    @pytest.mark.asyncio
    async def test_deactivate_skill(self, skill_dirs, mock_skill_repo):
        repo_dir = skill_dirs["repo_dir"]
        _make_skill_dir(repo_dir, "infra/demo")

        mock_device_fs = MagicMock()
        mock_device_fs.delete_tree = AsyncMock(return_value=True)

        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=repo_dir,
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=lambda b, u: mock_device_fs,
            git_sync_service_factory=MagicMock(),
        )
        # R2: activate_skill 不再本地建链, 要测 deactivate, 得手工模拟
        # bindpath 已建好的软链（线上是 adapter 建, 这里测 backend 删的路径）。
        link = skill_dirs["active_dir"] / "infra_demo"
        link.symlink_to(Path("skills-repo/infra/demo"))

        result = await svc.deactivate_skill("infra_demo", user_id="u-1", bolt_id="bolt-1")
        assert result is True
        # _delete_active_entry 走 device_fs.delete_tree (mock 直接返回 True), 不真删本地文件,
        # 所以这里只断言"路由到了 device_fs.delete_tree", 不再断言本地链消失。
        mock_device_fs.delete_tree.assert_awaited()

    @pytest.mark.asyncio
    async def test_deactivate_reserved_name_rejected(self, skill_dirs, mock_skill_repo):
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        result = await svc.deactivate_skill("skills-repo")
        assert result is False

    @pytest.mark.asyncio
    async def test_deactivate_nonexistent(self, skill_dirs, mock_skill_repo):
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        result = await svc.deactivate_skill("does-not-exist")
        assert result is True


# ── TestSkillServiceSync ─────────────────────────────────────────────


class TestSkillServiceSync:
    """sync_skills_from_git"""

    def test_sync_creates_new_skills(self, skill_dirs, mock_skill_repo):
        repo_dir = skill_dirs["repo_dir"]
        _make_skill_dir(repo_dir, "infra/demo")
        _make_skill_dir(repo_dir, "code/tool")

        mock_skill_repo.list_skills.return_value = []

        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=repo_dir,
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        result = svc.sync_skills_from_git()
        assert result["created"] == 2
        assert result["failed"] == 0

    def test_sync_deletes_removed_skills(self, skill_dirs, mock_skill_repo):
        repo_dir = skill_dirs["repo_dir"]
        # Only one skill in repo
        _make_skill_dir(repo_dir, "infra/demo")

        # But DB has two
        mock_skill_repo.list_skills.return_value = [
            {"id": "1", "git_path": "git://infra/demo", "name": "demo"},
            {"id": "2", "git_path": "git://old/removed", "name": "removed"},
        ]

        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=repo_dir,
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        result = svc.sync_skills_from_git()
        assert result["deleted"] == 1
        mock_skill_repo.delete.assert_called_once_with("2")

    def test_sync_updates_git_renamed_skill_instead_of_delete_create(
        self, skill_dirs, mock_skill_repo
    ):
        repo_dir = skill_dirs["repo_dir"]
        _make_skill_dir(
            repo_dir,
            "infra/demo",
            skill_md_content=(
                "---\n"
                "name: demo\n"
                "description: Same skill, new path\n"
                "---\n"
            ),
        )

        old_path = "git://legacy/demo"
        new_path = "git://infra/demo"
        mock_skill_repo.list_skills.return_value = [
            {
                "id": "42",
                "git_path": old_path,
                "name": "demo",
                "description": "old description",
                "category": "general",
                "tags": "[]",
                "link_name": "legacy_demo",
                "skill_uuid": "uuid-demo",
            },
        ]

        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=repo_dir,
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )

        result = svc.sync_skills_from_git(git_renames={old_path: new_path})

        assert result["created"] == 0
        assert result["deleted"] == 0
        assert result["updated"] == 1
        mock_skill_repo.create.assert_not_called()
        mock_skill_repo.delete.assert_not_called()
        mock_skill_repo.update.assert_called_once()
        assert mock_skill_repo.update.call_args.args[0] == "42"
        update_data = mock_skill_repo.update.call_args.args[1]
        assert update_data["git_path"] == new_path
        assert update_data["description"] == "Same skill, new path"

    def test_sync_git_rename_update_failure_does_not_delete_or_create(
        self, skill_dirs, mock_skill_repo
    ):
        repo_dir = skill_dirs["repo_dir"]
        _make_skill_dir(
            repo_dir,
            "infra/demo",
            skill_md_content=(
                "---\n"
                "name: demo\n"
                "description: Same skill, new path\n"
                "---\n"
            ),
        )

        old_path = "git://legacy/demo"
        new_path = "git://infra/demo"
        mock_skill_repo.list_skills.return_value = [
            {
                "id": "42",
                "git_path": old_path,
                "name": "demo",
                "description": "old description",
                "category": "general",
                "tags": "[]",
                "link_name": "legacy_demo",
                "skill_uuid": "uuid-demo",
            },
        ]
        mock_skill_repo.update.side_effect = RuntimeError("database down")

        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=repo_dir,
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )

        result = svc.sync_skills_from_git(git_renames={old_path: new_path})

        assert result["created"] == 0
        assert result["deleted"] == 0
        assert result["failed"] == 1
        assert result["errors"] == [
            "Failed to rename git://legacy/demo -> git://infra/demo: database down"
        ]
        mock_skill_repo.create.assert_not_called()
        mock_skill_repo.delete.assert_not_called()
        mock_skill_repo.update.assert_called_once()

    def test_sync_ignores_incomplete_git_rename_hint(
        self, skill_dirs, mock_skill_repo
    ):
        repo_dir = skill_dirs["repo_dir"]
        _make_skill_dir(repo_dir, "infra/demo")

        mock_skill_repo.list_skills.return_value = []

        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=repo_dir,
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )

        result = svc.sync_skills_from_git(
            git_renames={"git://legacy/missing": "git://infra/demo"}
        )

        assert result["created"] == 1
        assert result["updated"] == 0
        assert result["deleted"] == 0
        mock_skill_repo.create.assert_called_once()
        mock_skill_repo.update.assert_not_called()
        mock_skill_repo.delete.assert_not_called()

    def test_sync_scans_git_sync_service_target_not_repo_dir(
        self, skill_dirs, mock_skill_repo, tmp_path
    ):
        """sync_skills_from_git must scan the local atomic repo from
        GitSyncService, NOT the (possibly mid-rsync) NAS repo_dir.

        Reproduces the 2026-05-13 17:38 race scenario in unit form:
        - repo_dir (would be NAS in prod) is empty — simulating rsync
          having deleted everything mid-write
        - GitSyncService.skills_target has the real skill content
        - DB already has 1 git skill matching the real content
        Expected: deleted=0, updated=0, skipped=1 (NOT deleted=1)
        """
        local_target = tmp_path / "aiworkbench" / "skills-repo"
        local_target.mkdir(parents=True)
        _make_skill_dir(local_target, "infra/demo")

        # repo_dir intentionally empty — represents rsync mid-write on NAS
        # (skill_dirs["repo_dir"] is an empty dir from the fixture)

        mock_skill_repo.list_skills.return_value = [
            {"id": "1", "git_path": "git://infra/demo", "name": "demo"},
        ]

        # Plugin returns the local atomic path regardless of fallback —
        # mimics ProdSkillRepoSyncPlugin pointing at GitSyncService.skills_target.
        sync_stub = MagicMock()
        sync_stub.get_scan_target.return_value = local_target

        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=sync_stub,
            category_repo=MagicMock(),
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],   # empty (would-be NAS mid-rsync)
            local_dir=skill_dirs["local_dir"],
        )
        result = svc.sync_skills_from_git()

        # If we scanned repo_dir (empty) we would have deleted=1. The fix
        # makes us scan local_target instead, where the skill exists.
        # get_by_git_path finds the existing row → updated=1 (name match
        # would give skipped=1; name mismatch gives updated=1).
        assert result["deleted"] == 0, (
            f"Expected deleted=0 (scanned local atomic repo), got "
            f"{result['deleted']}. Full result: {result}"
        )
        assert result["updated"] == 1, (
            f"Expected updated=1 (existing skill found and processed), got "
            f"{result}. Keys present: created={result.get('created')}, "
            f"updated={result.get('updated')}, deleted={result.get('deleted')}, "
            f"skipped={result.get('skipped')}"
        )
        mock_skill_repo.delete.assert_not_called()

    def test_sync_logs_each_create_update_and_delete(
        self, skill_dirs, mock_skill_repo, caplog
    ):
        """Each create/update/delete must emit a log line with the id+name."""
        repo_dir = skill_dirs["repo_dir"]
        # Git has: infra/demo (existing in DB → update via category=general
        # path), infra/new (not in DB → create)
        _make_skill_dir(
            repo_dir,
            "infra/demo",
            skill_md_content=(
                "---\n"
                "name: demo\n"
                "description: An updated description\n"
                "category: general\n"
                "---\n"
            ),
        )
        _make_skill_dir(repo_dir, "infra/new")

        # DB has: infra/demo (will update because description differs) and
        # infra/gone (will delete because not in git)
        mock_skill_repo.list_skills.return_value = [
            {"id": "10", "git_path": "git://infra/demo", "name": "demo",
             "description": "old description", "category": "general",
             "tags": "[]", "link_name": "infra_demo"},
            {"id": "20", "git_path": "git://infra/gone", "name": "gone",
             "description": "x", "category": "general",
             "tags": "[]", "link_name": "infra_gone"},
        ]
        mock_skill_repo.create.return_value = {"id": "99", "name": "new"}

        fake_git_sync_svc = MagicMock()
        fake_git_sync_svc.config.skills_target = repo_dir

        # Capture log calls directly since agentclaw logger uses
        # propagate=False (logs go to files, not root handler).
        import agentclaw.community.core.skill_center.services.skill_service as svc_module
        logged_messages = []

        original_info = svc_module.logger.info
        original_warning = svc_module.logger.warning

        def capture_info(msg, *args, **kwargs):
            if isinstance(msg, str):
                logged_messages.append(("INFO", msg % args if args else msg))
            return original_info(msg, *args, **kwargs)

        def capture_warning(msg, *args, **kwargs):
            if isinstance(msg, str):
                logged_messages.append(("WARNING", msg % args if args else msg))
            return original_warning(msg, *args, **kwargs)

        with patch.object(
            svc_module.logger, "info", capture_info
        ), patch.object(
            svc_module.logger, "warning", capture_warning
        ):
            svc = SkillService(
                skill_repo=mock_skill_repo,
                skill_repo_sync=_lenient_skill_repo_sync(),
                category_repo=MagicMock(),
                market_cache=MagicMock(),
                device_fs_factory=MagicMock(),
                git_sync_service_factory=lambda: fake_git_sync_svc,
                active_dir=skill_dirs["active_dir"],
                repo_dir=repo_dir,
                local_dir=skill_dirs["local_dir"],
            )
            result = svc.sync_skills_from_git()

        assert result["created"] == 1
        assert result["updated"] == 1
        assert result["deleted"] == 1

        messages = [msg for _, msg in logged_messages]
        assert any(
            "CREATE skill" in m and "git://infra/new" in m
            for m in messages
        ), f"Missing CREATE log. Logs: {messages}"
        assert any(
            "UPDATE skill" in m and "id=10" in m
            for m in messages
        ), f"Missing UPDATE log. Logs: {messages}"
        assert any(
            "DELETE orphan skill" in m and "id=20" in m and "git://infra/gone" in m
            for m in messages
        ), f"Missing DELETE log. Logs: {messages}"

    def test_sync_aborts_gracefully_when_scan_target_unavailable(
        self, skill_dirs, mock_skill_repo, tmp_path
    ):
        """If the SkillRepoSyncPlugin's get_scan_target raises (prod
        strict path: GitSyncService.skills_target missing),
        sync_skills_from_git must log error and return a results dict —
        NOT propagate the exception to the API layer.

        Also: it MUST NOT call _skill_repo.delete on anything.
        """
        # DB has a skill — if the abort doesn't work, it would be wrongly deleted
        mock_skill_repo.list_skills.return_value = [
            {"id": "1", "git_path": "git://infra/demo", "name": "demo"},
        ]

        # Plugin raises — mimics ProdSkillRepoSyncPlugin when
        # GitSyncService.config.skills_target is missing.
        strict_sync_stub = MagicMock()
        strict_sync_stub.get_scan_target.side_effect = RuntimeError(
            "GitSyncService.skills_target does not exist. Refusing to "
            "fall back to NAS path in cloud mode."
        )

        import agentclaw.community.core.skill_center.services.skill_service as svc_module
        logged_errors = []

        original_error = svc_module.logger.error

        def capture_error(msg, *args, **kwargs):
            if isinstance(msg, str):
                logged_errors.append(msg % args if args else msg)
            return original_error(msg, *args, **kwargs)

        with patch.object(svc_module.logger, "error", capture_error):
            svc = SkillService(
                skill_repo=mock_skill_repo,
                skill_repo_sync=strict_sync_stub,
                category_repo=MagicMock(),
                market_cache=MagicMock(),
                device_fs_factory=MagicMock(),
                git_sync_service_factory=MagicMock(),
                active_dir=skill_dirs["active_dir"],
                repo_dir=skill_dirs["repo_dir"],
                local_dir=skill_dirs["local_dir"],
            )
            result = svc.sync_skills_from_git()

        # No exception propagated, result is a dict
        assert isinstance(result, dict)
        # Zero mutations
        assert result["created"] == 0
        assert result["updated"] == 0
        assert result["deleted"] == 0
        # Error recorded
        assert result["errors"], "Expected errors list to be populated"
        assert any("does not exist" in e or "Refusing to fall back" in e
                   for e in result["errors"])
        # Critically: nothing was deleted
        mock_skill_repo.delete.assert_not_called()
        # Error logged
        assert any(
            "Aborted" in msg for msg in logged_errors
        ), f"Expected 'Aborted' in logged errors, got: {logged_errors}"


# ── TestSkillServiceGetActiveSkills ──────────────────────────────────


class TestSkillServiceGetActiveSkills:
    """get_active_skills"""

    def test_empty_active_dir(self, skill_dirs, mock_skill_repo):
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
        market_cache=MagicMock(),
        device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        assert svc.get_active_skills() == []

    @pytest.mark.asyncio
    async def test_returns_activated_skills(self, skill_dirs, mock_skill_repo):
        repo_dir = skill_dirs["repo_dir"]
        _make_skill_dir(repo_dir, "infra/demo")

        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=repo_dir,
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        await svc.activate_skill("git://infra/demo")
        # R2: activate_skill 不再本地建链, 软链由 device_sync → adapter bindpath 创建。
        # 单测里手工模拟 bindpath 已经建好链, 然后跑 get_active_skills 扫描。
        link = skill_dirs["active_dir"] / "infra_demo"
        link.symlink_to(Path("skills-repo/infra/demo"))

        skills = svc.get_active_skills()
        assert len(skills) == 1
        assert skills[0].name == "demo"
        assert skills[0].is_active is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "active_root",
        [
            "/home/admin/.openclaw/workspace/skills",
            "/home/admin/.claude/skills",
            "/home/admin/.hermes/skills",
        ],
    )
    async def test_reads_desktop_active_skills_from_device_runtime(
        self, skill_dirs, mock_skill_repo, active_root
    ):
        device_fs = MagicMock()
        device_fs.list_dir = AsyncMock(
            return_value=[
                {
                    "name": "skills-repo",
                    "path": f"{active_root}/skills-repo",
                    "is_dir": True,
                },
                {
                    "name": "mcporter",
                    "path": f"{active_root}/mcporter",
                    "is_dir": True,
                },
                {
                    "name": "../escape",
                    "path": f"{active_root}/../escape",
                    "is_dir": True,
                },
                {
                    "name": "sp455-r2-oc-probe",
                    "path": f"{active_root}/sp455-r2-oc-probe",
                    "is_dir": True,
                },
                {
                    "name": "empty-metadata",
                    "path": f"{active_root}/empty-metadata",
                    "is_dir": True,
                },
            ]
        )

        async def _read_file(path, **_kwargs):
            if path.endswith("sp455-r2-oc-probe/SKILL.md"):
                return (
                    "---\nname: 运行时探针\n"
                    "description: visible on the Desktop device\n---\n"
                ).encode("gbk")
            if path.endswith("empty-metadata/SKILL.md"):
                return b""
            return None

        mock_skill_repo.get_by_link_name.return_value = {
            "git_path": (
                "local:///home/admin/runtime-pool/"
                "skills-local/sp455-r2-oc-probe"
            )
        }

        device_fs.exists = AsyncMock(
            side_effect=lambda path: path.endswith(
                (
                    "sp455-r2-oc-probe/SKILL.md",
                    "empty-metadata/SKILL.md",
                )
            )
        )
        device_fs.read_file = AsyncMock(side_effect=_read_file)
        device_fs_factory = MagicMock(return_value=device_fs)
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=device_fs_factory,
            git_sync_service_factory=MagicMock(),
        )

        skills = await svc.get_active_skills_from_device(
            bot_id="desktop_bot_1",
            owner_id="405935",
            active_dir=Path(active_root),
        )

        assert [skill.id for skill in skills] == ["sp455-r2-oc-probe"]
        assert skills[0].name == "运行时探针"
        assert skills[0].description == "visible on the Desktop device"
        assert skills[0].path == f"{active_root}/sp455-r2-oc-probe"
        assert skills[0].source_path == (
            "/home/admin/runtime-pool/skills-local/sp455-r2-oc-probe"
        )
        assert skills[0].is_active is True
        device_fs_factory.assert_called_once_with("desktop_bot_1", "405935")
        device_fs.list_dir.assert_awaited_once_with(
            active_root, recursive=False
        )
        assert all("escape" not in call.args[0] for call in device_fs.read_file.await_args_list)

    @pytest.mark.asyncio
    async def test_device_active_list_propagates_runtime_listing_failure(
        self, skill_dirs, mock_skill_repo
    ):
        device_fs = MagicMock()
        device_fs.list_dir = AsyncMock(side_effect=RuntimeError("device offline"))
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=Path("/runtime/skills"),
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(return_value=device_fs),
            git_sync_service_factory=MagicMock(),
        )

        with pytest.raises(RuntimeError, match="device offline"):
            await svc.get_active_skills_from_device(
                bot_id="desktop_bot_1", owner_id="405935"
            )

    @pytest.mark.asyncio
    async def test_device_active_list_skips_entries_without_skill_metadata(
        self, skill_dirs, mock_skill_repo
    ):
        active_root = "/home/admin/.claude/skills"
        device_fs = MagicMock()
        device_fs.list_dir = AsyncMock(
            return_value=[
                {
                    "name": "mcporter",
                    "path": f"{active_root}/mcporter",
                    "is_dir": True,
                },
                {
                    "name": "direct-skill",
                    "path": f"{active_root}/direct-skill",
                    "is_dir": True,
                },
            ]
        )
        device_fs.exists = AsyncMock(
            side_effect=lambda path: path.endswith("direct-skill/SKILL.md")
        )

        async def _read_file(path, **_kwargs):
            if path.endswith("direct-skill/SKILL.md"):
                return b"---\nname: direct-skill\n---\n"
            raise AssertionError(f"must not read missing metadata: {path}")

        device_fs.read_file = AsyncMock(side_effect=_read_file)
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=Path(active_root),
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(return_value=device_fs),
            git_sync_service_factory=MagicMock(),
        )

        skills = await svc.get_active_skills_from_device(
            bot_id="desktop_bot_1", owner_id="405935"
        )

        assert [skill.id for skill in skills] == ["direct-skill"]
        device_fs.read_file.assert_awaited_once_with(
            f"{active_root}/direct-skill/SKILL.md"
        )

    @pytest.mark.asyncio
    async def test_missing_device_active_root_is_empty(
        self, skill_dirs, mock_skill_repo
    ):
        device_fs = MagicMock()
        device_fs.list_dir = AsyncMock(return_value=None)
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=Path("/runtime/skills"),
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(return_value=device_fs),
            git_sync_service_factory=MagicMock(),
        )

        assert await svc.get_active_skills_from_device(
            bot_id="desktop_bot_1", owner_id="405935"
        ) == []


# ── TestSkillServiceMarketTree ───────────────────────────────────────


class TestSkillServiceMarketTree:
    """get_market_tree / _build_tree_node"""

    def test_market_tree_empty_repo(self, skill_dirs, mock_skill_repo):
        market_cache = MagicMock()
        market_cache.get.return_value = None  # force cache miss
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
            market_cache=market_cache,
        device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        svc._market_cache.invalidate()
        tree = svc.get_market_tree()
        assert tree == []

    def test_market_tree_with_skills(self, skill_dirs, mock_skill_repo):
        repo_dir = skill_dirs["repo_dir"]
        _make_skill_dir(repo_dir, "infra/demo")

        market_cache = MagicMock()
        market_cache.get.return_value = None  # force cache miss
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=repo_dir,
            local_dir=skill_dirs["local_dir"],
            market_cache=market_cache,
        device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        svc._market_cache.invalidate()
        tree = svc.get_market_tree()
        assert len(tree) == 1
        assert tree[0]["name"] == "infra"
        assert tree[0]["type"] == "dir"
        assert len(tree[0]["children"]) == 1
        assert tree[0]["children"][0]["type"] == "skill"

    def test_build_tree_node(self, skill_dirs, mock_skill_repo):
        repo_dir = skill_dirs["repo_dir"]
        _make_skill_dir(repo_dir, "code/my-tool")

        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=repo_dir,
            local_dir=skill_dirs["local_dir"],
        market_cache=MagicMock(),
        device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        node = svc._build_tree_node(repo_dir / "code")
        assert node is not None
        assert node.type == "dir"
        assert len(node.children) == 1
        assert node.children[0].type == "skill"


# ── TestSkillServiceParseSkillPath ───────────────────────────────────


class TestSkillServiceParseSkillPath:
    """parse_skill_path"""

    def test_git_protocol(self, skill_dirs, mock_skill_repo):
        repo_dir = skill_dirs["repo_dir"]
        _make_skill_dir(repo_dir, "infra/demo")

        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=repo_dir,
            local_dir=skill_dirs["local_dir"],
        market_cache=MagicMock(),
        device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        protocol, path = svc.parse_skill_path("git://infra/demo")
        assert protocol == "git"
        assert path == repo_dir / "infra/demo"

    def test_local_protocol(self, skill_dirs, mock_skill_repo):
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
        market_cache=MagicMock(),
        device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        protocol, path = svc.parse_skill_path("local:///tmp/skills-local/my-skill")
        assert protocol == "local"
        assert path == Path("/tmp/skills-local/my-skill")

    def test_local_teclaw_logical_accepted(self, skill_dirs, mock_skill_repo):
        """teclaw stores the minimal logical form local://skills-local/<name>."""
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        protocol, path = svc.parse_skill_path("local://skills-local/my-skill")
        assert protocol == "local"
        assert path == Path("skills-local/my-skill")

    def test_empty_raises(self, skill_dirs, mock_skill_repo):
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
        market_cache=MagicMock(),
        device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        with pytest.raises(ValueError):
            svc.parse_skill_path("")

    def test_local_relative_raises(self, skill_dirs, mock_skill_repo):
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
        market_cache=MagicMock(),
        device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        with pytest.raises(ValueError):
            svc.parse_skill_path("local://relative/path")

    def test_legacy_format(self, skill_dirs, mock_skill_repo):
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
        market_cache=MagicMock(),
        device_fs_factory=MagicMock(),
            git_sync_service_factory=MagicMock(),
        )
        protocol, path = svc.parse_skill_path("infra/demo")
        assert protocol == "git"
        assert path == skill_dirs["repo_dir"] / "infra/demo"


# ── TestResolveScanTarget ─────────────────────────────────────────────


class TestResolveScanTarget:
    """After Rule 14 / site #7, _resolve_sync_scan_target is a thin
    delegate to SkillRepoSyncPlugin.get_scan_target. Strictness rules
    are tested at the plugin layer; here we only verify delegation.
    """

    def test_delegates_to_skill_repo_sync_with_market_repo_fallback(
        self, skill_dirs, mock_skill_repo, tmp_path
    ):
        expected = tmp_path / "from-plugin"
        plugin = MagicMock()
        plugin.get_scan_target.return_value = expected

        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=plugin,
            category_repo=MagicMock(),
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=lambda: MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
        )
        resolved = svc._resolve_sync_scan_target()

        assert resolved == expected
        # Fallback passed in is whatever _get_market_repo_dir would return.
        plugin.get_scan_target.assert_called_once_with(svc._get_market_repo_dir())

    def test_propagates_plugin_runtime_error(
        self, skill_dirs, mock_skill_repo
    ):
        """If the plugin (prod impl) raises — strict mode — the service
        propagates the error so the sync caller can skip this round."""
        plugin = MagicMock()
        plugin.get_scan_target.side_effect = RuntimeError("scan target missing")

        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=plugin,
            category_repo=MagicMock(),
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=lambda: MagicMock(),
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
        )
        with pytest.raises(RuntimeError, match="scan target missing"):
            svc._resolve_sync_scan_target()


# ── TestSyncRepoWithLock ─────────────────────────────────────────────


class TestSyncRepoWithLock:
    """sync_repo_with_lock — 委托 GitSyncService 并把底层结果归一化。

    回归点：底层 GitSyncService.sync() 在锁被占用时返回
    ``{"success": False, "error": "Distributed lock held"}``。
    service 必须透传 ``error`` 键，否则路由层 (sync_market) 无法区分
    "锁被占用"（应返回友好提示）与真实失败（应 500），会把锁占用
    错误地当成 500 + ``{"detail": "Distributed lock held"}`` 抛出。
    """

    def _make_svc(self, skill_dirs, mock_skill_repo, sync_result):
        fake_git_sync = MagicMock()
        fake_git_sync.sync = AsyncMock(return_value=sync_result)
        return SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=_lenient_skill_repo_sync(),
            category_repo=MagicMock(),
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=lambda: fake_git_sync,
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
        )

    def test_lock_held_propagates_error_key(self, skill_dirs, mock_skill_repo):
        """锁被占用：error 键必须原样透传，供路由层做友好提示分支判断。"""
        svc = self._make_svc(
            skill_dirs,
            mock_skill_repo,
            {"success": False, "error": "Distributed lock held"},
        )

        result = svc.sync_repo_with_lock()

        assert result["success"] is False
        assert result["synced"] is False
        # 关键断言：error 键存在且原样透传（修复前该键缺失，路由 get("error") 拿到 None）
        assert result["error"] == "Distributed lock held"

    def test_global_sync_lock_held_propagates_error_key(self, skill_dirs, mock_skill_repo):
        """另一把锁（进程内 GlobalSyncLock）占用时，error 也必须透传。

        路由层把 GlobalSyncLock held 和 Distributed lock held 一并视为
        "锁占用 → 友好提示"，前提是 service 把两种 error 都透传上来。
        """
        svc = self._make_svc(
            skill_dirs,
            mock_skill_repo,
            {"success": False, "error": "GlobalSyncLock held"},
        )

        result = svc.sync_repo_with_lock()

        assert result["success"] is False
        assert result["error"] == "GlobalSyncLock held"

    def test_success_has_none_error(self, skill_dirs, mock_skill_repo):
        """成功路径：error 为 None，路由不会误判为锁占用。"""
        svc = self._make_svc(
            skill_dirs,
            mock_skill_repo,
            {"success": True, "subtrees": {"skills": {"updated": True}}},
        )

        result = svc.sync_repo_with_lock()

        assert result["success"] is True
        assert result["synced"] is True
        assert result["error"] is None
        assert result["message"] == "Sync completed"

    def test_local_root_uses_skill_repo_sync_without_git_sync(
        self,
        skill_dirs,
        mock_skill_repo,
        tmp_path,
    ):
        """singlebox/local: 有 host skills root 时不应构造依赖 secret 的 GitSyncService。"""
        skill_repo_sync = MagicMock()
        skill_repo_sync.get_local_skills_root.return_value = tmp_path / "skills"
        skill_repo_sync.sync = AsyncMock(
            return_value={
                "success": True,
                "fetch": True,
                "subtrees": {"skills": {"updated": True}},
                "error": None,
            }
        )
        git_sync_factory = MagicMock(
            side_effect=AssertionError("GitSyncService should not be resolved")
        )
        svc = SkillService(
            skill_repo=mock_skill_repo,
            skill_repo_sync=skill_repo_sync,
            category_repo=MagicMock(),
            market_cache=MagicMock(),
            device_fs_factory=MagicMock(),
            git_sync_service_factory=git_sync_factory,
            active_dir=skill_dirs["active_dir"],
            repo_dir=skill_dirs["repo_dir"],
            local_dir=skill_dirs["local_dir"],
        )

        result = svc.sync_repo_with_lock()

        assert result["success"] is True
        assert result["synced"] is True
        assert result["fetch"] is True
        assert result["error"] is None
        skill_repo_sync.sync.assert_awaited_once()
        git_sync_factory.assert_not_called()

    def test_generic_failure_propagates_error_key(self, skill_dirs, mock_skill_repo):
        """非锁占用的真实失败：error 同样透传（路由据此 500），message 兜底。"""
        svc = self._make_svc(
            skill_dirs,
            mock_skill_repo,
            {"success": False, "error": "Git fetch failed: boom"},
        )

        result = svc.sync_repo_with_lock()

        assert result["success"] is False
        assert result["error"] == "Git fetch failed: boom"
        assert result["message"] == "Git fetch failed: boom"
