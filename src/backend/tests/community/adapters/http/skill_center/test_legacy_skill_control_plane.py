"""The published /api/skills Local commands delegate to Installation control."""

from types import SimpleNamespace

import pytest

from agentclaw.community.adapters.http.skill_center.schemas import ActivateRequest
from agentclaw.community.adapters.http.skill_center.skills import (
    activate_skill,
    deactivate_skill,
)


class _Bots:
    def get_by_id_and_owner(self, bot_id: str, owner_id: str):
        assert (bot_id, owner_id) == ("bot", "owner")
        return {"active_engine": "openclaw", "bot_type": "personal"}


class _Assets:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bool | None]] = []

    def get_skill(self, *, skill_id: str, bot_id: str, actor_id: str):
        assert (skill_id, bot_id, actor_id) == ("7", "bot", "owner")
        self.calls.append(("get", None))
        return {"name": "local-seven"}

    async def set_active(self, *, skill_id: str, bot_id: str, actor_id: str, active: bool):
        assert (skill_id, bot_id, actor_id) == ("7", "bot", "owner")
        self.calls.append(("set", active))
        return {"name": "local-seven"}


@pytest.mark.asyncio
async def test_legacy_activate_keeps_wire_but_uses_bot_skill_asset_control_plane() -> None:
    assets = _Assets()
    response = await activate_skill(
        "7",
        ActivateRequest(source_path="local://ignored"),
        bot_id="bot",
        ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
        bot_repo=_Bots(),
        path_factory=object(),
        skill_service_factory=object(),
        skill_set_service_factory=object(),
        resolver=object(),
        device_sync_dispatcher=object(),
        asset_service=assets,
    )

    assert response.model_dump() == {
        "success": True,
        "message": "Skill activated successfully",
        "link_name": "local-seven",
    }
    assert assets.calls == [("get", None), ("set", True)]


@pytest.mark.asyncio
async def test_legacy_activate_with_relative_path_still_uses_control_plane() -> None:
    assets = _Assets()
    await activate_skill(
        "7", ActivateRequest(source_path="legacy", relative_path="legacy/path"),
        bot_id="bot", ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
        bot_repo=_Bots(), path_factory=object(), skill_service_factory=object(),
        skill_set_service_factory=object(), resolver=object(),
        device_sync_dispatcher=object(), asset_service=assets,
    )
    assert assets.calls == [("get", None), ("set", True)]


@pytest.mark.asyncio
async def test_legacy_deactivate_keeps_wire_but_uses_bot_skill_asset_control_plane() -> None:
    assets = _Assets()
    response = await deactivate_skill(
        "7",
        bot_id="bot",
        ctx=SimpleNamespace(user_id="owner", bot_id="bot"),
        bot_repo=_Bots(),
        path_factory=object(),
        skill_service_factory=object(),
        skill_set_service_factory=object(),
        resolver=object(),
        device_sync_dispatcher=object(),
        asset_service=assets,
    )

    assert response.model_dump() == {
        "success": True,
        "message": "Skill deactivated successfully",
    }
    assert assets.calls == [("get", None), ("set", False)]
