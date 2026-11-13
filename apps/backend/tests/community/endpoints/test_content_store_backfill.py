"""POST /api/system/content-store/manifest-inline/backfill — the transitional inline backfill."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.system.content_store_backfill import (
    ManifestInlineBackfillRequest,
    backfill_manifest_inline_content,
)
from agentclaw.community.api.manifest_inline_backfill_service import (
    InlineBackfillEntry,
    InlineBackfillReport,
    InlineBackfillRowNotFound,
)
from agentclaw.community.core.content_store.errors import ContentStoreError, ContentStoreFault
from tests.community.factories.access import make_staff_user
from agentclaw.community.core.content_store import ContentScope, ContentStoreProtocol
from agentclaw.community.core.repository.protocols.bot import BotConfigManifestRepositoryProtocol
from agentclaw.community.utils.env_utils import get_current_env
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

SUPER_ADMIN = "100000"  # seeded admin.super_admin in application-test.yaml
NOT_ADMIN = "999999"
PATH = "/api/system/content-store/manifest-inline/backfill"
DIGEST = "sha256:" + "ab" * 32
ROW = {"entity_id": "ent_a", "bot_id": "bot_1", "digest": DIGEST}

SOUL = "# Soul\nYou are the support agent.\n"
SOUL_DIGEST = "sha256:" + hashlib.sha256(SOUL.encode("utf-8")).hexdigest()
SOUL_ROW = {"entity_id": "backfill-owner", "bot_id": "backfill-bot", "digest": SOUL_DIGEST}
DOCUMENT = 'schema_version: 1\nmanifest:\n  identity:\n    - type: SOUL.md\n      content: "# Soul\\nYou are the support agent.\\n"\n'


def _seed_bot_with_an_inline_manifest_row(world) -> None:
    """A bot whose stored Manifest declares an inline SOUL.md, and the content row an apply filed for it."""
    make_staff_user(world, user_id=SUPER_ADMIN)
    env = get_current_env()
    world.get(BotConfigManifestRepositoryProtocol).upsert(
        env=env,
        entity_id=SOUL_ROW["entity_id"],
        bot_id=SOUL_ROW["bot_id"],
        document=DOCUMENT,
        size_bytes=len(DOCUMENT.encode("utf-8")),
        schema_version=1,
        modifier=SUPER_ADMIN,
    )
    world.get(ContentStoreProtocol).store_produced(
        SOUL.encode("utf-8"),
        scope=ContentScope(env=env, entity_id=SOUL_ROW["entity_id"], bot_id=SOUL_ROW["bot_id"]),
        origin="manifest/inline",
    )


@endpoint_test(
    method="POST",
    path=PATH,
    scenario="stores_the_inline_bodies_of_the_rows_manifest",
    input=CaseInput(headers={"x-user-id": SUPER_ADMIN}, json_body=SOUL_ROW),
    seed=_seed_bot_with_an_inline_manifest_row,
    expect=ExpectSuccess(
        status=200,
        json_equals={
            **SOUL_ROW,
            "document_found": True,
            "row_digest_restored": True,
            "entries": [
                {
                    "category": "identity",
                    "identity": "SOUL.md",
                    "digest": SOUL_DIGEST,
                    "size_bytes": len(SOUL.encode("utf-8")),
                }
            ],
        },
    ),
)
def backfill_stores_inline_bodies():
    """A super admin posts a row; the stored Manifest's inline bodies are written and reported."""


@endpoint_test(
    method="POST",
    path=PATH,
    scenario="forbidden_for_a_non_super_admin",
    input=CaseInput(headers={"x-user-id": NOT_ADMIN}, json_body=ROW),
    seed=lambda world: make_staff_user(world, user_id=NOT_ADMIN),
    expect=ExpectError(status=403),
)
def backfill_forbidden():
    """Only a super admin may write platform storage for an arbitrary bot."""


@endpoint_test(
    method="POST",
    path=PATH,
    scenario="row_not_found",
    input=CaseInput(headers={"x-user-id": SUPER_ADMIN}, json_body=ROW),
    seed=lambda world: make_staff_user(world, user_id=SUPER_ADMIN),
    expect=ExpectError(status=404),
)
def backfill_row_not_found():
    """A row this bot does not hold is a 404, through the real service and store."""


@endpoint_test(
    method="POST",
    path=PATH,
    scenario="malformed_digest",
    input=CaseInput(headers={"x-user-id": SUPER_ADMIN}, json_body={**ROW, "digest": "md5:abc"}),
    seed=lambda world: make_staff_user(world, user_id=SUPER_ADMIN),
    expect=ExpectError(status=422),
)
def backfill_malformed_digest():
    """The digest is the address; one that cannot name content is refused."""


class _Service:
    def __init__(self, outcome) -> None:
        self.outcome, self.calls = outcome, []

    def backfill(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _call(service, body=None):
    request = ManifestInlineBackfillRequest.model_validate(body or ROW)
    return backfill_manifest_inline_content(request, user=SimpleNamespace(staffId=SUPER_ADMIN), service=service)


def test_a_whole_row_may_be_posted_and_the_report_is_returned():
    report = InlineBackfillReport(
        entity_id="ent_a",
        bot_id="bot_1",
        digest=DIGEST,
        document_found=True,
        row_digest_restored=True,
        entries=(InlineBackfillEntry("identity", "SOUL.md", DIGEST, 12),),
    )
    service = _Service(report)
    row = {**ROW, "id": 7, "env": "pre", "source_url": "produced://manifest/inline", "fetched_at": "2026-11-10"}

    response = _call(service, row)

    assert service.calls == [ROW]
    assert response.model_dump() == {
        **ROW,
        "document_found": True,
        "row_digest_restored": True,
        "entries": [{"category": "identity", "identity": "SOUL.md", "digest": DIGEST, "size_bytes": 12}],
    }


@pytest.mark.parametrize(
    "error, status",
    [
        (InlineBackfillRowNotFound("no row"), 404),
        (ContentStoreError("untrusted content address"), 422),
        (ContentStoreFault("the object storage did not store the blob"), 502),
    ],
)
def test_service_errors_map_to_statuses(error, status):
    with pytest.raises(HTTPException) as raised:
        _call(_Service(error))
    assert raised.value.status_code == status
