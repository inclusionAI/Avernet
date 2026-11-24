"""Pin friend file operations to a replica without rewriting builder identities."""

import asyncio
from types import SimpleNamespace

import pytest

from agentclaw.community.adapters.http.openapi_v1.engine_runtime.sessions.dependencies_session_files import OpenApiSessionFileAdapter
from agentclaw.community.core.bot_management.token_vault import TokenVault
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord
from agentclaw.community.core.devices.services.conn_info_builders.baas_builder import BaasConnInfoBuilder
from agentclaw.community.core.devices.services.device_context_resolver import DeviceContextResolver
from agentclaw.community.core.runtime_binding.models import RuntimeBindingRequest
from agentclaw.community.core.runtime_binding.service import RuntimeBindingResolutionService
from agentclaw.community.core.runtime_binding.container_locator import RuntimeContainerLocator
from agentclaw.community.core.session_resources.runtime import SessionFileRuntimeResolver
from agentclaw.community.core.session_resources.engine_files import EngineSessionFilesService
from agentclaw.community.core.session_resources.materialization import SessionResourceMaterializeHandler
from agentclaw.community.core.task_queue.types import Complete
from agentclaw.community.plugin_api.device_adapter_transport import DeviceAdapterStreamResponse
from tests.community.core.runtime_binding.test_service import (
    BOT, CALLER, OWNER, _BindingRepository, _BotRepository,
    _CallerInstanceRepository, _PublishRepository,
)
from tests.community.core.session_resources.test_service import _Queue, _service


class _Bots(_BotRepository):
    def get_by_binding_id(self, binding_id):
        return None

    def get_by_id_and_entity(self, bot_id, entity_id):
        assert (bot_id, entity_id) == (BOT, OWNER)
        return self.bot


class _Baas:
    def __init__(self):
        self.calls = []

    def list_runtime_devices(self, binding, *, device_uuid=None):
        assert binding.id == 41
        assert device_uuid in (None, "DEVICE-B")
        return ({"provider_device_id": "device-1", "device_uuid": "DEVICE-B"},)

    def get_ws_info(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            ws_url="wss://baas.example.test/proxypass/engine/api/openclaw/ws",
            token="<test-chain-token>", target="engine", paas_device_id="device-1",
            baas_base_url="https://baas.example.test", engine_port=20003,
            tenant="tenant-1", bot_uuid="device-1",
        )


class _Staff:
    def __init__(self):
        self.callers = []

    def get_profile_by_work_no(self, *, work_no):
        self.callers.append(work_no)
        return SimpleNamespace(work_no=work_no, nick_name=f"creator {work_no}")


class _Transport:
    def __init__(self):
        self.calls = []

    async def invoke(self, connection, method, path, **kwargs):
        self.calls.append((connection, method, path, kwargs))
        if path == "/api/resource-materializations":
            return {"accepted": True}
        if method == "DELETE":
            return {"deleted": True, "resource_id": path.rsplit("/", 1)[-1]}
        return {"files": []}

    async def stream(self, connection, method, path, **kwargs):
        self.calls.append((connection, method, path, kwargs))

        async def chunks():
            yield b"friend file"

        async def close():
            pass

        return DeviceAdapterStreamResponse(200, {}, chunks(), close)


@pytest.mark.parametrize("actor", [CALLER, OWNER], ids=["friend-self", "owner-inspection"])
@pytest.mark.parametrize("props", [{"bolt_id": BOT}, {}], ids=["provisioned-props", "legacy-props"])
def test_real_upload_materialization_and_file_operations_keep_selected_replica(actor, props, caplog):
    binding = DeviceBindingRecord(
        id=41, entity_id=OWNER, entity_type="staff", device_id="device-1",
        device_provider="baas", env="dev", device_props=props, status="ACTIVE",
        apply_reason=f"caller_instance:{BOT}", applied_by=CALLER,
        release_reason=None, released_by=None, released_at=None,
        last_alive_at=None, gmt_create=None, gmt_modified=None,
    )
    bots = _Bots({"id": 3, "bot_id": BOT, "bot_type": "service", "call_type": "caller",
                  "active_engine": "openclaw", "binding_id": 12, "owner_id": OWNER})
    bindings = _BindingRepository({41: binding})
    runtime = RuntimeBindingResolutionService(
        bot_repository=bots, publish_repository=_PublishRepository(), binding_repository=bindings,
        caller_instance_repository=_CallerInstanceRepository({"status": "success", "ext": {"binding_id": 41}}),
        environment_provider=lambda: "dev",
    )
    baas = _Baas()
    builder = BaasConnInfoBuilder(baas, bots, bindings)
    resolver = DeviceContextResolver(bindings, bots, None, builder, None, None)
    transport = _Transport()
    staff = _Staff()
    queue = _Queue()
    resources, repository, _ = _service(queue, resolver=resolver, transport=transport, staff_dept=staff)
    runtime = SessionFileRuntimeResolver(RuntimeContainerLocator(
        bots=bots, runtime_bindings=runtime, binding_repository=bindings, baas_service=baas,
    ))
    files = EngineSessionFilesService(runtime, resolver, transport)
    adapter = OpenApiSessionFileAdapter(resources, runtime, files)
    target = RuntimeBindingRequest(BOT, OWNER, actor, "online")
    with caplog.at_level("INFO"):
        intent, = adapter.create_upload_intents(
            actor_user_id=actor, owner_id=OWNER, bot_id=BOT, session_key="friend-session",
            stage="online", engine_type="openclaw", files=[("friend.txt", 11, None)],
            session_user_id=CALLER,
        )
        assert baas.calls[-1] == {"bind_id": 41, "device_affinity": OWNER, "device_uuid": "DEVICE-B"}
        assert intent.resource.owner_id == OWNER
        assert intent.resource.binding_id == 41
        assert intent.resource.device_uuid == "DEVICE-B"
        assert intent.resource.creator_name == f"creator {actor}"
        assert staff.callers == [actor]
        completed = adapter.complete_upload(
            owner_id=OWNER, bot_id=BOT, session_key="friend-session",
            resource_id=intent.resource.resource_id, transfer_id=intent.resource.transfer_id,
        )
        handler = SessionResourceMaterializeHandler(
            resolver, transport, repository, TokenVault(master_key="test-master-key"),
        )
        assert isinstance(handler.handle(queue.calls[0][1]), Complete)
        assert transport.calls[-1][2] == "/api/resource-materializations"
        assert transport.calls[-1][3]["body"]["creator_name"] == f"creator {actor}"
        assert completed.owner_id == OWNER and completed.binding_id == 41

        async def read_and_delete():
            assert await adapter.list_ready(target=target, session_key="friend-session", session_user_id=CALLER) == []
            for disposition in ("inline", "attachment"):
                response = await adapter.open_content(
                    target=target, session_key="friend-session", resource_id=intent.resource.resource_id,
                    disposition=disposition, session_user_id=CALLER,
                )
                assert b"".join([chunk async for chunk in response.body]) == b"friend file"
                await response.close()
            await adapter.delete(target=target, session_key="friend-session",
                                 resource_id=intent.resource.resource_id, session_user_id=CALLER)

        asyncio.run(read_and_delete())
        assert len(transport.calls) == 5
        assert all(connection["device_uuid"] == "DEVICE-B" and connection["binding_id"] == 41
                   for connection, *_ in transport.calls)
        # Upload and materialization retain the record owner. Friend reads use
        # the resolved session subject; the builder preserves each input.
        assert [call["device_affinity"] for call in baas.calls] == [OWNER, OWNER] + [CALLER] * 4
        assert all(call["bind_id"] == 41 and call["device_uuid"] == "DEVICE-B" for call in baas.calls)
        context = resolver.resolve_for_binding(41, actor, bot_id=BOT)
        assert context.user_id == actor
        assert context.conn_info["device_affinity"] == actor
    assert "<test-chain-token>" not in caplog.text
