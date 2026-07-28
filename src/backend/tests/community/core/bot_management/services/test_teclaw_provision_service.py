"""Tests for TeclawProvisionService — eager teclaw container provisioning."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.bot_management.services.teclaw_provision_service import (
    TeclawProvisionResult,
    TeclawProvisionService,
)
from agentclaw.community.core.service_bot.services.deploy.producer import DeployArtifact


_BOT = {
    "bot_id": "b1",
    "bot_name": "Helper",
    "bot_desc": "desc",
    "entity_id": "staff-1",
    "entity_type": "staff",
    "active_engine": "teclaw",
}


def _make_service(
    *, create_result=None, binding_id: int = 77
) -> tuple[
    TeclawProvisionService,
    MagicMock,
    MagicMock,
    MagicMock,
    MagicMock,
    MagicMock,
]:
    baas = MagicMock()
    baas.create_teclaw_bot.return_value = (
        create_result
        if create_result is not None
        else {"bot_uuid": "BOT-x", "publish_id": 9}
    )
    router = MagicMock()
    router.resolve.return_value.produce_artifact.return_value = DeployArtifact(
        success=True, ext={"config_artifact": {"schema_version": 2, "skills": []}}
    )
    binding_repo = MagicMock()
    binding_repo.insert_binding.return_value = binding_id
    task_queue_service = MagicMock()
    bot_repo = MagicMock()
    bot_repo.update_by_owner.return_value = {**_BOT}
    svc = TeclawProvisionService(
        baas_service=baas,
        deploy_artifact_producer_router=router,
        device_binding_repo=binding_repo,
        task_queue_service=task_queue_service,
        bot_repository=bot_repo,
        teclaw_template_uuid="teclaw-tpl",
    )
    return svc, baas, router, binding_repo, task_queue_service, bot_repo


@pytest.mark.unit
@pytest.mark.parametrize(
    "engine,expected",
    [
        ("teclaw", True),
        ("TeClaw", True),
        ("openclaw", False),
        ("", False),
        (None, False),
    ],
)
def test_is_teclaw(engine, expected) -> None:
    svc, *_ = _make_service()
    assert svc.is_teclaw(engine) is expected


@pytest.mark.unit
def test_is_teclaw_engine_set_is_injectable() -> None:
    svc = TeclawProvisionService(
        baas_service=MagicMock(),
        deploy_artifact_producer_router=MagicMock(),
        device_binding_repo=MagicMock(),
        task_queue_service=MagicMock(),
        bot_repository=MagicMock(),
        teclaw_template_uuid="t",
        teclaw_engine_types={"foreign"},
    )
    assert svc.is_teclaw("foreign") is True
    assert svc.is_teclaw("teclaw") is False


@pytest.mark.unit
def test_provision_produces_creates_approves_and_binds() -> None:
    svc, baas, router, binding_repo, task_queue_service, _ = _make_service()

    def insert_binding(**_kwargs) -> int:
        task_queue_service.enqueue.assert_not_called()
        return 77

    binding_repo.insert_binding.side_effect = insert_binding

    with patch(
        "agentclaw.community.core.bot_management.services.teclaw_provision_service.time.time",
        return_value=123.0,
    ):
        result = svc.provision(bot=_BOT, owner_id="u1")

    # 1. deploy artifact produced via the teclaw producer (draft -> version None);
    #    owner_id spliced into the bot row for the producer's compose request.
    router.resolve.assert_called_once_with("teclaw")
    pa = router.resolve.return_value.produce_artifact.call_args
    assert pa.args[0]["bot_id"] == "b1" and pa.args[0]["owner_id"] == "u1"
    assert pa.kwargs["version"] is None

    # 2. created with the produced artifact + teclaw template; token 由创建发布单
    #    轮询任务在容器启动后写入 outbound rule，不混入 create payload。
    ck = baas.create_teclaw_bot.call_args
    assert ck.kwargs["config_artifact"] == {"schema_version": 2, "skills": []}
    assert ck.kwargs["template_uuid"] == "teclaw-tpl"
    assert "agent_pass_token" not in ck.kwargs

    # 3. approved (create + approve), same request_id as create
    assert baas.approve_publish.call_args.kwargs["publish_id"] == 9
    assert (
        baas.approve_publish.call_args.kwargs["request_id"] == ck.kwargs["request_id"]
    )

    # 4. binding recorded with device_id = bot_uuid, provider teclaw, PENDING,
    #    and the publish_id stashed in device_props as the status read handle
    bk = binding_repo.insert_binding.call_args.kwargs
    assert bk["device_id"] == "BOT-x"
    assert bk["device_provider"] == "teclaw"
    assert bk["status"] == "PENDING"
    assert bk["device_props"] == {
        "publish_id": 9,
        "bot_uuid": "BOT-x",
        "bolt_id": "b1",
        "entity_id": "u1",
    }

    # 5. Durable publish polling is enqueued after the binding is persisted.
    task_queue_service.enqueue.assert_called_once_with(
        "teclaw.create.publish_poll",
        {
            "binding_id": 77,
            "bot_id": "b1",
            "owner_id": "u1",
            "publish_id": 9,
            "started_at_epoch_s": 123.0,
        },
        deadline_seconds=86400,
    )

    assert result == TeclawProvisionResult(
        binding_id=77,
        device_id="BOT-x",
        status="PENDING",
        # the delivered initial artifact is carried out for create_bot to record
        config_artifact={"schema_version": 2, "skills": []},
    )


@pytest.mark.unit
def test_provision_does_not_push_agent_pass_rule_before_container_starts() -> None:
    # BaaS only mints the container's PaaS device while it executes the create
    # publish, so a push here would write to nothing. The create publish poll
    # task owns the push (see test_teclaw_publish_task_handler).
    svc, baas, *_ = _make_service()

    svc.provision(bot=_BOT, owner_id="u1")

    assert "agent_pass_token" not in baas.create_teclaw_bot.call_args.kwargs
    baas.update_teclaw_outbound_rule_by_bot_uuid.assert_not_called()


@pytest.mark.unit
def test_enqueue_failure_marks_bot_and_binding_failed_without_destroying_remote():
    svc, baas, _, binding_repo, task_queue_service, bot_repo = _make_service()
    task_queue_service.enqueue.side_effect = RuntimeError("queue down")

    result = svc.provision(bot=_BOT, owner_id="u1")

    assert result.status == "FAILED"
    bot_repo.update_by_owner.assert_called_once_with("b1", "u1", {"status": "FAILED"})
    binding_repo.update_status.assert_called_once_with(binding_id=77, status="FAILED")
    baas.destroy_bot.assert_not_called()
    baas.update_teclaw_outbound_rule_by_bot_uuid.assert_not_called()


@pytest.mark.unit
def test_enqueue_failure_propagates_bot_write_failure() -> None:
    svc, _, _, binding_repo, task_queue_service, bot_repo = _make_service()
    task_queue_service.enqueue.side_effect = RuntimeError("queue down")
    bot_repo.update_by_owner.side_effect = RuntimeError("bot db down")

    with pytest.raises(RuntimeError, match="bot db down"):
        svc.provision(bot=_BOT, owner_id="u1")

    binding_repo.update_status.assert_not_called()


@pytest.mark.unit
def test_enqueue_failure_propagates_unmatched_bot_update_without_updating_binding():
    svc, _, _, binding_repo, task_queue_service, bot_repo = _make_service()
    task_queue_service.enqueue.side_effect = RuntimeError("queue down")
    bot_repo.update_by_owner.return_value = None

    with pytest.raises(RuntimeError, match="bot status update matched no record"):
        svc.provision(bot=_BOT, owner_id="u1")

    binding_repo.update_status.assert_not_called()


@pytest.mark.unit
def test_enqueue_failure_propagates_binding_write_failure() -> None:
    svc, _, _, binding_repo, task_queue_service, _ = _make_service()
    task_queue_service.enqueue.side_effect = RuntimeError("queue down")
    binding_repo.update_status.side_effect = RuntimeError("binding db down")

    with pytest.raises(RuntimeError, match="binding db down"):
        svc.provision(bot=_BOT, owner_id="u1")


@pytest.mark.unit
def test_provision_raises_and_rolls_back_when_no_publish_id() -> None:
    from agentclaw.community.core.service_bot.services.baas_service import (
        BaasServiceError,
    )

    # A container was minted (bot_uuid) but BaaS returned no publish workflow —
    # without publish_id we can't approve/start it, so fail and roll back.
    svc, baas, _, binding_repo, _, _ = _make_service(
        create_result={"bot_uuid": "BOT-x"}
    )
    with pytest.raises(BaasServiceError, match="publish_id"):
        svc.provision(bot=_BOT, owner_id="u1")
    baas.approve_publish.assert_not_called()
    baas.destroy_bot.assert_called_once()
    assert baas.destroy_bot.call_args.kwargs["bot_uuid"] == "BOT-x"
    binding_repo.insert_binding.assert_not_called()


@pytest.mark.unit
def test_provision_raises_when_no_bot_uuid() -> None:
    from agentclaw.community.core.service_bot.services.baas_service import (
        BaasServiceError,
    )

    svc, baas, _, binding_repo, _, _ = _make_service(create_result={"publish_id": 9})
    with pytest.raises(BaasServiceError):
        svc.provision(bot=_BOT, owner_id="u1")
    # Nothing was minted (no bot_uuid) — no compensating destroy, no binding.
    baas.destroy_bot.assert_not_called()
    binding_repo.insert_binding.assert_not_called()


@pytest.mark.unit
def test_request_id_is_deterministic_32_hex() -> None:
    svc, baas, _, _, _, _ = _make_service()
    svc.provision(bot=_BOT, owner_id="u1")
    rid = baas.create_teclaw_bot.call_args.kwargs["request_id"]
    assert len(rid) == 32 and all(c in "0123456789abcdef" for c in rid)


@pytest.mark.unit
def test_approve_failure_compensating_destroys_and_reraises() -> None:
    from agentclaw.community.core.service_bot.services.baas_service import (
        BaasServiceError,
    )

    svc, baas, _, binding_repo, _, _ = _make_service()
    baas.approve_publish.side_effect = BaasServiceError("approve boom")

    with pytest.raises(BaasServiceError, match="approve boom"):
        svc.provision(bot=_BOT, owner_id="u1")

    # Orphaned BaaS bot is destroyed; no binding recorded.
    baas.destroy_bot.assert_called_once()
    assert baas.destroy_bot.call_args.kwargs["bot_uuid"] == "BOT-x"
    binding_repo.insert_binding.assert_not_called()


@pytest.mark.unit
def test_binding_failure_compensating_destroys_and_reraises() -> None:
    svc, baas, _, binding_repo, task_queue_service, _ = _make_service()
    binding_repo.insert_binding.side_effect = RuntimeError("db down")

    with pytest.raises(RuntimeError, match="db down"):
        svc.provision(bot=_BOT, owner_id="u1")

    baas.destroy_bot.assert_called_once()
    assert baas.destroy_bot.call_args.kwargs["bot_uuid"] == "BOT-x"
    # Provision failed before binding — no durable poll was enqueued.
    task_queue_service.enqueue.assert_not_called()


@pytest.mark.unit
def test_compensating_destroy_failure_does_not_mask_original_error() -> None:
    from agentclaw.community.core.service_bot.services.baas_service import (
        BaasServiceError,
    )

    svc, baas, _, _, _, _ = _make_service()
    baas.approve_publish.side_effect = BaasServiceError("approve boom")
    baas.destroy_bot.side_effect = BaasServiceError("destroy also boom")

    # The original (approve) error wins; the destroy failure is swallowed.
    with pytest.raises(BaasServiceError, match="approve boom"):
        svc.provision(bot=_BOT, owner_id="u1")


@pytest.mark.unit
def test_compensating_destroy_uses_distinct_request_id() -> None:
    svc, baas, _, _, _, _ = _make_service()
    baas.approve_publish.side_effect = RuntimeError("x")
    try:
        svc.provision(bot=_BOT, owner_id="u1")
    except RuntimeError:
        pass
    create_rid = baas.create_teclaw_bot.call_args.kwargs["request_id"]
    destroy_rid = baas.destroy_bot.call_args.kwargs["request_id"]
    assert create_rid != destroy_rid


# NOTE: status is persisted onto the stored column by the durable publish-poll
# task and read from the DB directly. The publish → bot/binding status mapping is
# unit-tested in test_teclaw_publish_task_handler (map_publish_status).
