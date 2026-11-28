"""Deployments created before mutation scopes must retain their immutable identity."""

import hashlib
import json
from dataclasses import asdict, replace

import pytest

from avernet_evolution.composition import compose
from avernet_evolution.core.evolution_run import EvolutionStore
from avernet_evolution.core.evolution_strategy import registration_from_json
from avernet_evolution.identity import _encode, validate_identities
from avernet_evolution.local import build_local_platform


@pytest.mark.asyncio
async def test_pre_scope_deployment_identity_restarts_unchanged(settings):
    platform = await build_local_platform(settings)
    store = EvolutionStore(settings.database)
    store.conn.execute("CREATE TABLE evolution_identity (name TEXT PRIMARY KEY, digest TEXT NOT NULL)")
    for record in await platform.registry.list():
        registration = asdict(record.registration)
        registration.pop("mutation_capability")
        material = {
            "registration": registration,
            "definitions": [asdict(d) for d in record.definitions],
            "worker_image": record.registration.runtime.image
            if record.registration.runtime.kind == "job_worker"
            else platform.worker_image,
        }
        digest = hashlib.sha256(json.dumps(material, sort_keys=True, default=_encode).encode()).hexdigest()
        store.conn.execute(
            "INSERT INTO evolution_identity VALUES (?, ?)",
            (f"strategy:{record.registration.id}@{record.registration.version}", digest),
        )
    store.conn.commit()
    store.conn.close()
    runtime = await compose(settings, platform)
    await runtime.close()


@pytest.mark.asyncio
async def test_new_capability_is_serializable_and_immutable_across_deployments(settings):
    platform = await build_local_platform(settings)
    registration = registration_from_json(
        {
            "id": "reference/behavior-refinement",
            "version": "1.0.0",
            "runtime": {"kind": "in_process", "package": "reference.behavior"},
            "mutation_capability": [{"gene": "persona", "include": "all", "operations": ["update"]}],
        }
    )
    await platform.registry.register(registration, {})
    runtime = await compose(settings, platform)
    try:
        other = await build_local_platform(settings)
        await other.registry.register(replace(registration, mutation_capability=None), {})
        with pytest.raises(ValueError, match="immutable deployment identity"):
            await validate_identities(runtime.store, other, settings.signing_key(), control_scope="local")
        await other.close()
    finally:
        await runtime.close()
