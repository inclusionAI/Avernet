"""Teclaw platform-managed manifest creation flow."""

import pytest

from agentclaw.community.core.bot_config_manifest.managed_files import (
    CATEGORY_IDENTITY,
    ManagedFileScope,
    ManagedFilesStore,
)
from agentclaw.community.core.task_queue.types import Complete, Fail
from tests.community.endpoints.test_openapi_create_with_manifest import (
    _HEADERS,
    _OWNER,
    _QUERY,
    _TECLAW_DOCUMENT,
    _Worker,
    _body,
    _poll,
    _seed_verifier,
    _stand_in_for_teclaw_platform_managed,
)


@pytest.mark.skip(
    reason=(
        "CI can miss the transient CREATING phase because the simulated worker "
        "claims up to 10 tasks per turn; re-enable after making phase observation deterministic"
    )
)
def test_a_teclaw_creation_walks_record_phase_container_ready(client, world):
    """`AWAITING_AUTHORIZATION → CREATING → APPLYING → CREATING → READY`, with
    the report from the single phase, and the phase's files in the platform's
    store before the container was provisioned."""
    _seed_verifier(world)
    order = _stand_in_for_teclaw_platform_managed(world)
    worker = _Worker(world)

    submitted = client.post(
        "/openapi/v1/bots/with-manifest",
        params=_QUERY,
        headers=_HEADERS,
        json=_body(_TECLAW_DOCUMENT, engine="teclaw", cluster="ANDC"),
    )
    assert submitted.status_code == 202, submitted.text
    bot_id = submitted.json()["data"]["bot_id"]
    assert _poll(client, bot_id).json()["data"]["state"] == "AWAITING_AUTHORIZATION"

    states: list[str] = []
    seen_at_provision: dict[str, list[str]] = {}
    store = world.get(ManagedFilesStore)
    scope = ManagedFileScope(entity_type="staff", entity_id=_OWNER, bot_id=bot_id)

    outcome = None
    for _ in range(8):
        turned = worker.turn()
        if "provision" in order and "identity" not in seen_at_provision:
            seen_at_provision["identity"] = [r.rel_path for r in store.list(scope, category=CATEGORY_IDENTITY)]
        state = _poll(client, bot_id).json()["data"]["state"]
        if not states or states[-1] != state:
            states.append(state)
        outcome = next((o for o in turned if isinstance(o, (Complete, Fail))), None)
        if outcome is not None:
            break
    assert isinstance(outcome, Complete), (outcome, states, order)

    assert order == ["record", "provision"], order
    assert states == ["CREATING", "APPLYING", "CREATING", "READY"], states
    assert seen_at_provision["identity"] == ["identity/RULES.md"], (
        "the platform's copy must exist when provisioning composes the first artifact"
    )
    ready = _poll(client, bot_id).json()["data"]
    assert ready["bot"]["bot_id"] == bot_id and ready["bot"]["status"] == "ACTIVE"
    assert ready["apply"]["result"] == "SUCCEEDED"
    assert [e["category"] for e in ready["apply"]["entries"]] == ["identity"]
