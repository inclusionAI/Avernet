



from pathlib import Path


from unittest.mock import call

import pytest












from agentclaw.community.core.skill_center.errors import (
    LocalSkillNotFoundError,
    SkillRuntimeNameConflictError,
    SkillSetControlPlaneConflictError,
)

from agentclaw.community.core.skill_center.services.runtime_layout_probe import (
    LAYOUT_CONTRACT_VERSION,
    RuntimeLayoutProbeResult,
    RuntimeLayoutProbeStatus,
)

from agentclaw.community.core.skill_center.services.skill_parser import SkillInfo



from tests.community.api.skill_center._skills_api_fixtures import mock_ctx as mock_ctx, _skill_service_di_app
class TestActiveSkillsRuntimeRead:
    @pytest.mark.parametrize(
        ("engine_type", "runtime_active_root"),
        [
            ("openclaw", "/home/admin/.openclaw/workspace/skills"),
            ("claude_code", "/home/admin/.claude/skills"),
            ("aicoding", "/home/admin/.claude/skills"),
            ("hermes", "/home/admin/.hermes/skills"),
        ],
    )
    def test_desktop_active_list_uses_engine_resolved_active_root(
        self, mock_ctx, engine_type, runtime_active_root
    ):
        with _skill_service_di_app(
            mock_ctx,
            bot_type="desktop",
            engine_type=engine_type,
        ) as (client, mock_svc, _):
            response = client.get("/api/skills/active/list")

            assert response.status_code == 200, response.text
            assert mock_svc.active_dir == Path("/backend/legacy/skills")
            mock_svc.get_active_skills_from_device.assert_awaited_once_with(
                bot_id=mock_ctx.bot_id,
                owner_id=mock_ctx.user_id,
                active_dir=Path(runtime_active_root),
            )

    def test_desktop_active_list_reads_the_live_device(self, mock_ctx):
        with _skill_service_di_app(mock_ctx, bot_type="desktop") as (
            client,
            mock_svc,
            _,
        ):
            mock_svc.get_active_skills_from_device.return_value = [
                SkillInfo(
                    id="sp455-r2-oc-probe",
                    name="sp455-r2-oc-probe",
                    path=("/home/admin/.openclaw/workspace/skills/sp455-r2-oc-probe"),
                    is_active=True,
                    is_installed=True,
                )
            ]

            response = client.get("/api/skills/active/list")

            assert response.status_code == 200, response.text
            assert response.json()["count"] == 1
            assert response.json()["data"][0]["id"] == "sp455-r2-oc-probe"
            mock_svc.get_active_skills_from_device.assert_awaited_once_with(
                bot_id=mock_ctx.bot_id,
                owner_id=mock_ctx.user_id,
                active_dir=Path("/home/admin/.openclaw/workspace/skills"),
            )
            mock_svc.get_active_skills.assert_not_called()

    def test_non_desktop_active_list_keeps_management_filesystem_scan(self, mock_ctx):
        with _skill_service_di_app(mock_ctx) as (client, mock_svc, _):
            mock_svc.get_active_skills.return_value = []

            response = client.get("/api/skills/active/list")

            assert response.status_code == 200, response.text
            mock_svc.get_active_skills.assert_called_once_with()
            mock_svc.get_active_skills_from_device.assert_not_awaited()

    def test_desktop_active_list_preserves_old_image_fallback(self, mock_ctx):
        fallback_root = Path("/backend/legacy/skills")
        old_image_probe = RuntimeLayoutProbeResult(
            status=RuntimeLayoutProbeStatus.NOT_CAPABLE,
            engine="claude_code",
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            preparation_id=None,
            evidence={"reason": "runtime_layout_probe_endpoint_absent"},
        )
        with _skill_service_di_app(
            mock_ctx,
            bot_type="desktop",
            engine_type="claude_code",
            runtime_probe_result=old_image_probe,
            management_active_root=fallback_root,
        ) as (client, mock_svc, _):
            response = client.get("/api/skills/active/list")

            assert response.status_code == 200, response.text
            assert mock_svc.active_dir == fallback_root
            mock_svc.get_active_skills_from_device.assert_awaited_once_with(
                bot_id=mock_ctx.bot_id,
                owner_id=mock_ctx.user_id,
                active_dir=fallback_root,
            )

    def test_desktop_teclaw_keeps_artifact_layout_without_probe(self, mock_ctx):
        management_root = Path("/backend/teclaw/skills")
        with _skill_service_di_app(
            mock_ctx,
            bot_type="desktop",
            engine_type="teclaw",
            management_active_root=management_root,
        ) as (client, mock_svc, _):
            response = client.get("/api/skills/active/list")

            assert response.status_code == 200, response.text
            assert mock_svc.active_dir == management_root
            mock_svc.get_active_skills_from_device.assert_awaited_once_with(
                bot_id=mock_ctx.bot_id,
                owner_id=mock_ctx.user_id,
                active_dir=management_root,
            )
            mock_svc.runtime_layout_probe.probe_bot.assert_not_awaited()

    def test_desktop_active_list_does_not_fallback_when_runtime_is_unbound(
        self, mock_ctx
    ):
        unbound_probe = RuntimeLayoutProbeResult(
            status=RuntimeLayoutProbeStatus.NOT_CAPABLE,
            engine="claude_code",
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            preparation_id=None,
            evidence={"reason": "current_runtime_not_bound"},
        )
        with _skill_service_di_app(
            mock_ctx,
            bot_type="desktop",
            engine_type="claude_code",
            runtime_probe_result=unbound_probe,
        ) as (client, mock_svc, _):
            response = client.get("/api/skills/active/list")

            assert response.status_code == 500
            assert mock_svc.active_dir is None
            mock_svc.get_active_skills_from_device.assert_not_awaited()

    def test_desktop_active_list_fails_closed_on_invalid_resolved_root(self, mock_ctx):
        invalid_probe = RuntimeLayoutProbeResult(
            status=RuntimeLayoutProbeStatus.READY,
            engine="claude_code",
            layout_contract_version=LAYOUT_CONTRACT_VERSION,
            preparation_id="prep-test",
            evidence={"resolved_layout": {"active_root": "relative/skills"}},
        )
        with _skill_service_di_app(
            mock_ctx,
            bot_type="desktop",
            engine_type="claude_code",
            runtime_probe_result=invalid_probe,
        ) as (client, mock_svc, _):
            response = client.get("/api/skills/active/list")

            assert response.status_code == 500
            assert mock_svc.active_dir is None
            mock_svc.get_active_skills_from_device.assert_not_awaited()

    def test_desktop_active_list_does_not_mask_device_failure_as_empty(self, mock_ctx):
        with _skill_service_di_app(mock_ctx, bot_type="desktop") as (
            client,
            mock_svc,
            _,
        ):
            mock_svc.get_active_skills_from_device.side_effect = RuntimeError(
                "device offline"
            )

            response = client.get("/api/skills/active/list")

            assert response.status_code == 500
            mock_svc.get_active_skills.assert_not_called()

class TestActivateSkillsBatchAsyncAwait:
    """The legacy batch wire delegates every item to Direct control plane."""

    def test_activate_skills_batch_awaits_service_method(self, mock_ctx):
        with _skill_service_di_app(mock_ctx) as (client, mock_svc, _):
            client.post(
                "/api/skills/market/activate-batch",
                json={"skill_paths": ["git://path/a", "git://path/b"]},
            )
            assert mock_svc.asset_service.activate_skill.await_count == 2
            assert mock_svc.activate_skills_batch.await_count == 0

    def test_activate_skills_batch_passes_correct_skill_paths(self, mock_ctx):
        """skill_paths must propagate; user_id/bolt_id must be kwargs."""
        with _skill_service_di_app(mock_ctx) as (client, mock_svc, _):
            client.post(
                "/api/skills/market/activate-batch",
                json={"skill_paths": ["git://path/a", "git://path/b"]},
            )
            assert mock_svc.asset_service.resolve_legacy_skill_id.call_args_list == [
                call(
                    skill_reference="git://path/a",
                    source_path="git://path/a",
                    bot_id="default",
                    owner_id=mock_ctx.user_id,
                    user_id=mock_ctx.user_id,
                ),
                call(
                    skill_reference="git://path/b",
                    source_path="git://path/b",
                    bot_id="default",
                    owner_id=mock_ctx.user_id,
                    user_id=mock_ctx.user_id,
                ),
            ]
            assert mock_svc.asset_service.activate_skill.await_args_list == [
                call(
                    skill_id="1",
                    bot_id="default",
                    owner_id=mock_ctx.user_id,
                    actor_id=mock_ctx.user_id,
                ),
                call(
                    skill_id="2",
                    bot_id="default",
                    owner_id=mock_ctx.user_id,
                    actor_id=mock_ctx.user_id,
                ),
            ]

    def test_batch_keeps_missing_item_as_partial_success(self, mock_ctx):
        with _skill_service_di_app(mock_ctx) as (client, mock_svc, _):

            def _resolve(**kwargs):
                if kwargs["skill_reference"] == "git://path/b":
                    raise LocalSkillNotFoundError()
                return "1"

            mock_svc.asset_service.resolve_legacy_skill_id.side_effect = _resolve

            response = client.post(
                "/api/skills/market/activate-batch",
                json={"skill_paths": ["git://path/a", "git://path/b"]},
            )

            assert response.status_code == 200, response.text
            assert len(response.json()["data"]["success"]) == 1
            assert response.json()["data"]["failed"][0]["path"] == "git://path/b"
            mock_svc.asset_service.activate_skill.assert_awaited_once()

    def test_batch_keeps_refused_items_as_partial_success(self, mock_ctx):
        """R1 refusals and the one-name invariant fail one item, never the batch.

        The Direct control plane surfaces both as raises; the historical batch
        wire promises the success/failed lists for exactly these outcomes.
        """
        with _skill_service_di_app(mock_ctx) as (client, mock_svc, _):
            ids = {"git://path/a": "1", "git://path/b": "2", "git://path/c": "3"}
            mock_svc.asset_service.resolve_legacy_skill_id.side_effect = (
                lambda **kwargs: ids[kwargs["skill_reference"]]
            )

            async def _activate(**kwargs):
                if kwargs["skill_id"] == "2":
                    raise SkillSetControlPlaneConflictError(
                        "RESOURCE_MANAGED_BY_SKILL_SET"
                    )
                if kwargs["skill_id"] == "3":
                    raise SkillRuntimeNameConflictError()
                return {
                    "id": kwargs["skill_id"],
                    "name": "path-a",
                    "git_path": "git://path/a",
                }

            mock_svc.asset_service.activate_skill.side_effect = _activate

            response = client.post(
                "/api/skills/market/activate-batch",
                json={
                    "skill_paths": [
                        "git://path/a",
                        "git://path/b",
                        "git://path/c",
                    ]
                },
            )

            assert response.status_code == 200, response.text
            data = response.json()["data"]
            assert [item["id"] for item in data["success"]] == ["1"]
            assert [item["path"] for item in data["failed"]] == [
                "git://path/b",
                "git://path/c",
            ]
            assert data["failed"][0]["error"] == "RESOURCE_MANAGED_BY_SKILL_SET"

    def test_activate_skills_batch_fails_when_runtime_mapping_sync_fails(
        self, mock_ctx
    ):
        with _skill_service_di_app(
            mock_ctx,
            device_sync_result={"success": False, "message": "source missing"},
            runtime_uses_pool_paths=True,
        ) as (client, mock_svc, _):
            response = client.post(
                "/api/skills/market/activate-batch",
                json={"skill_paths": ["git://path/a"]},
            )

            assert response.status_code == 502
            assert response.json()["detail"] == (
                "Failed to synchronize activated skills to runtime"
            )
            mock_svc.asset_service.activate_skill.assert_awaited_once()
