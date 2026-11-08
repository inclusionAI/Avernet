"""The RSI-03 story over the real app: capture → patch → diff → move ``draft`` → promote.

Every request goes through the public router with a signed principal and the
production DI graph on the test profile (SQLite, local device filesystem), so
the story exercises the same wiring singlebox would. It stops where the spec
says it stops: the only delivery seam bound today refuses the promotion as
``not_appliable`` naming RSI-04, before any ref moves.
"""

from __future__ import annotations

import asyncio
import time

import jwt
import pytest
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.services.identity import IdentityService
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.devices import DeviceBindingRepository
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config

_OWNER = "genome_e2e_user"
_KEY = "genome-flow-framework-signing-key-at-least-32-bytes"
_SOUL = "# Soul\n\n## Tone\n\nWarm and brief.\n\n## Escalation\n\nEscalate when the customer asks.\n"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _name: str) -> _Secret:
        return _Secret()


def _principal() -> str:
    now = int(time.time())
    claims = {
        "iss": "gateway",
        "aud": "backend",
        "iat": now,
        "exp": now + 3600,
        "principals": [{"type": "user", "subject": {"id": _OWNER, "username": f"{_OWNER}@example.test"}}],
    }
    return jwt.encode(claims, _KEY, algorithm="HS256")


def _seed_personal_bot(world, bot_id: str) -> None:
    """A personal openclaw bot on a local device binding without ``adapter_port``, so the identity files land on the
    local filesystem.
    """
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)
    binding_id = world.get(DeviceBindingRepository).insert_binding(
        entity_id=_OWNER,
        entity_type="staff",
        device_id=f"local_dev_{bot_id}",
        device_provider="local",
        env="dev",
        device_props={},
        status="ACTIVE",
        apply_reason="genome e2e seed",
        applied_by=_OWNER,
    )
    world.get(BotRepository).insert(
        {
            "bot_id": bot_id,
            "bot_name": f"Bot {bot_id}",
            "owner_id": _OWNER,
            "owner_name": _OWNER,
            "bot_type": "personal",
            "status": "ACTIVE",
            "entity_id": _OWNER,
            "entity_type": "staff",
            "creator_id": _OWNER,
            "active_engine": "openclaw",
            "device_id": f"local_dev_{bot_id}",
            "binding_id": binding_id,
        }
    )


@pytest.mark.e2e
def test_capture_patch_diff_draft_and_promote_up_to_the_delivery_seam(app_with_testing_modules, world) -> None:
    bot_id = f"bot_genome_e2e_{time.time_ns()}"
    _seed_personal_bot(world, bot_id)
    identity = world.get(IdentityService)
    asyncio.run(identity.write_identity_file("staff", _OWNER, bot_id, "SOUL.md", _SOUL, _OWNER, engine_type="openclaw"))
    client = TestClient(app_with_testing_modules, headers={PRINCIPAL_HEADER: _principal()})
    genome = f"/openapi/v1/bots/{bot_id}/genome"
    query = {"user_id": _OWNER}

    # 1. capture: the bot's first revision, parent none, draft, every byte in the content store
    r = client.post(f"{genome}/revisions:capture", params=query)
    assert r.status_code == 201, r.text
    first = r.json()["data"]
    assert (first["seq"], first["parent"], first["status"], first["created"], first["patch_digest"]) == (
        1,
        None,
        "draft",
        True,
        None,
    )
    r = client.get(f"{genome}/revisions/active", params=query)
    assert r.status_code == 404, "capture names no ref"
    revision = client.get(f"{genome}/revisions/r1", params=query).json()["data"]
    soul = next(e for e in revision["spec"]["persona"] if e["type"] == "SOUL.md")
    r = client.get(f"{genome}/content/{soul['digest']}", params=query)
    assert r.status_code == 200 and r.content == _SOUL.encode()
    again = client.post(f"{genome}/revisions:capture", params=query)
    assert (again.status_code, again.json()["data"]["created"], again.json()["data"]["id"]) == (200, False, first["id"])

    # 2. patch against the captured base
    patch = {
        "patch_schema": 1,
        "base": first["id"],
        "ops": [
            {
                "op": "file.edit",
                "target": "persona/SOUL.md",
                "edits": [
                    {
                        "kind": "insert_after",
                        "anchor": "## Tone",
                        "content": "Confirm the order id before quoting amounts.",
                    }
                ],
            }
        ],
        "rationale": "Owner edit after a customer complaint.",
        "evidence": ["feedback:fb_1"],
    }
    r = client.post(f"{genome}/revisions", params=query, json={"status": "draft", "patch": patch})
    assert r.status_code == 201, r.text
    second = r.json()["data"]
    assert (second["seq"], second["parent"], second["risk_tier"], second["flags"]) == (2, first["id"], "T2", [])
    assert second["patch_digest"] is not None and second["id"] != first["id"]

    # 3. diff
    diff = client.get(f"{genome}/revisions/r2/diff", params={**query, "against": "r1"}).json()["data"]
    assert (diff["revision"], diff["against"], diff["risk_tier"]) == (second["id"], first["id"], "T2")
    assert [(c["gene"], c["kind"]) for c in diff["changes"]] == [("persona/SOUL.md", "modified")]
    assert "+Confirm the order id before quoting amounts." in diff["changes"][0]["diff"]

    # 4. move draft by compare-and-swap
    r = client.put(
        f"{genome}/refs/draft", params=query, json={"revision": "r2", "expected_revision": None, "reason": "wip"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["revision"] == second["id"]
    stale = client.put(f"{genome}/refs/draft", params=query, json={"revision": "r1", "expected_revision": None})
    assert stale.status_code == 409 and stale.json()["data"]["current_revision"] == second["id"]
    refs_before = client.get(f"{genome}/refs", params=query).json()["data"]["refs"]
    assert [(ref["name"], ref["revision"]) for ref in refs_before] == [("draft", second["id"])]

    # 5. promote: refused at the delivery seam, naming RSI-04, before any ref moves
    r = client.post(
        f"{genome}/promotions", params=query, json={"revision": "r2", "expected_revision": None, "reason": "ship it"}
    )
    assert r.status_code == 422, r.text
    reasons = r.json()["data"]["reasons"]
    assert reasons[0]["code"] == "not_appliable" and "RSI-04" in reasons[0]["message"]
    assert client.get(f"{genome}/refs", params=query).json()["data"]["refs"] == refs_before
    assert client.get(f"{genome}/revisions/r2", params=query).json()["data"]["revision"]["status"] == "draft"
    assert client.get(f"{genome}/promotions", params=query).json()["data"]["total"] == 0
