from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord, PublishStatus
from agentclaw.community.core.service_bot.services.bot_build_service import BotBuildService, BotBuildServiceError
from agentclaw.community.core.service_bot.services.publish_flow_service import (
    PublishFlowService,
    PublishFlowServiceError,
)
from agentclaw.community.core.service_bot.services.bot_publish_service import (
    PublishNotFoundError,
    PublishStatusInvalidError,
)
from agentclaw.community.core.service_bot.services.publish_flow.tasks import (
    PROGRESS_POLL_TASK,
)
from agentclaw.community.core.service_bot.types import PublishStage


def _make_publish_record(**kwargs):
    data = dict(
        id=kwargs.get('id', 1),
        source_bot_pk=kwargs.get('source_bot_pk', 11),
        source_bot_id=kwargs.get('source_bot_id', 'bot-source'),
        publish_bot_id=kwargs.get('publish_bot_id', 'bot-pub-1'),
        name=kwargs.get('name', 'demo'),
        description=kwargs.get('description', 'desc'),
        owner_id=kwargs.get('owner_id', 'u1'),
        owner_name=kwargs.get('owner_name', 'user1'),
        status=kwargs.get('status', PublishStatus.VALIDATING.value),
        version=kwargs.get('version', 1),
        last_pub_id=kwargs.get('last_pub_id', 0),
        env=kwargs.get('env', 'dev'),
        ext=kwargs.get('ext', {}),
        permission_owner=kwargs.get('permission_owner', 'u1'),
        gmt_create=kwargs.get('gmt_create', datetime.now()),
        gmt_modified=kwargs.get('gmt_modified', datetime.now()),
    )
    return BotPublishRecord(**data)


def _pf(*args, **kw):
    """Construct PublishFlowService for tests, defaulting the DI-required teclaw
    promotion deps to Mocks (the arca/verify flow tests don't exercise them)."""
    kw.setdefault("common_config_service", Mock())
    kw.setdefault("task_queue_service", Mock())
    kw.setdefault("resolver", Mock())
    kw.setdefault("device_fs_dispatcher", Mock())
    kw.setdefault("teclaw_file_promotion", Mock())
    kw.setdefault("device_binding_repo", Mock())
    if "channel_overrides_reader" not in kw:
        # Default to "no channels for this stage" ({}), so promotion delivers the
        # base artifact with channels cleared — tests that care about channels pass
        # an explicit reader.
        reader = Mock()
        reader.overrides_for_stage.return_value = {}
        kw["channel_overrides_reader"] = reader
    return PublishFlowService(*args, **kw)


def _arca_router(build_service=None):
    """ARCA-only producer router (arca/baas → ArcaSnapshotProducer, default baas).

    Mirrors what the DI root assembles; lets tests construct PublishFlowService
    without re-stating the router each time.
    """
    from agentclaw.community.core.service_bot.services.deploy.arca_snapshot_producer import (
        ArcaSnapshotProducer,
    )
    from agentclaw.community.core.service_bot.services.deploy.producer import (
        DeployArtifactProducerRouter,
    )

    arca = ArcaSnapshotProducer(build_service or Mock())
    return DeployArtifactProducerRouter(
        providers={"arca": arca, "baas": arca}, default_provider_key="baas"
    )


def test_bot_build_service_upgrade_returns_structured_result_for_bot_not_found():
    baas_service = Mock()
    baas_service.upgrade_bot.side_effect = Exception(
        '{"detail":{"error_code":"BOT_NOT_FOUND","message":"Bot not found or already destroyed: BOT-xxx"}}'
    )
    svc = BotBuildService(
        device_service=Mock(),
        baas_service=baas_service,
        path_factory=Mock(),
        passport_plugin=Mock(),
        device_binding_repo=Mock(),
        sandbox_registry=Mock(),
        channel_service=Mock(),
        bot_repository=Mock(),
        common_whitelist_service=Mock(),
        baas_template_resolver=Mock(),
        teclaw_template_uuid="TEMPLATE-teclaw-placeholder",
    )

    result = svc.upgrade(
        bot_uuid='BOT-xxx',
        bot={'bot_id': 'b1'},
        user_id='u1',
        migration_path='/tmp/migration',
        device_count=1,
        publish_stage=PublishStage.ONLINE,
        version="1",
    )

    assert result['success'] is False
    assert result['error_code'] == 'BOT_NOT_FOUND'
    assert 'Bot not found' in result['message']
    assert result['bot_uuid'] == 'BOT-xxx'


def test_bot_build_service_upgrade_raises_for_other_errors():
    baas_service = Mock()
    baas_service.upgrade_bot.side_effect = Exception('boom')
    svc = BotBuildService(
        device_service=Mock(),
        baas_service=baas_service,
        path_factory=Mock(),
        passport_plugin=Mock(),
        device_binding_repo=Mock(),
        sandbox_registry=Mock(),
        channel_service=Mock(),
        bot_repository=Mock(),
        common_whitelist_service=Mock(),
        baas_template_resolver=Mock(),
        teclaw_template_uuid="TEMPLATE-teclaw-placeholder",
    )

    with pytest.raises(BotBuildServiceError, match='Bot upgrade failed: boom'):
        svc.upgrade(
            bot_uuid='BOT-xxx',
            bot={'bot_id': 'b1'},
            user_id='u1',
            migration_path='/tmp/migration',
            device_count=1,
            publish_stage=PublishStage.ONLINE,
            version="1",
        )


@pytest.mark.asyncio
async def test_execute_verify_upgrade_falls_back_to_first_release_on_bot_not_found():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    publish_record = _make_publish_record(status=PublishStatus.BUILT.value)
    build_service.upgrade_async = AsyncMock(return_value={
        'success': False,
        'error_code': 'BOT_NOT_FOUND',
        'message': 'Bot not found or already destroyed',
    })
    expected = Mock()
    svc._execute_verify_first_release = AsyncMock(return_value=expected)

    result = await svc._execute_verify_upgrade(
        publish_record=publish_record,
        operator='u1',
        migration_path='/tmp/migration',
        bot={'bot_id': 'b1'},
        bot_uuid='BOT-old',
        verify_binding_id=123,
    )

    assert result is expected
    svc._execute_verify_first_release.assert_awaited_once_with(
        publish_record=publish_record,
        operator='u1',
        migration_path='/tmp/migration',
        bot={'bot_id': 'b1'},
    )


@pytest.mark.asyncio
async def test_execute_upgrade_release_falls_back_to_first_release_on_bot_not_found():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    publish_record = _make_publish_record(status=PublishStatus.VALIDATING.value, last_pub_id=10)
    last_publish = _make_publish_record(id=10, status=PublishStatus.SUCCESS.value, ext={'binding': {'online': 88}})
    binding = Mock(device_id='BOT-old')

    publish_service.get_publish_by_id.return_value = last_publish
    publish_service.get_device_binding_by_id.return_value = binding
    build_service.upgrade_async = AsyncMock(return_value={
        'success': False,
        'error_code': 'BOT_NOT_FOUND',
        'message': 'Bot not found or already destroyed',
    })
    expected = Mock()
    svc._execute_first_release = AsyncMock(return_value=expected)

    result = await svc._execute_upgrade_release(
        publish_record=publish_record,
        operator='u1',
        migration_path='/tmp/migration',
        bot={'bot_id': 'b1'},
    )

    assert result is expected
    svc._execute_first_release.assert_awaited_once_with(
        publish_record=publish_record,
        operator='u1',
        migration_path='/tmp/migration',
        bot={'bot_id': 'b1'},
    )


def test_should_upgrade_online_requires_last_publish_success():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    publish_record = _make_publish_record(last_pub_id=10)
    publish_service.get_publish_by_id.return_value = _make_publish_record(
        id=10,
        status=PublishStatus.SUCCESS.value,
    )
    svc.get_publish_bot_status = Mock(return_value={'baas_bot_status': 'RUNNING'})
    assert svc._should_upgrade_online(publish_record) is True

    publish_service.get_publish_by_id.return_value = _make_publish_record(
        id=10,
        status=PublishStatus.SUCCESS.value,
    )
    svc.get_publish_bot_status = Mock(return_value={'baas_bot_status': 'RELEASED'})
    assert svc._should_upgrade_online(publish_record) is False

    publish_service.get_publish_by_id.return_value = _make_publish_record(
        id=10,
        status=PublishStatus.RELEASED.value,
    )
    assert svc._should_upgrade_online(publish_record) is False

    publish_service.get_publish_by_id.return_value = None
    assert svc._should_upgrade_online(publish_record) is False


def test_approve_baas_publish_returns_false_when_baas_raises():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    baas_service.approve_publish.side_effect = RuntimeError("down")
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    result = svc.approve_baas_publish(
        baas_publish_id=9,
        operator="op",
        stage=PublishStage.VERIFY,
        request_id="rid",
    )

    assert result is False


@pytest.mark.asyncio
async def test_execute_release_phase_falls_back_to_first_release_when_last_publish_not_success():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    publish_record = _make_publish_record(
        id=2,
        status=PublishStatus.VALIDATING.value,
        source_bot_id='bot-source',
        last_pub_id=10,
        ext={'migration_path': '/tmp/migration'},
    )
    publish_service.get_publish_by_id.return_value = _make_publish_record(
        id=10,
        status=PublishStatus.SUCCESS.value,
    )
    svc.get_publish_bot_status = Mock(return_value={'baas_bot_status': 'RELEASED'})

    # DI refactor replaced the module-level get_bot_service() shim with
    # the injected self._bot_service; set it directly instead of patching.
    bot_service = Mock()
    bot_service.get_bot.return_value = {'bot_id': 'bot-source'}
    svc._bot_service = bot_service
    svc._execute_first_release = AsyncMock(return_value='FIRST')
    svc._execute_upgrade_release = AsyncMock(return_value='UPGRADE')

    result = await svc.execute_release_phase(publish_record, operator='u1')

    assert result == 'FIRST'
    svc._execute_first_release.assert_awaited_once()
    svc._execute_upgrade_release.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_clears_retry_flag_when_restart_submit_fails():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    ext = {
        'source_status': PublishStatus.VALIDATE_PUB.value,
        'retry': False,
        'binding': {'verify': 123},
    }
    records = [
        _make_publish_record(status=PublishStatus.FAILED.value, ext=ext.copy()),
        _make_publish_record(status=PublishStatus.FAILED.value, ext=ext.copy()),
        _make_publish_record(status=PublishStatus.VALIDATE_PUB.value, ext={**ext, 'retry': True}),
    ]
    publish_service.get_publish_by_id.side_effect = records
    publish_service.update_publish_status_with_ext.return_value = _make_publish_record(
        status=PublishStatus.VALIDATE_PUB.value,
        ext={**ext, 'retry': True},
    )
    svc.restart_bot = Mock(return_value={'success': False, 'message': 'submit failed'})

    result = await svc.retry(publish_id=1, operator='u1')

    assert result.message == 'Retry failed: submit failed'
    rollback_ext = publish_service.update_publish_status_with_ext.call_args.kwargs['ext']
    assert rollback_ext['retry'] is True
    publish_service.update_publish_ext.assert_called_once()
    cleared_ext = publish_service.update_publish_ext.call_args.kwargs['ext']
    assert 'retry' not in cleared_ext
    assert cleared_ext['source_status'] == PublishStatus.VALIDATE_PUB.value


@pytest.mark.asyncio
async def test_retry_restart_enqueues_progress_poll_on_success():
    """Regression for #162 (secondary orphan): a successful BaaS-restart retry must
    enqueue the durable progress poll so the retried *_PUB record self-drives out of
    its wait state without user /sync (or /restart_status) polling."""
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    ext = {
        'source_status': PublishStatus.VALIDATE_PUB.value,
        'retry': False,
        'binding': {'verify': 123},
    }
    records = [
        _make_publish_record(status=PublishStatus.FAILED.value, ext=ext.copy()),
        _make_publish_record(status=PublishStatus.FAILED.value, ext=ext.copy()),
        _make_publish_record(status=PublishStatus.VALIDATE_PUB.value, ext={**ext, 'retry': True}),
    ]
    publish_service.get_publish_by_id.side_effect = records
    publish_service.update_publish_status_with_ext.return_value = _make_publish_record(
        status=PublishStatus.VALIDATE_PUB.value,
        ext={**ext, 'retry': True},
    )
    svc.restart_bot = Mock(return_value={'success': True, 'message': 'ok', 'stage': 'verify'})

    result = await svc.retry(publish_id=1, operator='u1')

    assert result.action == 'restart'
    # The poll was enqueued for this record; the retry flag was NOT cleared.
    svc._task_queue_service.enqueue.assert_called_once()
    args = svc._task_queue_service.enqueue.call_args.args
    assert args[0] == PROGRESS_POLL_TASK
    assert args[1] == {'publish_id': 1}
    publish_service.update_publish_ext.assert_not_called()


@pytest.mark.asyncio
async def test_retry_restart_does_not_enqueue_poll_when_submit_fails():
    """The poll must only be enqueued when the restart actually submitted; a failed
    submit clears the retry flag and leaves the queue untouched."""
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    ext = {
        'source_status': PublishStatus.VALIDATE_PUB.value,
        'retry': False,
        'binding': {'verify': 123},
    }
    records = [
        _make_publish_record(status=PublishStatus.FAILED.value, ext=ext.copy()),
        _make_publish_record(status=PublishStatus.FAILED.value, ext=ext.copy()),
        _make_publish_record(status=PublishStatus.VALIDATE_PUB.value, ext={**ext, 'retry': True}),
    ]
    publish_service.get_publish_by_id.side_effect = records
    publish_service.update_publish_status_with_ext.return_value = _make_publish_record(
        status=PublishStatus.VALIDATE_PUB.value,
        ext={**ext, 'retry': True},
    )
    svc.restart_bot = Mock(return_value={'success': False, 'message': 'submit failed'})

    await svc.retry(publish_id=1, operator='u1')

    svc._task_queue_service.enqueue.assert_not_called()


@pytest.mark.skip(reason="TODO(#168): 待 totalfrank 修复 _artifact_for_stage")
@pytest.mark.asyncio
async def test_execute_rollback_enqueues_progress_poll_for_target():
    """Regression for #162: execute_rollback must enqueue the durable progress poll
    on the TARGET record after approving the BaaS publish, so the rollback advances
    ONLINE_PUB → SUCCESS through the durable queue instead of depending on user
    /sync polling (which became read-only in #105)."""
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))

    target_id = 5319
    current_id = 42
    online_binding_id = 77
    baas_publish_id = 999

    target_record = _make_publish_record(
        id=target_id, status=PublishStatus.SUCCESS.value, version=7,
    )
    current_record = _make_publish_record(
        id=current_id, status=PublishStatus.DRAFT.value, source_bot_id='bot-source',
    )
    publish_service.get_publish_by_id.side_effect = lambda pid: {
        target_id: target_record, current_id: current_record,
    }[pid]

    target_ext = {
        'migration_path': '/tmp/m',
        'config_artifact': {'schema_version': 3},
        'binding': {PublishStage.ONLINE.value: online_binding_id},
        'publish': {},
    }
    svc._get_latest_ext = Mock(return_value=target_ext)
    publish_service.get_device_binding_by_id.return_value = Mock(device_id='BOT-online')
    bot_service.get_bot.return_value = {'bot_id': 'bot-source'}
    build_service.upgrade_async = AsyncMock(return_value={'publish_id': baas_publish_id})
    build_service.generate_request_id.return_value = 'rid'
    svc._update_publish_status = Mock()
    svc.approve_baas_publish = Mock(return_value=True)

    result = await svc.execute_rollback(
        current_publish_id=current_id,
        target_publish_id=target_id,
        operator='u1',
    )

    assert result.publish_id == target_id
    assert result.status == PublishStatus.ONLINE_PUB
    # The poll targets the rollback TARGET (not the current) record and is enqueued
    # after approve_baas_publish.
    svc._task_queue_service.enqueue.assert_called_once()
    args = svc._task_queue_service.enqueue.call_args.args
    assert args[0] == PROGRESS_POLL_TASK
    assert args[1] == {'publish_id': target_id}
    svc.approve_baas_publish.assert_called_once()


def test_handle_sync_failure_clears_retry_flag_and_stores_source_status_value():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    ext = {
        'retry': True,
        'source_status': PublishStatus.ONLINE_PUB.value,
        'restart': {'online': '456'},
    }

    result = svc._handle_sync_failure(
        publish_id=1,
        current_status=PublishStatus.ONLINE_PUB,
        ext=ext,
        progress={'status': 'FAILED', 'failed_devices': [{'id': 'd1'}]},
    )

    assert result.status == PublishStatus.FAILED
    publish_service.update_publish_status_with_ext.assert_called_once()
    updated_ext = publish_service.update_publish_status_with_ext.call_args.kwargs['ext']
    assert 'retry' not in updated_ext
    assert updated_ext['source_status'] == PublishStatus.ONLINE_PUB.value
    assert updated_ext['error_message'] == 'BaaS publish failed: 1 device(s) failed'


# ---------------------------------------------------------------------------
# Task 12: build-phase producer selection + de-hardcoded device_provider
# ---------------------------------------------------------------------------

from agentclaw.community.core.service_bot.services.deploy.producer import (  # noqa: E402
    DeployArtifact,
    DeployArtifactProducer,
    DeployArtifactProducerRouter,
)


class _StubProducer(DeployArtifactProducer):
    def __init__(self, ext: dict) -> None:
        self.ext = ext
        self.calls: list[tuple[dict, int]] = []

    def produce_artifact(self, bot, version) -> DeployArtifact:
        self.calls.append((bot, version))
        return DeployArtifact(success=True, ext=self.ext)


def _build_svc_with_router(router, bot, provider="baas"):
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    # Container is resolved by querying baas (provider != engine). Tests stub the
    # resolved token directly; the binding-lookup + device probe is covered in
    # test_baas_service_resolve_container.py.
    baas_service.resolve_container_provider.return_value = provider
    bot_service = Mock()
    bot_service.get_bot.return_value = bot
    # teclaw build runs the file-promotion gather; default it to a no-op (empty
    # snapshot) so the build-phase routing assertions aren't perturbed. The gather
    # itself is covered by test_stage_teclaw_files_* and the endpoint test.
    from agentclaw.community.core.service_bot.services.deploy.teclaw_file_promotion import (
        PromotedRefs,
    )
    promo = Mock()
    promo.stage_files = AsyncMock(return_value=PromotedRefs())
    svc = _pf(
        publish_service, build_service, baas_service, bot_service,
        producer_router=router, teclaw_file_promotion=promo,
    )
    # Avoid touching real status/ext plumbing — isolate the build phase.
    svc._ext_state.get_latest_ext = Mock(return_value={})
    svc._ext_state.update_status = Mock()
    svc._ext_state.owner_id = Mock(return_value="u1")
    publish_service.update_publish_status = Mock()
    return svc, publish_service


@pytest.mark.asyncio
async def test_build_phase_routes_arca_and_merges_mount_ext():
    arca = _StubProducer({"migration_path": "/m/3", "build_target_path": "/t/3"})
    teclaw = _StubProducer({"artifact_path": "oss://a", "content_hash": "sha256:x"})
    router = DeployArtifactProducerRouter(
        providers={"baas": arca, "teclaw": teclaw}, default_provider_key="baas"
    )
    svc, _ = _build_svc_with_router(router, {"bot_id": "b1", "active_engine": "openclaw"})

    record = _make_publish_record(status=PublishStatus.DRAFT.value, version=3)
    await svc.execute_build_phase(record, "op")

    # openclaw → baas → ARCA producer; teclaw stub untouched.
    assert arca.calls == [({"bot_id": "b1", "active_engine": "openclaw"}, 3)]
    assert teclaw.calls == []
    # ARCA ext carries the mount chain unchanged (the durable record).
    ext_written = svc._ext_state.update_status.call_args.kwargs["ext"]
    assert ext_written["migration_path"] == "/m/3"
    assert ext_written["build_target_path"] == "/t/3"


@pytest.mark.asyncio
async def test_build_phase_routes_external_and_merges_artifact_ext():
    arca = _StubProducer({"migration_path": "/m"})
    teclaw = _StubProducer(
        {"config_artifact": {"schema_version": 2}, "content_hash": "sha256:y", "engine_ext": {"k": 1}}
    )
    router = DeployArtifactProducerRouter(
        providers={"baas": arca, "teclaw": teclaw}, default_provider_key="baas"
    )
    svc, _ = _build_svc_with_router(
        router, {"bot_id": "b2", "active_engine": "teclaw"}, provider="teclaw"
    )

    record = _make_publish_record(status=PublishStatus.DRAFT.value, version=2)
    await svc.execute_build_phase(record, "op")

    # baas reports a TECLAW container → teclaw producer.
    assert teclaw.calls == [({"bot_id": "b2", "active_engine": "teclaw"}, 2)]
    assert arca.calls == []
    # external pins the refs-only artifact onto ext (no mount chain). The teclaw
    # file-promotion gather merges its (here empty) snapshot into the artifact,
    # so the resources/identity_files keys are present (empty).
    ext_written = svc._ext_state.update_status.call_args.kwargs["ext"]
    assert ext_written["config_artifact"] == {
        "schema_version": 2, "resources": [], "identity_files": [],
    }
    assert ext_written["content_hash"] == "sha256:y"
    assert ext_written["engine_ext"] == {"k": 1}


@pytest.mark.asyncio
async def test_build_phase_failed_artifact_returns_failed_result():
    class _Fail(DeployArtifactProducer):
        def produce_artifact(self, bot, version):
            return DeployArtifact(success=False, message="boom")

    router = DeployArtifactProducerRouter(
        providers={"baas": _Fail()}, default_provider_key="baas"
    )
    svc, _ = _build_svc_with_router(router, {"bot_id": "b", "active_engine": "openclaw"})
    record = _make_publish_record(status=PublishStatus.DRAFT.value, version=1)

    # The build phase catches failures and returns a FAILED result (not raises),
    # surfacing the producer's message.
    result = await svc.execute_build_phase(record, "op")
    assert result.status == PublishStatus.FAILED
    assert "boom" in result.message


def test_create_release_binding_uses_resolved_provider_for_external():
    publish_service = Mock()
    publish_service.create_device_binding.return_value = 77
    svc = _pf(publish_service, Mock(), Mock(), Mock(), _arca_router())
    # baas reports a TECLAW container for this bot.
    svc._baas_service.resolve_container_provider = Mock(return_value="teclaw")

    binding_id = svc.create_release_binding(
        bot={"bot_id": "b", "entity_id": "u", "active_engine": "teclaw"},
        bot_uuid="BOT-1",
        baas_publish_id=9,
        operator="op",
    )

    assert binding_id == 77
    # The binding records the resolved provider, not the old hardcoded "baas".
    assert publish_service.create_device_binding.call_args.kwargs["device_provider"] == "teclaw"


def test_create_release_binding_defaults_provider_to_baas():
    publish_service = Mock()
    publish_service.create_device_binding.return_value = 1
    svc = _pf(publish_service, Mock(), Mock(), Mock(), _arca_router())
    # baas does not report a teclaw container → default baas.
    svc._baas_service.resolve_container_provider = Mock(return_value="baas")

    binding_id = svc.create_release_binding(
        bot={"bot_id": "b", "active_engine": "openclaw"},
        bot_uuid="BOT-1",
        baas_publish_id=9,
        operator="op",
    )

    assert binding_id == 1
    assert publish_service.create_device_binding.call_args.kwargs["device_provider"] == "baas"


@pytest.mark.asyncio
async def test_restart_fallback_threads_config_artifact():
    # On BOT_NOT_FOUND, restart falls back to release_async — which for a teclaw
    # bot must carry the frozen artifact (from ext), not an empty config.
    publish_service = Mock()
    build_service = Mock()
    build_service.generate_request_id = Mock(return_value="rid")
    build_service.upgrade_async = AsyncMock(
        return_value={"success": False, "error_code": "BOT_NOT_FOUND"}
    )
    build_service.release_async = AsyncMock(return_value={"publish_id": 99})
    baas_service = Mock()
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service)
    )
    svc._ext_state.update_status = Mock()
    svc._ext_state.get_latest_ext = Mock(return_value={})

    artifact = {"schema_version": 2, "skills": []}
    record = _make_publish_record(ext={"config_artifact": artifact})

    try:
        await svc._restart_bot_async(
            publish_id=1,
            publish_record=record,
            migration_path="",
            bot_uuid="BOT-x",
            binding_id=1,
            bot={"bot_id": "b", "entity_id": "u"},
            stage=PublishStage.ONLINE,
            operator="op",
        )
    except Exception:
        pass  # downstream approve/status flow not under test

    assert build_service.release_async.await_args.kwargs["delivery"].config_artifact == artifact


# ── Task 9: restart reads stored per-stage overrides ─────────────────────────

def _enriched_artifact(stage="release", channels=None):
    art = {
        "engine_type": "teclaw",
        "engine_ext": {"bot_id": "b2", "owner_id": "u1", "stage": stage},
    }
    if channels is not None:
        art["engine_overrides"] = channels
    return art


@pytest.mark.asyncio
async def test_restart_verify_delivers_stored_verify_overrides_not_online():
    # Retry-hole regression: even though online overrides are also stored (and the
    # base may have last been restamped to release), restarting VERIFY must deliver
    # the VERIFY channels + canary stamp — never the online ones.
    build_service = Mock()
    build_service.generate_request_id = Mock(return_value="rid")
    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 9})
    svc = _pf(Mock(), build_service, Mock(), Mock(), _arca_router(build_service))
    svc.refresh_publish_handle = Mock()
    svc._mutate_and_update_ext = Mock()
    svc.approve_baas_publish = Mock()

    record = _make_publish_record(
        ext={
            "config_artifact": _enriched_artifact(
                "release", {"channels": {"dingding": {"accounts": [{"client_id": "draft"}]}}}
            ),
            "engine_overrides_by_stage": {"verify": _VERIFY_CH, "online": _ONLINE_CH},
        }
    )
    await svc._restart_bot_async(
        publish_id=1, publish_record=record, migration_path="", bot_uuid="BOT-x",
        binding_id=1, bot={"bot_id": "b", "entity_id": "u"},
        stage=PublishStage.VERIFY, operator="op",
    )

    delivered = build_service.upgrade_async.await_args.kwargs["delivery"].config_artifact
    assert delivered["engine_overrides"] == _VERIFY_CH
    assert delivered["engine_ext"]["stage"] == "canary"


@pytest.mark.asyncio
async def test_restart_no_stored_overrides_delivers_restamped_base():
    # Pre-feature record (no engine_overrides_by_stage): overlay no-ops, base
    # engine_overrides delivered unchanged; engine_ext.stage still restamped.
    base_channels = {"channels": {"dingding": {"accounts": [{"client_id": "base"}]}}}
    build_service = Mock()
    build_service.generate_request_id = Mock(return_value="rid")
    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 9})
    svc = _pf(Mock(), build_service, Mock(), Mock(), _arca_router(build_service))
    svc.refresh_publish_handle = Mock()
    svc._mutate_and_update_ext = Mock()
    svc.approve_baas_publish = Mock()

    record = _make_publish_record(
        ext={"config_artifact": _enriched_artifact("release", base_channels)}
    )
    await svc._restart_bot_async(
        publish_id=1, publish_record=record, migration_path="", bot_uuid="BOT-x",
        binding_id=1, bot={"bot_id": "b", "entity_id": "u"},
        stage=PublishStage.VERIFY, operator="op",
    )

    delivered = build_service.upgrade_async.await_args.kwargs["delivery"].config_artifact
    assert delivered["engine_overrides"] == base_channels  # unchanged (no overlay)
    assert delivered["engine_ext"]["stage"] == "canary"    # still restamped


@pytest.mark.asyncio
async def test_restart_tolerates_null_engine_overrides_by_stage():
    # A raw ext blob may carry engine_overrides_by_stage as JSON null; the lookup
    # must not raise (the {} get-default does not fire on a present-but-None value).
    build_service = Mock()
    build_service.generate_request_id = Mock(return_value="rid")
    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 9})
    svc = _pf(Mock(), build_service, Mock(), Mock(), _arca_router(build_service))
    svc.refresh_publish_handle = Mock()
    svc._mutate_and_update_ext = Mock()
    svc.approve_baas_publish = Mock()

    record = _make_publish_record(
        ext={"config_artifact": _enriched_artifact("release"), "engine_overrides_by_stage": None}
    )
    await svc._restart_bot_async(
        publish_id=1, publish_record=record, migration_path="", bot_uuid="BOT-x",
        binding_id=1, bot={"bot_id": "b", "entity_id": "u"},
        stage=PublishStage.VERIFY, operator="op",
    )

    delivered = build_service.upgrade_async.await_args.kwargs["delivery"].config_artifact
    assert delivered["engine_ext"]["stage"] == "canary"


# ── teclaw publish_id read-handle refresh (_refresh_publish_handle) ───────────


def test_refresh_publish_handle_merges_publish_id():
    repo = Mock()
    svc = _pf(
        Mock(), Mock(), Mock(), Mock(), _arca_router(), device_binding_repo=repo
    )
    svc.refresh_publish_handle(42, "pub-9")
    repo.update_device_props.assert_called_once_with(
        binding_id=42, props={"publish_id": "pub-9"}
    )


def test_refresh_publish_handle_noop_without_repo():
    # Positional construction (no repo) — the unit-test shape; must not raise.
    svc = _pf(Mock(), Mock(), Mock(), Mock(), _arca_router())
    svc.refresh_publish_handle(42, "pub-9")


def test_refresh_publish_handle_noop_on_missing_ids():
    repo = Mock()
    svc = _pf(
        Mock(), Mock(), Mock(), Mock(), _arca_router(), device_binding_repo=repo
    )
    svc.refresh_publish_handle(None, "pub-9")
    svc.refresh_publish_handle(42, None)
    repo.update_device_props.assert_not_called()


def test_refresh_publish_handle_swallows_repo_error():
    repo = Mock()
    repo.update_device_props.side_effect = RuntimeError("db down")
    svc = _pf(
        Mock(), Mock(), Mock(), Mock(), _arca_router(), device_binding_repo=repo
    )
    svc.refresh_publish_handle(42, "pub-9")  # best-effort → must not raise


# ---------------------------------------------------------------------------
# Rollback: _mark_previous_publish_superseded clears rollback_restored_from marker
# ---------------------------------------------------------------------------


def test_mark_previous_publish_superseded_clears_rollback_restored_from_marker():
    """_mark_previous_publish_superseded should clear the target version's rollback_restored_from marker."""
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    # Current version v3 published successfully, with last_pub_id=2
    current_record = _make_publish_record(
        id=3,
        status=PublishStatus.SUCCESS.value,
        last_pub_id=2,
        version=3,
    )
    # Target version v2 was restored via rollback, carrying the rollback_restored_from marker
    last_publish = _make_publish_record(
        id=2,
        status=PublishStatus.SUCCESS.value,
        version=2,
        ext={"rollback_restored_from": 4, "migration_path": "/tmp/build"},
    )

    publish_service.get_publish_by_id.return_value = last_publish
    publish_service.update_publish_status_with_ext.return_value = last_publish

    # Call _mark_previous_publish_superseded
    svc._mark_previous_publish_superseded(
        publish_record=current_record,
        stage=PublishStage.ONLINE,
        target_status=PublishStatus.SUCCESS,
    )

    # Verify update_publish_status_with_ext was called
    assert publish_service.update_publish_status_with_ext.called
    call_kwargs = publish_service.update_publish_status_with_ext.call_args.kwargs

    # Verify the status changed to UPGRADED
    assert call_kwargs["target_status"] == PublishStatus.UPGRADED.value
    assert call_kwargs["source_status"] == PublishStatus.SUCCESS.value

    # Verify the rollback_restored_from marker was cleared
    assert "rollback_restored_from" not in call_kwargs["ext"]
    # Verify other ext fields are preserved
    assert call_kwargs["ext"]["migration_path"] == "/tmp/build"


def test_mark_previous_publish_superseded_preserves_ext_without_rollback_marker():
    """When the target version has no rollback_restored_from marker, ext stays unchanged."""
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    current_record = _make_publish_record(
        id=3,
        status=PublishStatus.SUCCESS.value,
        last_pub_id=2,
        version=3,
    )
    last_publish = _make_publish_record(
        id=2,
        status=PublishStatus.SUCCESS.value,
        version=2,
        ext={"migration_path": "/tmp/build", "other_key": "other_value"},
    )

    publish_service.get_publish_by_id.return_value = last_publish
    publish_service.update_publish_status_with_ext.return_value = last_publish

    svc._mark_previous_publish_superseded(
        publish_record=current_record,
        stage=PublishStage.ONLINE,
        target_status=PublishStatus.SUCCESS,
    )

    call_kwargs = publish_service.update_publish_status_with_ext.call_args.kwargs

    # Verify ext is preserved in full
    assert call_kwargs["ext"]["migration_path"] == "/tmp/build"
    assert call_kwargs["ext"]["other_key"] == "other_value"


def test_mark_previous_publish_superseded_no_op_for_verify_stage():
    """The VERIFY stage does not invoke the _mark_previous_publish_superseded logic."""
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    current_record = _make_publish_record(
        id=3,
        status=PublishStatus.SUCCESS.value,
        last_pub_id=2,
        version=3,
    )

    svc._mark_previous_publish_superseded(
        publish_record=current_record,
        stage=PublishStage.VERIFY,  # non-ONLINE stage
        target_status=PublishStatus.SUCCESS,
    )

    # Verify get_publish_by_id was not called (the VERIFY stage does not upgrade the previous version)
    publish_service.get_publish_by_id.assert_not_called()
    publish_service.update_publish_status_with_ext.assert_not_called()


def test_mark_previous_publish_superseded_no_op_for_non_success_status():
    """When the target status is not SUCCESS, the _mark_previous_publish_superseded logic is not invoked."""
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    current_record = _make_publish_record(
        id=3,
        status=PublishStatus.VALIDATING.value,
        last_pub_id=2,
        version=3,
    )

    svc._mark_previous_publish_superseded(
        publish_record=current_record,
        stage=PublishStage.ONLINE,
        target_status=PublishStatus.VALIDATING,  # not SUCCESS
    )

    publish_service.get_publish_by_id.assert_not_called()
    publish_service.update_publish_status_with_ext.assert_not_called()


def test_mark_previous_publish_superseded_no_op_when_last_pub_id_is_zero():
    """When last_pub_id is 0, the upgrade logic is not invoked."""
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    current_record = _make_publish_record(
        id=1,
        status=PublishStatus.SUCCESS.value,
        last_pub_id=0,  # no previous version
        version=1,
    )

    svc._mark_previous_publish_superseded(
        publish_record=current_record,
        stage=PublishStage.ONLINE,
        target_status=PublishStatus.SUCCESS,
    )

    publish_service.get_publish_by_id.assert_not_called()
    publish_service.update_publish_status_with_ext.assert_not_called()


# ---------------------------------------------------------------------------
# engine_ext.stage promotion: re-stamp delivered + persisted artifact per env
# ---------------------------------------------------------------------------


def _artifact_ext(stage="draft"):
    """A build snapshot ext carrying an enriched external config_artifact."""
    return {
        "config_artifact": {
            "schema_version": 2,
            "engine_type": "teclaw",
            "engine_ext": {"bot_id": "b2", "owner_id": "u1", "stage": stage},
        }
    }


@pytest.mark.asyncio
async def test_verify_first_release_stamps_canary_delivered_and_persisted():
    publish_service = Mock()
    publish_service.create_device_binding.return_value = 55
    build_service = Mock()
    build_service.release_async = AsyncMock(
        return_value={"bot_uuid": "BOT-1", "publish_id": 9}
    )
    build_service.generate_request_id = Mock(return_value="rid")
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "teclaw"
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service)
    )
    # _record_release_ext re-reads ext from DB (a fresh draft snapshot).
    svc._ext_state.get_latest_ext = Mock(return_value=_artifact_ext("draft"))
    svc._ext_state.update_status = Mock()
    svc.approve_baas_publish = Mock()

    record = _make_publish_record(
        status=PublishStatus.BUILT.value, ext=_artifact_ext("draft")
    )
    await svc._execute_verify_first_release(
        publish_record=record,
        operator="op",
        migration_path="",
        bot={"bot_id": "b2", "owner_id": "u1"},
    )

    delivered = build_service.release_async.await_args.kwargs["delivery"].config_artifact
    assert delivered["engine_ext"]["stage"] == "canary"
    # identity keys stable across the promotion
    assert delivered["engine_ext"]["bot_id"] == "b2"
    assert delivered["engine_ext"]["owner_id"] == "u1"

    persisted = svc._ext_state.update_status.call_args.kwargs["ext"]["config_artifact"]
    assert persisted["engine_ext"]["stage"] == "canary"
    assert persisted["engine_ext"]["bot_id"] == "b2"


@pytest.mark.asyncio
async def test_verify_upgrade_stamps_canary_delivered_and_persisted():
    publish_service = Mock()
    build_service = Mock()
    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 9})
    build_service.generate_request_id = Mock(return_value="rid")
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "teclaw"
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service)
    )
    svc._ext_state.get_latest_ext = Mock(return_value=_artifact_ext("draft"))
    svc._ext_state.update_status = Mock()
    svc.approve_baas_publish = Mock()
    svc.refresh_publish_handle = Mock()

    record = _make_publish_record(
        status=PublishStatus.BUILT.value, ext=_artifact_ext("draft")
    )
    await svc._execute_verify_upgrade(
        publish_record=record,
        operator="op",
        migration_path="",
        bot={"bot_id": "b2", "owner_id": "u1"},
        bot_uuid="BOT-old",
        verify_binding_id=123,
    )

    delivered = build_service.upgrade_async.await_args.kwargs["delivery"].config_artifact
    assert delivered["engine_ext"]["stage"] == "canary"
    persisted = svc._ext_state.update_status.call_args.kwargs["ext"]["config_artifact"]
    assert persisted["engine_ext"]["stage"] == "canary"


@pytest.mark.asyncio
async def test_verify_upgrade_refreshes_teclaw_rule_after_baas_approve():
    publish_service = Mock()
    build_service = Mock()
    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 9})
    build_service.generate_request_id = Mock(return_value="rid")
    build_service.refresh_teclaw_mcp_outbound_rule = Mock()
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "teclaw"
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service)
    )
    svc._ext_state.get_latest_ext = Mock(return_value=_artifact_ext("draft"))
    svc._ext_state.update_status = Mock()
    svc.approve_baas_publish = Mock(return_value=True)
    svc.refresh_publish_handle = Mock()

    record = _make_publish_record(
        status=PublishStatus.BUILT.value, ext=_artifact_ext("draft")
    )
    bot = {"bot_id": "b2", "entity_id": "u1"}

    await svc._execute_verify_upgrade(
        publish_record=record,
        operator="op",
        migration_path="",
        bot=bot,
        bot_uuid="BOT-old",
        verify_binding_id=123,
    )

    build_service.refresh_teclaw_mcp_outbound_rule.assert_called_once_with(
        bot_uuid="BOT-old",
        bot=bot,
    )


# ── Task 7: verify promotion fetches + overlays + stores per-stage channels ──

_VERIFY_CH = {"channels": {"dingding": {"enabled": True, "accounts": [{"client_id": "v1"}]}}}


def _reader_returning(overrides):
    reader = Mock()
    reader.overrides_for_stage.return_value = overrides
    return reader


@pytest.mark.asyncio
async def test_verify_first_release_overlays_and_stores_stage_channels():
    publish_service = Mock()
    publish_service.create_device_binding.return_value = 55
    build_service = Mock()
    build_service.release_async = AsyncMock(
        return_value={"bot_uuid": "BOT-1", "publish_id": 9}
    )
    build_service.generate_request_id = Mock(return_value="rid")
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "teclaw"
    reader = _reader_returning(_VERIFY_CH)
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service),
        channel_overrides_reader=reader,
    )
    svc._ext_state.get_latest_ext = Mock(return_value=_artifact_ext("draft"))
    svc._ext_state.update_status = Mock()
    svc.approve_baas_publish = Mock()

    record = _make_publish_record(
        status=PublishStatus.BUILT.value, ext=_artifact_ext("draft")
    )
    await svc._execute_verify_first_release(
        publish_record=record, operator="op", migration_path="",
        bot={"bot_id": "b2", "owner_id": "u1"},
    )

    # fetched for the VERIFY stage only
    assert reader.overrides_for_stage.call_args.kwargs["accept_stages"] == {"verify"}
    # delivered artifact carries the verify channels
    delivered = build_service.release_async.await_args.kwargs["delivery"].config_artifact
    assert delivered["engine_overrides"] == _VERIFY_CH
    assert delivered["engine_ext"]["stage"] == "canary"
    # persisted per-stage map
    persisted_ext = svc._ext_state.update_status.call_args.kwargs["ext"]
    assert persisted_ext["engine_overrides_by_stage"]["verify"] == _VERIFY_CH


@pytest.mark.asyncio
async def test_verify_upgrade_overlays_and_stores_stage_channels():
    publish_service = Mock()
    build_service = Mock()
    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 9})
    build_service.generate_request_id = Mock(return_value="rid")
    reader = _reader_returning(_VERIFY_CH)
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "teclaw"
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service),
        channel_overrides_reader=reader,
    )
    svc._ext_state.get_latest_ext = Mock(return_value=_artifact_ext("draft"))
    svc._ext_state.update_status = Mock()
    svc.approve_baas_publish = Mock()
    svc.refresh_publish_handle = Mock()

    record = _make_publish_record(
        status=PublishStatus.BUILT.value, ext=_artifact_ext("draft")
    )
    await svc._execute_verify_upgrade(
        publish_record=record, operator="op", migration_path="",
        bot={"bot_id": "b2", "owner_id": "u1"}, bot_uuid="BOT-old", verify_binding_id=123,
    )

    delivered = build_service.upgrade_async.await_args.kwargs["delivery"].config_artifact
    assert delivered["engine_overrides"] == _VERIFY_CH
    assert delivered["engine_ext"]["stage"] == "canary"
    persisted_ext = svc._ext_state.update_status.call_args.kwargs["ext"]
    assert persisted_ext["engine_overrides_by_stage"]["verify"] == _VERIFY_CH


@pytest.mark.asyncio
async def test_verify_first_release_raises_when_baas_returns_no_publish_id():
    # BaaS returned a bot_uuid but no publish_id → first_release raises before
    # recording, so the release path always gets a real int id.
    publish_service = Mock()
    build_service = Mock()
    build_service.release_async = AsyncMock(return_value={"bot_uuid": "BOT-1"})
    baas_service = Mock()
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service)
    )
    svc._ext_state.get_latest_ext = Mock(return_value=_artifact_ext("draft"))

    record = _make_publish_record(
        status=PublishStatus.BUILT.value, ext=_artifact_ext("draft")
    )
    with pytest.raises(PublishFlowServiceError, match="publish_id"):
        await svc._execute_verify_first_release(
            publish_record=record, operator="op", migration_path="",
            bot={"bot_id": "b2", "owner_id": "u1"},
        )
    publish_service.create_device_binding.assert_not_called()


@pytest.mark.asyncio
async def test_verify_first_release_arca_skips_channel_fetch_and_store():
    # ARCA mount path: ext has migration_path, NO config_artifact → no fetch, no
    # overlay, no per-stage store; delivery stays None.
    publish_service = Mock()
    publish_service.create_device_binding.return_value = 55
    build_service = Mock()
    build_service.release_async = AsyncMock(
        return_value={"bot_uuid": "BOT-1", "publish_id": 9}
    )
    build_service.generate_request_id = Mock(return_value="rid")
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "baas"
    reader = _reader_returning(_VERIFY_CH)
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service),
        channel_overrides_reader=reader,
    )
    svc._ext_state.get_latest_ext = Mock(return_value={"migration_path": "/m"})
    svc._ext_state.update_status = Mock()
    svc.approve_baas_publish = Mock()

    record = _make_publish_record(
        status=PublishStatus.BUILT.value, ext={"migration_path": "/m"}
    )
    await svc._execute_verify_first_release(
        publish_record=record, operator="op", migration_path="/m",
        bot={"bot_id": "b2", "owner_id": "u1"},
    )

    reader.overrides_for_stage.assert_not_called()
    assert build_service.release_async.await_args.kwargs["delivery"].config_artifact is None
    persisted_ext = svc._ext_state.update_status.call_args.kwargs["ext"]
    assert "engine_overrides_by_stage" not in persisted_ext


@pytest.mark.asyncio
async def test_online_first_release_stamps_release_delivered_and_persisted():
    publish_service = Mock()
    publish_service.create_device_binding.return_value = 55
    build_service = Mock()
    build_service.release_async = AsyncMock(
        return_value={"bot_uuid": "BOT-1", "publish_id": 9}
    )
    build_service.generate_request_id = Mock(return_value="rid")
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "teclaw"
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service)
    )
    svc._ext_state.get_latest_ext = Mock(return_value=_artifact_ext("canary"))
    svc._ext_state.update_status = Mock()
    svc.approve_baas_publish = Mock()

    record = _make_publish_record(
        status=PublishStatus.VALIDATING.value, ext=_artifact_ext("canary")
    )
    await svc._execute_first_release(
        publish_record=record,
        operator="op",
        migration_path="",
        bot={"bot_id": "b2", "owner_id": "u1"},
    )

    delivered = build_service.release_async.await_args.kwargs["delivery"].config_artifact
    assert delivered["engine_ext"]["stage"] == "release"
    persisted = svc._ext_state.update_status.call_args.kwargs["ext"]["config_artifact"]
    assert persisted["engine_ext"]["stage"] == "release"
    assert persisted["engine_ext"]["bot_id"] == "b2"


@pytest.mark.asyncio
async def test_online_upgrade_stamps_release_delivered_and_persisted():
    publish_service = Mock()
    build_service = Mock()
    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 9})
    build_service.generate_request_id = Mock(return_value="rid")
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "teclaw"
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service)
    )
    last_publish = _make_publish_record(
        id=10, status=PublishStatus.SUCCESS.value, ext={"binding": {"online": 88}}
    )
    publish_service.get_publish_by_id.return_value = last_publish
    publish_service.get_device_binding_by_id.return_value = Mock(device_id="BOT-old")
    svc._ext_state.get_latest_ext = Mock(return_value=_artifact_ext("canary"))
    svc._ext_state.update_status = Mock()
    svc.approve_baas_publish = Mock()
    svc.refresh_publish_handle = Mock()

    record = _make_publish_record(
        status=PublishStatus.VALIDATING.value,
        last_pub_id=10,
        ext=_artifact_ext("canary"),
    )
    await svc._execute_upgrade_release(
        publish_record=record,
        operator="op",
        migration_path="",
        bot={"bot_id": "b2", "owner_id": "u1"},
    )

    delivered = build_service.upgrade_async.await_args.kwargs["delivery"].config_artifact
    assert delivered["engine_ext"]["stage"] == "release"
    persisted = svc._ext_state.update_status.call_args.kwargs["ext"]["config_artifact"]
    assert persisted["engine_ext"]["stage"] == "release"


@pytest.mark.asyncio
async def test_online_upgrade_refreshes_teclaw_rule_after_baas_approve():
    publish_service = Mock()
    build_service = Mock()
    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 9})
    build_service.generate_request_id = Mock(return_value="rid")
    build_service.refresh_teclaw_mcp_outbound_rule = Mock()
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "teclaw"
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service)
    )
    last_publish = _make_publish_record(
        id=10, status=PublishStatus.SUCCESS.value, ext={"binding": {"online": 88}}
    )
    publish_service.get_publish_by_id.return_value = last_publish
    publish_service.get_device_binding_by_id.return_value = Mock(device_id="BOT-old")
    svc._ext_state.get_latest_ext = Mock(return_value=_artifact_ext("canary"))
    svc._ext_state.update_status = Mock()
    svc.approve_baas_publish = Mock(return_value=True)
    svc.refresh_publish_handle = Mock()

    record = _make_publish_record(
        status=PublishStatus.VALIDATING.value,
        last_pub_id=10,
        ext=_artifact_ext("canary"),
    )
    bot = {"bot_id": "b2", "entity_id": "u1"}

    await svc._execute_upgrade_release(
        publish_record=record,
        operator="op",
        migration_path="",
        bot=bot,
    )

    build_service.refresh_teclaw_mcp_outbound_rule.assert_called_once_with(
        bot_uuid="BOT-old",
        bot=bot,
    )


# ── Task 8: online promotion fetches + overlays + stores per-stage channels ──

_ONLINE_CH = {"channels": {"dingding": {"enabled": True, "accounts": [{"client_id": "o1"}]}}}


@pytest.mark.asyncio
async def test_online_first_release_overlays_and_stores_stage_channels():
    publish_service = Mock()
    publish_service.create_device_binding.return_value = 55
    build_service = Mock()
    build_service.release_async = AsyncMock(
        return_value={"bot_uuid": "BOT-1", "publish_id": 9}
    )
    build_service.generate_request_id = Mock(return_value="rid")
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "teclaw"
    reader = _reader_returning(_ONLINE_CH)
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service),
        channel_overrides_reader=reader,
    )
    svc._ext_state.get_latest_ext = Mock(return_value=_artifact_ext("canary"))
    svc._ext_state.update_status = Mock()
    svc.approve_baas_publish = Mock()

    record = _make_publish_record(
        status=PublishStatus.VALIDATING.value, ext=_artifact_ext("canary")
    )
    await svc._execute_first_release(
        publish_record=record, operator="op", migration_path="",
        bot={"bot_id": "b2", "owner_id": "u1"},
    )

    assert reader.overrides_for_stage.call_args.kwargs["accept_stages"] == {"online"}
    delivered = build_service.release_async.await_args.kwargs["delivery"].config_artifact
    assert delivered["engine_overrides"] == _ONLINE_CH
    assert delivered["engine_ext"]["stage"] == "release"
    persisted_ext = svc._ext_state.update_status.call_args.kwargs["ext"]
    assert persisted_ext["engine_overrides_by_stage"]["online"] == _ONLINE_CH


@pytest.mark.asyncio
async def test_online_upgrade_overlays_and_stores_stage_channels():
    publish_service = Mock()
    build_service = Mock()
    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 9})
    build_service.generate_request_id = Mock(return_value="rid")
    reader = _reader_returning(_ONLINE_CH)
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "teclaw"
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service),
        channel_overrides_reader=reader,
    )
    last_publish = _make_publish_record(
        id=10, status=PublishStatus.SUCCESS.value, ext={"binding": {"online": 88}}
    )
    publish_service.get_publish_by_id.return_value = last_publish
    publish_service.get_device_binding_by_id.return_value = Mock(device_id="BOT-old")
    svc._ext_state.get_latest_ext = Mock(return_value=_artifact_ext("canary"))
    svc._ext_state.update_status = Mock()
    svc.approve_baas_publish = Mock()
    svc.refresh_publish_handle = Mock()

    record = _make_publish_record(
        status=PublishStatus.VALIDATING.value, last_pub_id=10, ext=_artifact_ext("canary")
    )
    await svc._execute_upgrade_release(
        publish_record=record, operator="op", migration_path="",
        bot={"bot_id": "b2", "owner_id": "u1"},
    )

    delivered = build_service.upgrade_async.await_args.kwargs["delivery"].config_artifact
    assert delivered["engine_overrides"] == _ONLINE_CH
    assert delivered["engine_ext"]["stage"] == "release"
    persisted_ext = svc._ext_state.update_status.call_args.kwargs["ext"]
    assert persisted_ext["engine_overrides_by_stage"]["online"] == _ONLINE_CH


@pytest.mark.asyncio
async def test_arca_path_has_no_config_artifact_and_is_not_restamped():
    # ARCA mount path: ext carries migration_path, not config_artifact. Re-stamp
    # must no-op — None delivered, no spurious config_artifact key persisted.
    publish_service = Mock()
    publish_service.create_device_binding.return_value = 55
    build_service = Mock()
    build_service.release_async = AsyncMock(
        return_value={"bot_uuid": "BOT-1", "publish_id": 9}
    )
    build_service.generate_request_id = Mock(return_value="rid")
    baas_service = Mock()
    baas_service.resolve_container_provider.return_value = "baas"
    svc = _pf(
        publish_service, build_service, baas_service, Mock(), _arca_router(build_service)
    )
    svc._ext_state.get_latest_ext = Mock(return_value={"migration_path": "/m"})
    svc._ext_state.update_status = Mock()
    svc.approve_baas_publish = Mock()

    record = _make_publish_record(
        status=PublishStatus.BUILT.value, ext={"migration_path": "/m"}
    )
    await svc._execute_verify_first_release(
        publish_record=record,
        operator="op",
        migration_path="/m",
        bot={"bot_id": "b2", "owner_id": "u1"},
    )

    assert build_service.release_async.await_args.kwargs["delivery"].config_artifact is None
    persisted = svc._ext_state.update_status.call_args.kwargs["ext"]
    assert "config_artifact" not in persisted


def test_mark_previous_publish_superseded_warns_when_last_publish_not_found():
    """When the previous version does not exist, log a warning but do not raise."""
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    svc = _pf(publish_service, build_service, baas_service, Mock(), _arca_router(build_service))

    current_record = _make_publish_record(
        id=3,
        status=PublishStatus.SUCCESS.value,
        last_pub_id=999,  # nonexistent previous version
        version=3,
    )

    publish_service.get_publish_by_id.return_value = None

    # Should not raise
    svc._mark_previous_publish_superseded(
        publish_record=current_record,
        stage=PublishStage.ONLINE,
        target_status=PublishStatus.SUCCESS,
    )

    # Verify get_publish_by_id was called but update was not
    publish_service.get_publish_by_id.assert_called_once_with(999)
    publish_service.update_publish_status_with_ext.assert_not_called()


# ============================================================================
# execute_rollback tests
# ============================================================================


@pytest.mark.skip(reason="TODO(#168): 待 totalfrank 修复 _artifact_for_stage")
@pytest.mark.asyncio
async def test_execute_rollback_uses_fixed_device_count_one():
    """execute_rollback should always use a fixed device_count=1."""
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    # Mock upgrade_async
    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 12345})
    build_service.generate_request_id = Mock(return_value="req-rollback-1")
    baas_service.approve_publish = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))

    # Current version (v3, DRAFT)
    current_record = _make_publish_record(
        id=3,
        status=PublishStatus.DRAFT.value,
        version=3,
        source_bot_id="bot-123",
    )

    # Target version (v2, SUCCESS) - has a build artifact and binding
    target_record = _make_publish_record(
        id=2,
        status=PublishStatus.SUCCESS.value,
        version=2,
        ext={
            "migration_path": "/tmp/build/v2",
            "binding": {"online": 100},  # binding_id
        },
    )

    # Mock binding
    mock_binding = Mock()
    mock_binding.device_id = "device-uuid-001"

    publish_service.get_publish_by_id.side_effect = [target_record, current_record]
    publish_service.get_device_binding_by_id.return_value = mock_binding
    publish_service.get_publish_by_id.return_value = target_record  # subsequent calls return target
    bot_service.get_bot.return_value = {"bot_id": "bot-123", "entity_id": "e1", "entity_type": "staff"}

    # get_publish_by_id needs re-setup because it is called multiple times
    def get_publish_side_effect(pk):
        if pk == 2:
            return target_record
        elif pk == 3:
            return current_record
        return None

    publish_service.get_publish_by_id = Mock(side_effect=get_publish_side_effect)

    # Call execute_rollback - get_publish_by_id must be set up first so get_latest_ext works
    result = await svc.execute_rollback(
        current_publish_id=3,
        target_publish_id=2,
        operator="user1",
    )

    # Verify upgrade_async uses the fixed device_count
    upgrade_call = build_service.upgrade_async.call_args
    assert upgrade_call.kwargs["device_count"] == 1

    # Verify the return value
    assert result.publish_id == 2  # returns target_publish_id
    assert result.status == PublishStatus.ONLINE_PUB


@pytest.mark.asyncio
async def test_execute_rollback_missing_build_artifact():
    """execute_rollback should raise when the build artifact is missing."""
    from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowServiceError

    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))

    current_record = _make_publish_record(id=3, status=PublishStatus.DRAFT.value, owner_id="user1")

    # Target version has no build artifact
    target_record = _make_publish_record(
        id=2,
        status=PublishStatus.SUCCESS.value,
        ext={
            "binding": {"online": 100},
            # missing migration_path and config_artifact
        },
    )

    def get_publish_side_effect(pk):
        if pk == 2:
            return target_record
        elif pk == 3:
            return current_record
        return None

    publish_service.get_publish_by_id = Mock(side_effect=get_publish_side_effect)

    with pytest.raises(PublishFlowServiceError, match="Target version is missing build artifact"):
        await svc.execute_rollback(
            current_publish_id=3,
            target_publish_id=2,
            operator="user1",
        )


@pytest.mark.asyncio
async def test_execute_rollback_missing_binding():
    """execute_rollback should raise when the binding is missing."""
    from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowServiceError

    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))

    current_record = _make_publish_record(id=3, status=PublishStatus.DRAFT.value, owner_id="user1")

    # Target version has a build artifact but no binding
    target_record = _make_publish_record(
        id=2,
        status=PublishStatus.SUCCESS.value,
        ext={
            "migration_path": "/tmp/build/v2",
            # missing binding
        },
    )

    def get_publish_side_effect(pk):
        if pk == 2:
            return target_record
        elif pk == 3:
            return current_record
        return None

    publish_service.get_publish_by_id = Mock(side_effect=get_publish_side_effect)

    with pytest.raises(PublishFlowServiceError, match="Target version is missing online binding"):
        await svc.execute_rollback(
            current_publish_id=3,
            target_publish_id=2,
            operator="user1",
        )


@pytest.mark.skip(reason="TODO(#168): 待 totalfrank 修复 _artifact_for_stage delivery 契约")
@pytest.mark.asyncio
async def test_execute_rollback_with_config_artifact():
    """execute_rollback uses config_artifact (the teclaw scenario); a target
    without engine_overrides_by_stage (pre-feature record) delivers the raw
    stored artifact unchanged (restamp/overlay no-op)."""
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 12345})
    build_service.generate_request_id = Mock(return_value="req-rollback-2")
    baas_service.approve_publish = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))

    current_record = _make_publish_record(
        id=3,
        status=PublishStatus.DRAFT.value,
        version=3,
        source_bot_id="bot-456",
    )

# Target version has only config_artifact (no migration_path); no stored
    # per-stage overrides.
    artifact = {"schema_version": 3, "engine_type": "teclaw", "engine_ext": {}}
    target_record = _make_publish_record(
        id=2,
        status=PublishStatus.SUCCESS.value,
        version=2,
        ext={
"config_artifact": artifact,
            "binding": {"online": 200},
        },
    )

    mock_binding = Mock()
    mock_binding.device_id = "device-uuid-002"

    def get_publish_side_effect(pk):
        if pk == 2:
            return target_record
        elif pk == 3:
            return current_record
        return None

    publish_service.get_publish_by_id = Mock(side_effect=get_publish_side_effect)
    publish_service.get_device_binding_by_id.return_value = mock_binding
    bot_service.get_bot.return_value = {"bot_id": "bot-456", "entity_id": "e2", "entity_type": "staff"}

    result = await svc.execute_rollback(
        current_publish_id=3,
        target_publish_id=2,
        operator="user1",
    )

# Verify upgrade_async used config_artifact, unchanged (backward compat)
    upgrade_call = build_service.upgrade_async.call_args
    assert upgrade_call.kwargs["config_artifact"] == artifact
    assert upgrade_call.kwargs["migration_path"] is None

    assert result.publish_id == 2
    assert result.status == PublishStatus.ONLINE_PUB


@pytest.mark.skip(reason="TODO(#168): 待 totalfrank 修复 _artifact_for_stage delivery 契约")
@pytest.mark.asyncio
async def test_execute_rollback_delivers_stored_online_overrides_not_live():
    """Regression for #168: rollback must overlay the target version's STORED
    online engine_overrides (DingTalk channels incl. card_template_id) onto the
    delivered artifact — the per-stage slot persisted at that version's online
    promotion — and must NOT re-fetch live channel config (which holds the state
    being rolled away from)."""
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    build_service.upgrade_async = AsyncMock(return_value={"publish_id": 12345})
    build_service.generate_request_id = Mock(return_value="req-rollback-3")
    baas_service.approve_publish = Mock()

    # A live reader that would deliver the WRONG (current) channels if consulted.
    reader = Mock()
    reader.overrides_for_stage.return_value = {
        "channels": {"dingding": {"enabled": True, "accounts": [{"client_id": "live-wrong"}]}}
    }

    svc = _pf(
        publish_service, build_service, baas_service, bot_service,
        _arca_router(build_service), channel_overrides_reader=reader,
    )

    current_record = _make_publish_record(
        id=3, status=PublishStatus.DRAFT.value, version=3, source_bot_id="bot-456",
    )

    stored_online = {
        "channels": {
            "dingding": {
                "enabled": True,
                "accounts": [{"client_id": "cid-1", "card_template_id": "card-A"}],
            }
        }
    }
    target_record = _make_publish_record(
        id=2,
        status=PublishStatus.SUCCESS.value,
        version=2,
        ext={
            # Base artifact carries stale draft channels; the stored online slot wins.
            "config_artifact": _enriched_artifact(
                "release",
                {"channels": {"dingding": {"accounts": [{"client_id": "draft"}]}}},
            ),
            "engine_overrides_by_stage": {"verify": _VERIFY_CH, "online": stored_online},
            "binding": {"online": 200},
        },
    )

    mock_binding = Mock()
    mock_binding.device_id = "device-uuid-003"

    def get_publish_side_effect(pk):
        if pk == 2:
            return target_record
        elif pk == 3:
            return current_record
        return None

    publish_service.get_publish_by_id = Mock(side_effect=get_publish_side_effect)
    publish_service.get_device_binding_by_id.return_value = mock_binding
    bot_service.get_bot.return_value = {"bot_id": "bot-456", "entity_id": "e2", "entity_type": "staff"}

    await svc.execute_rollback(
        current_publish_id=3,
        target_publish_id=2,
        operator="user1",
    )

    delivered = build_service.upgrade_async.await_args.kwargs["config_artifact"]
    assert delivered["engine_overrides"] == stored_online
    assert delivered["engine_ext"]["stage"] == "release"
    # Stored slot, never a live re-fetch.
    reader.overrides_for_stage.assert_not_called()


# teclaw build-time file promotion moved to TeclawProviderBehavior; its test now
# lives in test_provider_behavior.py (test_teclaw_stage_build_files_merges_refs).


@pytest.mark.unit
def test_scale_bot_success_prefers_bot_ext_device_count():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()
    common_config_service = Mock()

    svc = _pf(
        publish_service,
        build_service,
        baas_service,
        bot_service,
        _arca_router(build_service),
        common_config_service=common_config_service,
    )

    record = _make_publish_record(
        id=10,
        status=PublishStatus.SUCCESS.value,
        ext={"binding": {"online": 123}},
    )
    binding = Mock()
    binding.device_id = "BOT-UUID-1"

    publish_service.get_publish_by_id = Mock(return_value=record)
    publish_service.get_device_binding_by_id = Mock(return_value=binding)
    bot_service.get_bot = Mock(return_value={"bot_id": "bot-source", "ext": {"service_bot_config": {"device_count": 3}}})
    baas_service.scale_bot = Mock(return_value={"publish_id": 888, "target_count": 3})

    result = svc.scale_bot(publish_id=10, operator="u1")

    assert result["success"] is True
    assert result["bot_uuid"] == "BOT-UUID-1"
    assert result["target_count"] == 3
    assert result["baas_publish_id"] == 888
    common_config_service.get_value.assert_not_called()
    _, kwargs = baas_service.scale_bot.call_args
    assert kwargs["bot_uuid"] == "BOT-UUID-1"
    assert kwargs["owner_id"] == "u1"
    assert kwargs["target_count"] == 3
    assert kwargs["auto_approve_publish"] is True
    assert kwargs["request_id"].startswith("scale_BOT-UUID-1_")
    publish_service.update_publish_ext.assert_called_once_with(
        publish_id=10,
        ext={"binding": {"online": 123}, "scale": {"publish_id": 888}},
    )


@pytest.mark.unit
def test_scale_bot_falls_back_to_common_config_default_device_count():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()
    common_config_service = Mock()

    svc = _pf(
        publish_service,
        build_service,
        baas_service,
        bot_service,
        _arca_router(build_service),
        common_config_service=common_config_service,
    )

    record = _make_publish_record(
        id=13,
        status=PublishStatus.SUCCESS.value,
        ext={"binding": {"online": 321}},
    )
    binding = Mock()
    binding.device_id = "BOT-UUID-2"

    publish_service.get_publish_by_id = Mock(return_value=record)
    publish_service.get_device_binding_by_id = Mock(return_value=binding)
    bot_service.get_bot = Mock(return_value={"bot_id": "bot-source", "ext": {}})
    common_config_service.get_value = Mock(return_value=2)
    baas_service.scale_bot = Mock(return_value={"publish_id": 999, "target_count": 2})

    result = svc.scale_bot(publish_id=13, operator="u1")

    assert result["target_count"] == 2
    common_config_service.get_value.assert_called_once()
    _, kwargs = baas_service.scale_bot.call_args
    assert kwargs["bot_uuid"] == "BOT-UUID-2"
    assert kwargs["owner_id"] == "u1"
    assert kwargs["target_count"] == 2
    assert kwargs["auto_approve_publish"] is True
    assert kwargs["request_id"].startswith("scale_BOT-UUID-2_")


@pytest.mark.unit
def test_scale_bot_teclaw_returns_supported_message_without_baas_call():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))

    publish_service.get_publish_by_id = Mock(
        return_value=_make_publish_record(id=15, status=PublishStatus.SUCCESS.value)
    )
    bot_service.get_bot = Mock(
        return_value={"bot_id": "bot-source", "active_engine": "teclaw", "ext": {}}
    )
    # resolve_container_provider derives from active_engine in real code; the
    # provider seam reads teclaw → TeclawProviderBehavior (supports_scale=False).
    baas_service.resolve_container_provider.return_value = "teclaw"

    result = svc.scale_bot(publish_id=15, operator="u1")

    assert result == {
        "success": True,
        "message": "Service bots on the teclaw engine do not support scaling",
        "publish_id": 15,
        "engine": "teclaw",
        "supported": True,
    }
    baas_service.scale_bot.assert_not_called()
    publish_service.get_device_binding_by_id.assert_not_called()


@pytest.mark.unit
def test_scale_bot_invalid_status():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))
    publish_service.get_publish_by_id = Mock(return_value=_make_publish_record(id=11, status=PublishStatus.DRAFT.value))

    with pytest.raises(PublishStatusInvalidError, match="does not support scale operations"):
        svc.scale_bot(publish_id=11, operator="u1")


@pytest.mark.unit
def test_scale_bot_missing_online_binding():
    from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowServiceError
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))
    publish_service.get_publish_by_id = Mock(return_value=_make_publish_record(id=12, status=PublishStatus.SUCCESS.value, ext={}))

    with pytest.raises(PublishFlowServiceError, match="Binding info for the online stage not found"):
        svc.scale_bot(publish_id=12, operator="u1")


@pytest.mark.unit
def test_scale_bot_raises_when_ext_and_common_config_both_missing():
    from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowServiceError
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()
    common_config_service = Mock()

    svc = _pf(
        publish_service,
        build_service,
        baas_service,
        bot_service,
        _arca_router(build_service),
        common_config_service=common_config_service,
    )
    publish_service.get_publish_by_id = Mock(
        return_value=_make_publish_record(
            id=14,
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 456}},
        )
    )
    binding = Mock()
    binding.device_id = "BOT-UUID-3"
    publish_service.get_device_binding_by_id = Mock(return_value=binding)
    bot_service.get_bot = Mock(return_value={"bot_id": "bot-source", "ext": {"service_bot_config": {"device_count": 0}}})
    common_config_service.get_value = Mock(return_value=None)

    with pytest.raises(PublishFlowServiceError, match="service_bot_config\\.device_count not found"):
        svc.scale_bot(publish_id=14, operator="u1")


@pytest.mark.unit
def test_sync_scale_progress_raises_when_publish_not_found():
    from agentclaw.community.core.service_bot.services.bot_publish_service import PublishNotFoundError

    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))
    publish_service.get_publish_by_id = Mock(return_value=None)

    with pytest.raises(PublishNotFoundError, match="Publish order not found: 101"):
        svc.sync_scale_progress(101)


@pytest.mark.unit
@pytest.mark.parametrize("ext", [None, {}, {"scale": {}}, {"scale": {"publish_id": ""}}])
def test_sync_scale_progress_returns_message_when_scale_publish_id_missing(ext):
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))
    publish_service.get_publish_by_id = Mock(
        return_value=_make_publish_record(id=102, status=PublishStatus.SUCCESS.value, ext=ext)
    )

    result = svc.sync_scale_progress(102)

    assert result.publish_id == 102
    assert result.status == PublishStatus.SUCCESS
    assert result.message == "Scale publish record ID not found"
    baas_service.get_publish_progress.assert_not_called()


@pytest.mark.unit
def test_sync_scale_progress_returns_error_when_baas_progress_query_fails():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))
    publish_service.get_publish_by_id = Mock(
        return_value=_make_publish_record(
            id=103,
            status=PublishStatus.SUCCESS.value,
            ext={"scale": {"publish_id": "777"}},
        )
    )
    baas_service.get_publish_progress = Mock(side_effect=RuntimeError("boom"))

    result = svc.sync_scale_progress(103)

    assert result.publish_id == 103
    assert result.status == PublishStatus.SUCCESS
    assert result.message == "Failed to get BaaS scale publish progress: boom"
    baas_service.get_publish_progress.assert_called_once_with(
        publish_id=777,
        include_devices=True,
    )


@pytest.mark.unit
def test_sync_scale_progress_returns_baas_status_and_progress_payload():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))
    publish_service.get_publish_by_id = Mock(
        return_value=_make_publish_record(
            id=104,
            status=PublishStatus.ONLINE_PUB.value,
            ext={"scale": {"publish_id": "888"}},
        )
    )
    progress = {"status": "APPROVING", "device_details": [{"id": 1}]}
    baas_service.get_publish_progress = Mock(return_value=progress)

    result = svc.sync_scale_progress(104)

    assert result.publish_id == 104
    assert result.status == PublishStatus.ONLINE_PUB
    assert result.message == "BaaS scale status: APPROVING"
    assert result.data == progress
    baas_service.get_publish_progress.assert_called_once_with(
        publish_id=888,
        include_devices=True,
    )


@pytest.mark.unit
def test_get_publish_bot_status_success():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))
    publish_service.get_publish_by_id = Mock(
        return_value=_make_publish_record(
            id=201,
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 456}},
        )
    )
    binding = Mock()
    binding.device_id = "BOT-UUID-201"
    publish_service.get_device_binding_by_id = Mock(return_value=binding)
    baas_service.get_bot = Mock(return_value={"bot_uuid": "BOT-UUID-201", "status": "RUNNING"})

    result = svc.get_publish_bot_status(201, PublishStage.ONLINE)

    assert result["publish_id"] == 201
    assert result["stage"] == "online"
    assert result["binding_id"] == 456
    assert result["bot_uuid"] == "BOT-UUID-201"
    assert result["baas_bot_status"] == "RUNNING"


@pytest.mark.unit
def test_get_publish_bot_status_missing_binding():
    from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowServiceError

    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))
    publish_service.get_publish_by_id = Mock(
        return_value=_make_publish_record(id=202, status=PublishStatus.SUCCESS.value, ext={})
    )

    with pytest.raises(PublishFlowServiceError, match="No binding found for the online stage"):
        svc.get_publish_bot_status(202, PublishStage.ONLINE)


@pytest.mark.unit
def test_get_publish_bot_status_publish_not_found():
    from agentclaw.community.core.service_bot.services.bot_publish_service import PublishNotFoundError

    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))
    publish_service.get_publish_by_id = Mock(return_value=None)

    with pytest.raises(PublishNotFoundError, match="Publish order not found: 203"):
        svc.get_publish_bot_status(203, PublishStage.ONLINE)


@pytest.mark.unit
def test_get_publish_bot_status_binding_record_not_found():
    from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowServiceError

    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))
    publish_service.get_publish_by_id = Mock(
        return_value=_make_publish_record(
            id=204,
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 789}},
        )
    )
    publish_service.get_device_binding_by_id = Mock(return_value=None)

    with pytest.raises(PublishFlowServiceError, match="Binding record not found: binding_id=789"):
        svc.get_publish_bot_status(204, PublishStage.ONLINE)


@pytest.mark.unit
def test_get_publish_bot_status_baas_bot_released_fallback():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))
    publish_service.get_publish_by_id = Mock(
        return_value=_make_publish_record(
            id=205,
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 790}},
        )
    )
    binding = Mock()
    binding.device_id = "BOT-UUID-205"
    publish_service.get_device_binding_by_id = Mock(return_value=binding)
    baas_service.get_bot = Mock(return_value={"status": "RELEASED"})

    result = svc.get_publish_bot_status(205, PublishStage.ONLINE)

    assert result["baas_bot_status"] == "RELEASED"


@pytest.mark.unit
def test_get_publish_bot_status_binding_missing_bot_uuid():
    from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowServiceError

    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))
    publish_service.get_publish_by_id = Mock(
        return_value=_make_publish_record(
            id=205,
            status=PublishStatus.SUCCESS.value,
            ext={"binding": {"online": 790}},
        )
    )
    binding = Mock()
    binding.device_id = ""
    publish_service.get_device_binding_by_id = Mock(return_value=binding)

    with pytest.raises(PublishFlowServiceError, match="Binding record missing bot_uuid: binding_id=790"):
        svc.get_publish_bot_status(205, PublishStage.ONLINE)


@pytest.mark.asyncio
async def test_eval_publish_success():
    publish_service = Mock()
    build_service = Mock()
    build_service.release_async = AsyncMock(
        return_value={"bot_uuid": "BOT-EVAL-1", "publish_id": 901, "status": "RUNNING"}
    )
    build_service.generate_request_id = Mock(return_value="rid-eval")
    baas_service = Mock()
    bot_service = Mock()
    bot_service.get_bot.return_value = {
        "bot_id": "bot-source",
        "entity_id": "u1",
        "entity_type": "staff",
        "ext": {},
    }

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))
    svc.approve_baas_publish = Mock(return_value=True)
    publish_service.get_publish_by_id.return_value = _make_publish_record(
        id=301,
        version=3,
        ext={"migration_path": "/tmp/m301", "config_artifact": {"engine_ext": {}}},
    )

    result = await svc.eval_publish(301, "u1", biz_id="biz-001")

    assert result["success"] is True
    assert result["stage"] == "eval"
    assert result["bot_uuid"] == "BOT-EVAL-1"
    assert result["baas_publish_id"] == 901
    assert result["baas_bot_status"] == "RUNNING"
    assert build_service.release_async.await_args.kwargs["publish_stage"] == PublishStage.EVAL
    assert build_service.release_async.await_args.kwargs["version"] == "3"
    assert build_service.release_async.await_args.kwargs["bot"] == bot_service.get_bot.return_value
    assert build_service.release_async.await_args.kwargs["ext_info"] == {"biz_id": "biz-001"}
    assert bot_service.get_bot.return_value["ext"] == {}
    svc.approve_baas_publish.assert_called_once_with(
        baas_publish_id=901,
        operator="u1",
        stage=PublishStage.EVAL,
        request_id="rid-eval",
    )


def test_eval_teardown_success():
    build_service = Mock()
    build_service.generate_request_id = Mock(return_value="rid-destroy-eval")
    baas_service = Mock()
    baas_service.destroy_bot.return_value = {"publish_id": 902}
    svc = _pf(Mock(), build_service, baas_service, Mock(), _arca_router(build_service))
    svc.approve_baas_publish = Mock(return_value=True)

    result = svc.eval_teardown(
        "BOT-EVAL-2",
        operator="u1",
        request_bot={"entity_id": "u1", "entity_type": "staff", "bot_id": "bot-source"},
    )

    assert result == {
        "success": True,
        "bot_uuid": "BOT-EVAL-2",
        "baas_publish_id": 902,
        "message": "Eval environment teardown submitted",
    }
    baas_service.destroy_bot.assert_called_once_with(
        bot_uuid="BOT-EVAL-2",
        operator="u1",
        request_id="rid-destroy-eval",
    )
    svc.approve_baas_publish.assert_called_once_with(
        baas_publish_id=902,
        operator="u1",
        stage=PublishStage.EVAL,
        request_id="rid-destroy-eval",
    )


def test_get_baas_publish_progress_success_default_include_devices_public_name():
    svc = _pf(Mock(), Mock(), Mock(), Mock(), Mock())
    svc._baas_service.get_publish_progress.return_value = {"status": "SUCCESS"}

    result = svc.get_baas_publish_progress(baas_publish_id="123")

    assert result == {"status": "SUCCESS"}
    svc._baas_service.get_publish_progress.assert_called_once_with(
        publish_id=123,
        include_devices=True,
    )


def test_get_baas_publish_progress_success_custom_include_devices_public_name():
    svc = _pf(Mock(), Mock(), Mock(), Mock(), Mock())
    svc._baas_service.get_publish_progress.return_value = {"status": "RUNNING"}

    result = svc.get_baas_publish_progress(baas_publish_id=456, include_devices=False)

    assert result == {"status": "RUNNING"}
    svc._baas_service.get_publish_progress.assert_called_once_with(
        publish_id=456,
        include_devices=False,
    )


def test_get_baas_publish_progress_raises_when_baas_fails_public_name():
    svc = _pf(Mock(), Mock(), Mock(), Mock(), Mock())
    svc._baas_service.get_publish_progress.side_effect = RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        svc.get_baas_publish_progress(baas_publish_id="789")


@pytest.mark.unit
def test_handle_sync_success_skips_destroy_verify_bot_for_teclaw_online_publish():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))

    publish_record = _make_publish_record(
        id=21,
        status=PublishStatus.ONLINE_PUB.value,
        ext={"binding": {PublishStage.ONLINE.value: 200, PublishStage.VERIFY.value: 100}},
    )
    ext = dict(publish_record.ext)
    progress = {"status": "SUCCESS", "device_details": [], "overall_progress": {}}

    bot_service.get_bot = Mock(
        return_value={"bot_id": "bot-source", "active_engine": "teclaw", "ext": {}}
    )
    # teclaw → TeclawProviderBehavior (destroys_verify_bot_on_online=False).
    baas_service.resolve_container_provider.return_value = "teclaw"
    svc._mark_previous_publish_superseded = Mock()
    svc._activate_binding = Mock()
    svc._destroy_bot_by_stage = Mock()

    result = svc._handle_sync_success(
        publish_id=21,
        publish_record=publish_record,
        stage=PublishStage.ONLINE,
        ext=ext,
        baas_publish_id=123,
        progress=progress,
    )

    assert result.status == PublishStatus.SUCCESS
    bot_service.get_bot.assert_called_once_with(bot_id="bot-source", user_id="u1")
    svc._destroy_bot_by_stage.assert_not_called()


@pytest.mark.unit
def test_handle_sync_success_destroys_verify_bot_for_non_teclaw_online_publish():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))

    publish_record = _make_publish_record(
        id=22,
        status=PublishStatus.ONLINE_PUB.value,
        ext={"binding": {PublishStage.ONLINE.value: 200, PublishStage.VERIFY.value: 100}},
    )
    ext = dict(publish_record.ext)
    progress = {"status": "SUCCESS", "device_details": [], "overall_progress": {}}

    bot_service.get_bot = Mock(
        return_value={"bot_id": "bot-source", "active_engine": "openclaw", "ext": {}}
    )
    svc._mark_previous_publish_superseded = Mock()
    svc._activate_binding = Mock()
    svc._destroy_bot_by_stage = Mock()

    result = svc._handle_sync_success(
        publish_id=22,
        publish_record=publish_record,
        stage=PublishStage.ONLINE,
        ext=ext,
        baas_publish_id=123,
        progress=progress,
    )

    assert result.status == PublishStatus.SUCCESS
    bot_service.get_bot.assert_called_once_with(bot_id="bot-source", user_id="u1")
    svc._destroy_bot_by_stage.assert_called_once_with(publish_record, PublishStage.VERIFY)


@pytest.mark.unit
def test_handle_sync_success_verify_stage_updates_validating_and_clears_retry():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))

    publish_record = _make_publish_record(
        id=23,
        status=PublishStatus.VALIDATE_PUB.value,
        ext={"binding": {PublishStage.VERIFY.value: 300}, "retry": True},
    )
    ext = dict(publish_record.ext)
    progress = {"status": "SUCCESS", "device_details": []}

    svc._mark_previous_publish_superseded = Mock()
    svc._activate_binding = Mock()
    svc._destroy_bot_by_stage = Mock()

    result = svc._handle_sync_success(
        publish_id=23,
        publish_record=publish_record,
        stage=PublishStage.VERIFY,
        ext=ext,
        baas_publish_id=456,
        progress=progress,
    )

    assert result.status == PublishStatus.VALIDATING
    assert result.message == "Publish progress synced successfully, status: SUCCESS"
    assert result.data == progress
    assert "retry" not in ext
    publish_service.update_publish_status_with_ext.assert_called_once_with(
        publish_id=23,
        target_status=PublishStatus.VALIDATING,
        ext=ext,
        source_status=PublishStatus.VALIDATE_PUB,
    )
    svc._mark_previous_publish_superseded.assert_called_once_with(
        publish_record, PublishStage.VERIFY, PublishStatus.VALIDATING
    )
    svc._activate_binding.assert_called_once_with(
        ext=ext,
        stage=PublishStage.VERIFY,
        progress=progress,
        baas_status="SUCCESS",
        baas_publish_id=456,
        bot_id=publish_record.source_bot_id,
    )
    bot_service.get_bot.assert_not_called()
    svc._destroy_bot_by_stage.assert_not_called()


@pytest.mark.unit
def test_handle_sync_success_online_publish_raises_when_bot_missing():
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))

    publish_record = _make_publish_record(
        id=24,
        status=PublishStatus.ONLINE_PUB.value,
        ext={"binding": {PublishStage.ONLINE.value: 400, PublishStage.VERIFY.value: 401}},
    )
    ext = dict(publish_record.ext)
    progress = {"status": "SUCCESS"}

    bot_service.get_bot = Mock(return_value=None)
    svc._mark_previous_publish_superseded = Mock()
    svc._activate_binding = Mock()
    svc._destroy_bot_by_stage = Mock()

    from agentclaw.community.core.service_bot.services.publish_flow_service import PublishFlowServiceError

    with pytest.raises(PublishFlowServiceError, match="Bot does not exist: bot-source"):
        svc._handle_sync_success(
            publish_id=24,
            publish_record=publish_record,
            stage=PublishStage.ONLINE,
            ext=ext,
            baas_publish_id=789,
            progress=progress,
        )

    publish_service.update_publish_status_with_ext.assert_called_once()
    svc._mark_previous_publish_superseded.assert_called_once_with(
        publish_record, PublishStage.ONLINE, PublishStatus.SUCCESS
    )
    svc._activate_binding.assert_called_once()
    svc._destroy_bot_by_stage.assert_not_called()


@pytest.mark.unit
def test_handle_sync_success_online_publish_logs_warning_when_destroy_verify_fails(caplog):
    publish_service = Mock()
    build_service = Mock()
    baas_service = Mock()
    bot_service = Mock()

    svc = _pf(publish_service, build_service, baas_service, bot_service, _arca_router(build_service))

    publish_record = _make_publish_record(
        id=25,
        status=PublishStatus.ONLINE_PUB.value,
        ext={"binding": {PublishStage.ONLINE.value: 500, PublishStage.VERIFY.value: 501}},
    )
    ext = dict(publish_record.ext)
    progress = {"status": "SUCCESS"}

    bot_service.get_bot = Mock(
        return_value={"bot_id": "bot-source", "active_engine": "openclaw", "ext": {}}
    )
    svc._mark_previous_publish_superseded = Mock()
    svc._activate_binding = Mock()
    svc._destroy_bot_by_stage = Mock(side_effect=RuntimeError("destroy failed"))

    with caplog.at_level("WARNING"):
        result = svc._handle_sync_success(
            publish_id=25,
            publish_record=publish_record,
            stage=PublishStage.ONLINE,
            ext=ext,
            baas_publish_id=790,
            progress=progress,
        )

    assert result.status == PublishStatus.SUCCESS
    svc._destroy_bot_by_stage.assert_called_once_with(publish_record, PublishStage.VERIFY)
    assert publish_service.update_publish_status_with_ext.called


# ===========================================================================
# Characterization tests (Task 1) — pin CURRENT behavior of the thin-coverage
# public entry points (process / describe_publish / advance_publish_progress /
# restart_bot / retry) before the publish-flow refactor, so the restructure can
# be shown to preserve behavior. Deliberately dispatch-level (collaborators
# mocked): they assert which branch/handler current code takes, not deep effects
# already covered elsewhere.
# ===========================================================================


def _svc_with_record(
    record, *, build_service=None, baas_service=None, bot_service=None,
    task_queue_service=None,
):
    """PublishFlowService whose publish_service.get_publish_by_id returns `record`."""
    publish_service = Mock()
    publish_service.get_publish_by_id.return_value = record
    build_service = build_service or Mock()
    kw = {}
    if task_queue_service is not None:
        kw["task_queue_service"] = task_queue_service
    svc = _pf(
        publish_service,
        build_service,
        baas_service or Mock(),
        bot_service or Mock(),
        _arca_router(build_service),
        **kw,
    )
    return svc, publish_service


_CREATE_TASK = (
    "agentclaw.community.core.service_bot.services.publish_flow.restart_mixin.asyncio.create_task"
)


# ---- process() dispatch ----------------------------------------------------

@pytest.mark.asyncio
async def test_process_draft_advances_to_building_and_enqueues_verify_flow():
    record = _make_publish_record(status=PublishStatus.DRAFT.value)
    tq = Mock()
    svc, publish_service = _svc_with_record(record, task_queue_service=tq)
    result = await svc.process(publish_id=1, operator="op")
    # User-driven advance: move DRAFT -> BUILDING synchronously under the lock,
    # then enqueue the durable verify_flow task for the remainder.
    publish_service.update_publish_status.assert_called_once_with(
        1, PublishStatus.BUILDING.value, PublishStatus.DRAFT.value
    )
    tq.enqueue.assert_called_once()
    assert tq.enqueue.call_args.args[0] == "service_bot.publish.verify_flow"
    assert result.status == PublishStatus.BUILDING
    assert result.action == "process"
    assert "Build started" in result.message


@pytest.mark.asyncio
async def test_process_draft_lost_race_describes_without_enqueue():
    # A concurrent submit already advanced DRAFT -> BUILDING: the CAS raises, so
    # this call reports progress instead of enqueuing a second build.
    record = _make_publish_record(status=PublishStatus.DRAFT.value)
    tq = Mock()
    svc, publish_service = _svc_with_record(record, task_queue_service=tq)
    publish_service.update_publish_status.side_effect = PublishNotFoundError("lost")
    publish_service.get_publish_by_id.return_value = _make_publish_record(
        status=PublishStatus.BUILDING.value
    )
    result = await svc.process(publish_id=1, operator="op")
    tq.enqueue.assert_not_called()
    assert result.status == PublishStatus.BUILDING
    assert "Build in progress" in result.message


@pytest.mark.asyncio
async def test_process_validating_advances_to_online_pub_and_enqueues_release():
    record = _make_publish_record(status=PublishStatus.VALIDATING.value)
    tq = Mock()
    svc, publish_service = _svc_with_record(record, task_queue_service=tq)
    result = await svc.process(publish_id=1, operator="op")
    publish_service.update_publish_status.assert_called_once_with(
        1, PublishStatus.ONLINE_PUB.value, PublishStatus.VALIDATING.value
    )
    tq.enqueue.assert_called_once()
    assert tq.enqueue.call_args.args[0] == "service_bot.publish.online_release"
    assert result.status == PublishStatus.ONLINE_PUB
    assert result.bot_uuid is None  # no synchronous ids in the async-submit response
    assert "submitted" in result.message


@pytest.mark.asyncio
async def test_process_built_is_describe_only_no_enqueue():
    record = _make_publish_record(status=PublishStatus.BUILT.value)
    tq = Mock()
    svc, _ = _svc_with_record(record, task_queue_service=tq)
    result = await svc.process(publish_id=1, operator="op")
    assert result.status == PublishStatus.BUILT
    tq.enqueue.assert_not_called()  # verify_flow task owns BUILT -> VALIDATE_PUB


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,fragment",
    [
        (PublishStatus.BUILDING, "Build in progress"),
        (PublishStatus.VALIDATE_PUB, "Verify environment publish in progress"),
        (PublishStatus.ONLINE_PUB, "Online publish in progress"),
        (PublishStatus.SUCCESS, "Publish complete"),
    ],
)
async def test_process_describe_only_states_do_not_mutate(status, fragment):
    record = _make_publish_record(status=status.value)
    tq = Mock()
    svc, publish_service = _svc_with_record(record, task_queue_service=tq)
    result = await svc.process(publish_id=1, operator="op")
    assert result.status == status
    assert fragment in result.message
    tq.enqueue.assert_not_called()
    publish_service.update_publish_status_with_ext.assert_not_called()
    publish_service.update_publish_status.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,fragment",
    [
        (PublishStatus.UPGRADED, "Superseded"),
        (PublishStatus.RELEASED, "Taken offline"),
    ],
)
async def test_process_terminal_states_describe_instead_of_raising(status, fragment):
    # A record can reach a terminal state concurrently (superseded by a newer
    # publish / taken offline) while a process() call is in flight. process()
    # describes these gracefully via the _SYNC_ONLY fallback instead of raising
    # PublishStatusInvalidError.
    record = _make_publish_record(status=status.value)
    tq = Mock()
    svc, publish_service = _svc_with_record(record, task_queue_service=tq)
    result = await svc.process(publish_id=1, operator="op")
    assert result.status == status
    assert fragment in result.message
    tq.enqueue.assert_not_called()
    publish_service.update_publish_status.assert_not_called()


@pytest.mark.asyncio
async def test_process_failed_reports_error_message():
    record = _make_publish_record(
        status=PublishStatus.FAILED.value, ext={"error_message": "boom"}
    )
    svc, _ = _svc_with_record(record)
    result = await svc.process(publish_id=1, operator="op")
    assert result.status == PublishStatus.FAILED
    assert "boom" in result.message


@pytest.mark.asyncio
async def test_process_not_found_raises():
    svc, _ = _svc_with_record(None)
    with pytest.raises(PublishNotFoundError):
        await svc.process(publish_id=999, operator="op")


# ---- describe_publish() / advance_publish_progress() dispatch --------------

# The user-facing /sync endpoint talks to describe_publish (read-only status
# report); advance_publish_progress is the poll task's engine.

def test_describe_publish_not_found_raises():
    svc, _ = _svc_with_record(None)
    with pytest.raises(PublishNotFoundError):
        svc.describe_publish(publish_id=999)


@pytest.mark.parametrize(
    "status,fragment",
    [
        (PublishStatus.DRAFT, "not started"),
        (PublishStatus.BUILDING, "Build in progress"),
        (PublishStatus.VALIDATE_PUB, "Verify environment publish in progress"),
        (PublishStatus.VALIDATING, "awaiting online publish"),
        (PublishStatus.ONLINE_PUB, "Online publish in progress"),
        (PublishStatus.SUCCESS, "Publish complete"),
        (PublishStatus.UPGRADED, "Superseded"),
        (PublishStatus.RELEASED, "Taken offline"),
    ],
)
def test_describe_publish_is_read_only(status, fragment):
    record = _make_publish_record(
        status=status.value, ext={"publish": {"verify": 500}}
    )
    svc, publish_service = _svc_with_record(record)
    svc.get_baas_publish_progress = Mock()
    result = svc.describe_publish(publish_id=1)
    assert result.status == status
    assert fragment in result.message
    # Read-only: no BaaS query, no status/ext writes.
    svc.get_baas_publish_progress.assert_not_called()
    publish_service.update_publish_status_with_ext.assert_not_called()
    publish_service.update_publish_status.assert_not_called()


def test_describe_publish_failed_reports_error_message():
    record = _make_publish_record(
        status=PublishStatus.FAILED.value, ext={"error_message": "boom"}
    )
    svc, _ = _svc_with_record(record)
    result = svc.describe_publish(publish_id=1)
    assert result.status == PublishStatus.FAILED
    assert "boom" in result.message


def test_advance_publish_progress_not_found_raises():
    svc, _ = _svc_with_record(None)
    with pytest.raises(PublishNotFoundError):
        svc.advance_publish_progress(publish_id=999)


def test_advance_publish_progress_retry_flag_redirects_to_restart_sync():
    record = _make_publish_record(
        status=PublishStatus.VALIDATE_PUB.value,
        ext={"retry": True, "source_status": PublishStatus.VALIDATE_PUB.value},
    )
    svc, _ = _svc_with_record(record)
    sentinel = Mock()
    svc.sync_restart_progress = Mock(return_value=sentinel)
    assert svc.advance_publish_progress(publish_id=1) is sentinel
    svc.sync_restart_progress.assert_called_once_with(1)


def test_advance_publish_progress_non_wait_state_is_noop_report():
    # TOCTOU catch-all: the record left the *_PUB wait state between the poll's
    # status check and the engine's re-read → nothing to drive.
    record = _make_publish_record(status=PublishStatus.FAILED.value)
    svc, publish_service = _svc_with_record(record)
    svc.get_baas_publish_progress = Mock()
    result = svc.advance_publish_progress(publish_id=1)
    assert "does not support progress sync" in result.message
    svc.get_baas_publish_progress.assert_not_called()
    publish_service.update_publish_status_with_ext.assert_not_called()


def test_advance_publish_progress_no_baas_publish_id_returns_guard():
    record = _make_publish_record(status=PublishStatus.VALIDATE_PUB.value, ext={})
    svc, _ = _svc_with_record(record)
    result = svc.advance_publish_progress(publish_id=1)
    assert "not found" in result.message


def test_advance_publish_progress_success_dispatches_handle_success():
    record = _make_publish_record(
        status=PublishStatus.VALIDATE_PUB.value, ext={"publish": {"verify": 500}}
    )
    svc, _ = _svc_with_record(record)
    svc.get_baas_publish_progress = Mock(return_value={"status": "SUCCESS"})
    sentinel = Mock()
    svc._handle_sync_success = Mock(return_value=sentinel)
    assert svc.advance_publish_progress(publish_id=1) is sentinel
    svc._handle_sync_success.assert_called_once()


def test_advance_publish_progress_failed_baas_dispatches_handle_failure():
    record = _make_publish_record(
        status=PublishStatus.VALIDATE_PUB.value, ext={"publish": {"verify": 500}}
    )
    svc, _ = _svc_with_record(record)
    svc.get_baas_publish_progress = Mock(return_value={"status": "FAILED"})
    sentinel = Mock()
    svc._handle_sync_failure = Mock(return_value=sentinel)
    assert svc.advance_publish_progress(publish_id=1) is sentinel
    svc._handle_sync_failure.assert_called_once()


def test_advance_publish_progress_other_status_reports_without_mutation():
    record = _make_publish_record(
        status=PublishStatus.VALIDATE_PUB.value, ext={"publish": {"verify": 500}}
    )
    svc, publish_service = _svc_with_record(record)
    svc.get_baas_publish_progress = Mock(return_value={"status": "PENDING"})
    result = svc.advance_publish_progress(publish_id=1)
    assert "PENDING" in result.message
    publish_service.update_publish_status_with_ext.assert_not_called()


# ---- sync_restart_progress() dispatch (previously zero coverage) -----------

def test_sync_restart_progress_not_found_raises():
    svc, _ = _svc_with_record(None)
    with pytest.raises(PublishNotFoundError):
        svc.sync_restart_progress(publish_id=999)


def test_sync_restart_progress_unsupported_status_returns_guard():
    record = _make_publish_record(status=PublishStatus.BUILT.value)
    svc, _ = _svc_with_record(record)
    result = svc.sync_restart_progress(publish_id=1)
    assert "does not support querying restart progress" in result.message


def test_sync_restart_progress_no_handle_returns_guard():
    record = _make_publish_record(status=PublishStatus.ONLINE_PUB.value, ext={})
    svc, _ = _svc_with_record(record)
    result = svc.sync_restart_progress(publish_id=1)
    assert "not found" in result.message


def test_sync_restart_progress_success_dispatches_handle_success():
    record = _make_publish_record(
        status=PublishStatus.ONLINE_PUB.value, ext={"restart": {"online": 700}}
    )
    svc, _ = _svc_with_record(record)
    svc.get_baas_publish_progress = Mock(return_value={"status": "SUCCESS"})
    sentinel = Mock()
    svc._handle_sync_success = Mock(return_value=sentinel)
    assert svc.sync_restart_progress(publish_id=1) is sentinel
    svc._handle_sync_success.assert_called_once()


def test_sync_restart_progress_stable_status_still_fails_on_baas_failed():
    # VALIDATING is a stable state → no forward advance, but a BaaS FAILED still
    # routes to _handle_sync_failure.
    record = _make_publish_record(
        status=PublishStatus.VALIDATING.value, ext={"restart": {"verify": 700}}
    )
    svc, _ = _svc_with_record(record)
    svc.get_baas_publish_progress = Mock(return_value={"status": "FAILED"})
    sentinel = Mock()
    svc._handle_sync_failure = Mock(return_value=sentinel)
    assert svc.sync_restart_progress(publish_id=1) is sentinel
    svc._handle_sync_failure.assert_called_once()


# ---- restart_bot() submit path ---------------------------------------------

def test_restart_bot_not_found_returns_failure():
    svc, _ = _svc_with_record(None)
    result = svc.restart_bot(publish_id=999, operator="op")
    assert result["success"] is False


def test_restart_bot_unsupported_status_returns_failure():
    record = _make_publish_record(status=PublishStatus.DRAFT.value)
    svc, _ = _svc_with_record(record)
    result = svc.restart_bot(publish_id=1, operator="op")
    assert result["success"] is False
    assert "does not support restart operation" in result["message"]


def test_restart_bot_missing_binding_returns_failure():
    record = _make_publish_record(status=PublishStatus.SUCCESS.value, ext={})
    svc, _ = _svc_with_record(record)
    result = svc.restart_bot(publish_id=1, operator="op")
    assert result["success"] is False


def test_restart_bot_success_schedules_async_and_returns_stage():
    record = _make_publish_record(
        status=PublishStatus.SUCCESS.value,
        ext={"binding": {"online": 42}, "migration_path": "/m"},
    )
    svc, publish_service = _svc_with_record(record)
    publish_service.get_device_binding_by_id.return_value = Mock(device_id="BOT-x")
    svc._bot_service.get_bot = Mock(return_value={"bot_id": "bot-source"})
    svc._restart_bot_async = Mock()
    with patch(_CREATE_TASK) as create_task:
        result = svc.restart_bot(publish_id=1, operator="op")
    create_task.assert_called_once()
    assert result["success"] is True
    assert result["stage"] == PublishStage.ONLINE.value
    assert result["bot_uuid"] == "BOT-x"


# ---- retry() across source_status ------------------------------------------

@pytest.mark.asyncio
async def test_retry_rejects_non_failed_status():
    record = _make_publish_record(status=PublishStatus.VALIDATING.value)
    svc, _ = _svc_with_record(record)
    with pytest.raises(PublishFlowServiceError):
        await svc.retry(publish_id=1, operator="op")


@pytest.mark.asyncio
async def test_retry_missing_source_status_raises():
    record = _make_publish_record(status=PublishStatus.FAILED.value, ext={})
    svc, _ = _svc_with_record(record)
    with pytest.raises(PublishFlowServiceError):
        await svc.retry(publish_id=1, operator="op")


@pytest.mark.asyncio
async def test_retry_from_online_pub_recorded_calls_restart():
    # Online release already recorded (ext.publish.online) → a BaaS-wait failure →
    # retry restarts the BaaS publish.
    record = _make_publish_record(
        status=PublishStatus.FAILED.value,
        ext={
            "source_status": PublishStatus.ONLINE_PUB.value,
            "publish": {"online": 9},
        },
    )
    svc, _ = _svc_with_record(record)
    svc.restart_bot = Mock(return_value={"success": True})
    result = await svc.retry(publish_id=1, operator="op")
    svc.restart_bot.assert_called_once()
    assert result.action == "restart"


@pytest.mark.asyncio
async def test_retry_from_online_pub_not_recorded_reenqueues_online_release():
    # Online release NOT recorded → the release work itself failed → retry re-runs
    # the online_release task rather than restarting a bot that was never created.
    record = _make_publish_record(
        status=PublishStatus.FAILED.value,
        ext={"source_status": PublishStatus.ONLINE_PUB.value},
    )
    tq = Mock()
    svc, _ = _svc_with_record(record, task_queue_service=tq)
    svc.restart_bot = Mock()
    result = await svc.retry(publish_id=1, operator="op")
    svc.restart_bot.assert_not_called()
    tq.enqueue.assert_called_once()
    assert tq.enqueue.call_args.args[0] == "service_bot.publish.online_release"
    assert result.action == "process"


@pytest.mark.asyncio
async def test_retry_from_built_source_enqueues_verify_flow():
    # retry must enqueue directly (NOT via process(), which is describe-only on BUILT)
    record = _make_publish_record(
        status=PublishStatus.FAILED.value,
        ext={"source_status": PublishStatus.BUILT.value},
    )
    tq = Mock()
    svc, _ = _svc_with_record(record, task_queue_service=tq)
    result = await svc.retry(publish_id=1, operator="op")
    tq.enqueue.assert_called_once()
    assert tq.enqueue.call_args.args[0] == "service_bot.publish.verify_flow"
    assert result.action == "process"
