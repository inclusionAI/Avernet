"""Seed helpers for the ``devices`` domain.

``make_active_local_device`` inserts an ACTIVE local device binding so
the real ``CronRelayService`` (and any relay) can resolve a usable
``conn_info`` via ``DeviceService.get_device_connection_v2`` — reaching
the in-memory adapter transport — without a live device process.
"""
from __future__ import annotations

from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from tests.community.framework.world import World


def make_active_local_device(
    world: World,
    *,
    owner_id: str = "u_owner",
    device_id: str = "local_dev_test",
    adapter_port: int = 20003,
    openclaw_port: int = 18789,
    env: str = "dev",
) -> int:
    """Insert an ACTIVE local device binding and return its ``binding_id``.

    The binding carries ``device_provider="local"`` and an
    ``adapter_port`` in ``device_props`` — the two things
    ``LocalDeviceService._compose_device_conn_info`` needs to produce a
    direct ``http://localhost:{port}`` connection. ``entity_id`` is the
    owner so the device permission check (``entity_id == staff_id``)
    passes for the owner identity the relay resolves.
    """
    repo = world.get(DeviceBindingRepository)
    return repo.insert_binding(
        entity_id=owner_id,
        entity_type="staff",
        device_id=device_id,
        device_provider="local",
        env=env,
        device_props={"adapter_port": adapter_port, "openclaw_port": openclaw_port},
        status="ACTIVE",
        apply_reason="test seed",
        applied_by=owner_id,
    )


def make_active_arca_device(
    world: World,
    *,
    owner_id: str = "u_owner",
    device_id: str = "arca_dev_test",
    sandbox_id: str = "sbx-test-123",
    env: str = "dev",
) -> int:
    """Insert an ACTIVE Arca-style device binding and return its ``binding_id``.

    Historical context — this factory was designed for the *legacy*
    ``DeviceFilesystemDispatcher.for_bot(bot_id, user_id)`` path, where
    callers could keep the binding's ``device_provider="arca"`` (so the
    HTTP route's ``arca_utils.get_device_info`` reported arca) while
    separately driving the ``LocalDeviceAccessor.get_connection_info``
    boundary to report ``"local"`` — that override is what made
    ``for_bot`` build a real ``LocalDeviceFileSystem`` for the disk-side
    assertion.

    After the resolver migration (Phase 2 Task 3),
    ``DeviceFilesystemDispatcher.dispatch(ctx)`` routes purely by
    ``ctx.provider``, which the resolver reads from
    ``binding.device_provider``. The ``LocalDeviceAccessor`` override is no
    longer in the dispatch path, so the binding's own ``device_provider``
    is what selects the FS impl. To preserve the same disk-write
    assertion shape, the binding now uses ``"local"`` — resolver returns
    a local ``DeviceContext``, dispatch builds the real
    ``LocalDeviceFileSystem``, and the write lands on disk where the
    tests read it back.

    The ``sandbox_id`` in ``device_props`` is kept so any future arca-
    branch read paths (``arca_utils.get_device_info`` →
    ``("arca", sandbox_id)``) still get a populated tuple if/when callers
    introduce arca-specific assertions.

    ``DeviceContextResolver.resolve_for_bot`` reads
    ``DeviceBindingRepository.get_active_by_bot_and_owner`` which JOINs
    ``ac_bots.binding_id == ac_entity_device_binding.id`` — callers must
    insert the bot with ``binding_id`` set to the returned int so the
    resolver can find the active binding.
    """
    repo = world.get(DeviceBindingRepository)
    return repo.insert_binding(
        entity_id=owner_id,
        entity_type="staff",
        device_id=device_id,
        device_provider="local",
        env=env,
        device_props={"sandbox_id": sandbox_id},
        status="ACTIVE",
        apply_reason="test seed",
        applied_by=owner_id,
    )
