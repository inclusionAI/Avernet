from pydantic import ValidationError
import pytest

from agentclaw.community.adapters.http.bot_public.risk_contract import (
    LegacyRiskRequest, OpenApiRiskRequest, RiskRecordResponse,
)


def test_legacy_publish_defaults_and_only_public_target():
    request = LegacyRiskRequest.model_validate({"client_request_id": "r1", "public": 1})
    assert request.permission_owner == "caller"
    assert request.friend_approval == "0"
    assert request.public == "1"
    with pytest.raises(ValidationError):
        LegacyRiskRequest.model_validate({"client_request_id": "r1", "public": "0"})


def test_new_public_scope_visibility_require_matching_departments():
    request = OpenApiRiskRequest.model_validate({"client_request_id": "r1", "public_scope": "user", "visibility": "protected", "view_depts": [{"deptNo": "d1", "deptName": "Dept"}]})
    assert request.view_depts[0].deptNo == "d1"
    for value in (
        {"visibility": "protected", "view_depts": []},
        {"visibility": "public", "view_depts": [{"deptNo": "d1", "deptName": "Dept"}]},
        {"visibility": "private", "view_depts": []},
    ):
        with pytest.raises(ValidationError):
            OpenApiRiskRequest.model_validate({"client_request_id": "r1", "public_scope": "user", **value})


def test_response_never_leaks_input_or_summary():
    response = RiskRecordResponse.model_validate({"risk_check_id": "r1", "phase": "CHECKING", "security_status": "REJECT", "detail_url": "https://example.com/report", "target": {"public_scope": "user", "visibility": "public", "view_depts": []}, "input_json": "secret", "input_hash": "digest", "summary": "private"}).model_dump()
    assert response["detail_url"] == "https://example.com/report"
    assert "input_json" not in response and "summary" not in response and "input_hash" not in response


def test_collaboration_address_resolves_full_identity_and_requires_manage_permission():
    from agentclaw.community.adapters.http.bot_public.risk_access import authorize_risk_bot, RiskAccessDenied
    from agentclaw.community.core.bot_collaborator.models import PermissionLevel

    class Bots:
        def get_by_id_and_entity(self, bot_id, entity_id):
            if (bot_id, entity_id) == ("default", "entity"):
                return {"id": 5, "bot_id": "default", "entity_id": "entity", "owner_id": "owner", "env": "dev"}
    class Collaborators:
        def get_explicit_permission_level(self, *, bot, user_id, env):
            return PermissionLevel.ADMIN if user_id == "manager" else PermissionLevel.MEMBER
    bot = authorize_risk_bot(Bots(), Collaborators(), bot_id="default:entity", actor_id="manager", surface="openapi")
    assert bot.bot_pk == 5
    for actor, locator in [("member", "default:entity"), ("manager", "default:other"), ("manager", "default")]:
        with pytest.raises(RiskAccessDenied):
            authorize_risk_bot(Bots(), Collaborators(), bot_id=locator, actor_id=actor, surface="openapi")


def test_legacy_owner_locator_does_not_replace_authenticated_actor():
    from agentclaw.community.adapters.http.bot_public.risk_access import authorize_risk_bot, RiskAccessDenied
    from agentclaw.community.core.bot_collaborator.models import PermissionLevel

    bot = {"id": 5, "bot_id": "default", "entity_id": "entity", "owner_id": "owner", "env": "dev"}
    class Bots:
        def list_matches_by_legacy_locator(self, bot_id, owner_id):
            return [bot] if (bot_id, owner_id) == ("default", "owner") else []
    class Collaborators:
        def get_explicit_permission_level(self, *, bot, user_id, env):
            return PermissionLevel.ADMIN if user_id == "manager" else PermissionLevel.NONE
    assert authorize_risk_bot(Bots(), Collaborators(), bot_id="default", actor_id="manager", legacy_owner_id="owner", surface="legacy").bot_pk == 5
    with pytest.raises(RiskAccessDenied):
        authorize_risk_bot(Bots(), Collaborators(), bot_id="default", actor_id="stranger", legacy_owner_id="owner", surface="legacy")
