"""Grant/revoke DTO extraction preserves the task schema entry point."""

import pytest
from pydantic import ValidationError

from agentclaw.community.adapters.http.task import schemas, schemas_grants


@pytest.mark.parametrize("name,payload", [
    ("TaskGrantRequestDTO", {"bcs_bot_id": "bot:owner"}),
    ("TaskGrantResultDTO", {
        "bcs_bot_id": "bot:owner", "api_key_prefix": "prefix",
        "grant_status": "granted", "operator": "owner",
    }),
    ("TaskRevokeRequestDTO", {"bcs_bot_id": "bot:owner"}),
    ("TaskRevokeResultDTO", {
        "bcs_bot_id": "bot:owner", "grant_status": "revoked",
    }),
])
def test_grant_schemas_preserve_exports_and_payloads(name, payload):
    model = getattr(schemas_grants, name)
    assert getattr(schemas, name) is model
    assert model.model_validate(payload).model_dump() == payload
    with pytest.raises(ValidationError):
        model.model_validate({})
