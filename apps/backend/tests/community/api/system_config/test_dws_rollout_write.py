from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from agentclaw.community.adapters.http.system_config.router import (
    create_category,
    set_config,
)
from agentclaw.community.adapters.http.system_config.schemas import (
    CreateCategoryRequest,
    SetConfigRequest,
)
from agentclaw.community.core.errors import Forbidden

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize(
    "category", ["dws_hosting", "DWS_HOSTING", "dws_hósting", "dws_hosting "]
)
@pytest.mark.parametrize("create", [False, True])
async def test_non_operator_cannot_write_any_dws_alias(category, create):
    service = Mock()
    auth = Mock(is_operator_allowed=Mock(return_value=False))
    user = SimpleNamespace(staffId="other", operatorName="other")
    req = (
        CreateCategoryRequest(category=category, category_name="DWS")
        if create
        else SetConfigRequest(
            category=category, config_key="rollout", config_value={"mode": "all"}
        )
    )
    with pytest.raises(Forbidden):
        await (create_category if create else set_config)(req, user, service, auth)
    assert not service.mock_calls


@pytest.mark.parametrize("category", ["device", "unrelated"])
async def test_non_dws_writes_unchanged(category):
    service = Mock(
        create_category=Mock(return_value=1), set_config=Mock(return_value=1)
    )
    auth = Mock(is_operator_allowed=Mock(return_value=False))
    user = SimpleNamespace(staffId="other", operatorName="other")
    await create_category(
        CreateCategoryRequest(category=category, category_name="Other"),
        user,
        service,
        auth,
    )
    await set_config(
        SetConfigRequest(
            category=category, config_key="arbitrary", config_value={"x": 1}
        ),
        user,
        service,
        auth,
    )
    auth.is_operator_allowed.assert_not_called()


@pytest.mark.parametrize(
    "value,key,status",
    [
        ({"mode": "all"}, "rollout", 200),
        ({"mode": "disabled"}, "rollout", 200),
        ({"mode": "allowlist", "staff_ids": ["165137"]}, "rollout", 200),
        ({"mode": "bad"}, "rollout", 422),
        ({"mode": "all"}, "other", 422),
    ],
)
async def test_operator_policy_validation(value, key, status):
    service = Mock(set_config=Mock(return_value=1))
    auth = Mock(is_operator_allowed=Mock(return_value=True))
    user = SimpleNamespace(staffId="operator", operatorName="operator")
    req = SetConfigRequest(category="dws_hosting", config_key=key, config_value=value)
    if status == 422:
        with pytest.raises(HTTPException) as error:
            await set_config(req, user, service, auth)
        assert error.value.status_code == 422
        service.set_config.assert_not_called()
    else:
        assert (await set_config(req, user, service, auth)).success
