"""Endpoint-injection coverage for Space Skill Publication resources."""

from __future__ import annotations

from datetime import UTC, datetime
import time

import jwt

from agentclaw.community.api.space_skill_publication_service import (
    SpaceSkillPublicationServiceProtocol,
)
from agentclaw.community.core.skill_center.publication_contract import (
    PublicationAttemptRecord,
    PublicationAttemptStatus,
    PublicationImpactItem,
    PublicationRecovery,
    PublicationRecoveryKind,
    PublicationRecoveryState,
    PublicationRetryResult,
)
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    bind_overrides,
    endpoint_test,
)


_USER_ID = "publication-endpoint-user"
_SIGNING_KEY = "publication-endpoint-secret-key-at-least-32-bytes"
_BASE = "/openapi/v1/bots/spaces/{space_id}/skills/{skill_id}"


class _Secret:
    secret_user = "test"
    secret_value = _SIGNING_KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _principal_headers() -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 60 * 60,
            "principals": [
                {
                    "type": "user",
                    "tenant": "publication-endpoint-test",
                    "subject": {"id": _USER_ID, "username": "publisher@example.com"},
                }
            ],
        },
        _SIGNING_KEY,
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": token}


def _enable_public_auth(_world) -> None:
    init_principal_verifier_config(_Resolver(), "test-key", strict=False)


def _attempt() -> PublicationAttemptRecord:
    now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    return PublicationAttemptRecord(
        attempt_id=71,
        skill_id=11,
        frozen_draft_locator=(
            "draft://00000000-0000-4000-8000-000000000011/v2/"
            "00000000-0000-4000-8000-000000000012"
        ),
        target_version=2,
        status=PublicationAttemptStatus.PREPARING,
        sc_version_number="2.0.0",
        recovery=PublicationRecovery(
            PublicationRecoveryState.AUTO_RETRYING,
            PublicationRecoveryKind.PREPARATION,
        ),
        error_code=None,
        error_message=None,
        skill_version_id=None,
        created_by=_USER_ID,
        gmt_created=now,
        gmt_modified=now,
    )


def _seed_publication_service(world) -> None:
    _enable_public_auth(world)
    attempt = _attempt()

    def impact(_self, **_kwargs):
        return 1, [PublicationImpactItem(_USER_ID, "bot-1", "Risk Bot")]

    def create(_self, **_kwargs):
        return attempt

    def collection(_self, **_kwargs):
        return 1, [attempt]

    def detail(_self, **_kwargs):
        return attempt

    def retry(_self, **_kwargs):
        return PublicationRetryResult(attempt, task_required=True)

    bind_overrides(
        world,
        SpaceSkillPublicationServiceProtocol,
        {
            "list_publication_impact": impact,
            "create_publication": create,
            "list_publications": collection,
            "get_publication": detail,
            "retry_publication": retry,
        },
    )


def _happy_input(*, headers: dict[str, str] | None = None) -> CaseInput:
    return CaseInput(
        path_params={"space_id": 3, "skill_id": 11, "attempt_id": 71},
        query_params={"user_id": _USER_ID},
        headers={**_principal_headers(), **(headers or {})},
    )


def _wrong_user_input(*, headers: dict[str, str] | None = None) -> CaseInput:
    return CaseInput(
        path_params={"space_id": 3, "skill_id": 11, "attempt_id": 71},
        query_params={"user_id": "another-user"},
        headers={**_principal_headers(), **(headers or {})},
    )


_CASES = (
    (
        "GET",
        f"{_BASE}/publication-impact",
        _happy_input(),
        ExpectSuccess(status=200, json_contains={"data": {"total": 1}}),
    ),
    (
        "POST",
        f"{_BASE}/publications",
        _happy_input(headers={"Idempotency-Key": "publication-endpoint-71"}),
        ExpectSuccess(
            status=202,
            json_contains={"code": 202000, "data": {"attempt_id": "71"}},
        ),
    ),
    (
        "GET",
        f"{_BASE}/publications",
        _happy_input(),
        ExpectSuccess(status=200, json_contains={"data": {"total": 1}}),
    ),
    (
        "GET",
        f"{_BASE}/publications/{{attempt_id}}",
        _happy_input(),
        ExpectSuccess(
            status=200,
            json_contains={"code": 200000, "data": {"attempt_id": "71"}},
        ),
    ),
    (
        "POST",
        f"{_BASE}/publications/{{attempt_id}}/retry",
        _happy_input(),
        ExpectSuccess(
            status=202,
            json_contains={"code": 202000, "data": {"attempt_id": "71"}},
        ),
    ),
)


for _method, _path, _input, _expect in _CASES:
    endpoint_test(
        method=_method,
        path=_path,
        scenario="happy",
        seed=_seed_publication_service,
        input=_input,
        expect=_expect,
    )(lambda: None)

    endpoint_test(
        method=_method,
        path=_path,
        scenario="wrong_user",
        seed=_enable_public_auth,
        input=_wrong_user_input(
            headers=(
                {"Idempotency-Key": "publication-endpoint-wrong-user"}
                if _method == "POST" and _path.endswith("/publications")
                else None
            )
        ),
        expect=ExpectError(status=403),
    )(lambda: None)
