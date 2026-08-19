"""Public seam tests for type-resolved Local Skill content and parameters."""

import pytest

from agentclaw.community.core.skill_center.errors import LocalSkillStorageError
from agentclaw.community.core.skill_center.services.bot_skill_asset_service import (
    BotSkillAssetService,
)


class _Skills:
    def get_by_id(self, skill_id: str):
        if skill_id != "42":
            return None
        return {
            "id": "42",
            "name": "weekly-report",
            "git_path": "local://weekly-report",
            "user_id": "owner",
            "bolt_id": "bot",
        }


class _Bots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str):
        if (bot_id, owner_id) != ("bot", "owner"):
            return None
        return {
            "entity_id": "owner",
            "entity_type": "staff",
            "active_engine": "openclaw",
            "bot_type": "personal",
        }


class _Storage:
    async def read_file(self, path: str):
        assert path == "SKILL.md"
        return b"---\nname: weekly-report\ndescription: weekly\nconfig:\n  - name: region\n    required: true\n---\n# Report"


class _Factory:
    def local_skill_package_storage_for_locator(self, **kwargs):
        assert kwargs["locator"] == "weekly-report"
        return _Storage()


class _Parameters:
    def __init__(self, result: bool = True) -> None:
        self.result = result
        self.saved = None

    def get_skill_parameters(self, name: str):
        assert name == "weekly-report"
        return {"region": "cn"}

    async def save_skill_parameters(self, name: str, values: dict):
        self.saved = (name, values)
        return self.result


class _ParameterFactory:
    def __init__(self, result: bool = True) -> None:
        self.parameters = _Parameters(result)

    async def create(self, **kwargs):
        assert kwargs == {"bot_id": "bot", "user_id": "owner"}
        return self.parameters


class _Resolver:
    def resolve_for_bot(self, *_args):
        return type("Context", (), {"provider": "local"})()


def _service(*, save_result: bool = True):
    parameters = _ParameterFactory(save_result)
    return BotSkillAssetService(
        _Skills(),
        _Bots(),
        object(),
        _Factory(),
        parameters,
        lambda: _Resolver(),
    ), parameters


@pytest.mark.asyncio
async def test_local_content_and_parameters_use_one_skill_id_resolver() -> None:
    service, parameters = _service()

    assert "# Report" in await service.get_content(
        skill_id="42", bot_id="bot", actor_id="owner"
    )
    assert await service.get_parameters(
        skill_id="42", bot_id="bot", actor_id="owner"
    ) == {"region": "cn"}
    assert await service.replace_parameters(
        skill_id="42",
        bot_id="bot",
        actor_id="owner",
        parameters={"region": "cn"},
    ) == {"region": "cn"}
    assert parameters.parameters.saved == ("weekly-report", {"region": "cn"})


@pytest.mark.asyncio
async def test_parameter_persistence_failure_is_not_reported_as_success() -> None:
    service, _parameters = _service(save_result=False)

    with pytest.raises(LocalSkillStorageError):
        await service.replace_parameters(
            skill_id="42",
            bot_id="bot",
            actor_id="owner",
            parameters={"region": "cn"},
        )
