"""Tests for core.auth.models."""
from agentclaw.community.core.auth.models import AuthenticatedIdentity


def test_authenticated_identity_from_dict():
    data = {
        "id": "123",
        "operatorName": "test_user",
        "staffId": "456",
    }
    user = AuthenticatedIdentity(**data)
    assert user.id == "123"
    assert user.staffId == "456"
    assert user.outUserNo == "456"
    assert user.operatorName == "test_user"


def test_authenticated_identity_alias_population():
    data = {
        "id": "123",
        "operatorName": "test_user",
        "outUserNo": "456",
    }
    user = AuthenticatedIdentity(**data)
    assert user.staffId == "456"


def test_authenticated_identity_optional_fields():
    user = AuthenticatedIdentity(id="1", operatorName="op", staffId="2")
    assert user.nickName is None
    assert user.tenantId is None
    assert user.realName is None
