"""Unit tests for BotInventoryService aggregation behavior."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_inventory.adapters.noop_business_space import (
    NoopBusinessSpaceContext,
)
from agentclaw.community.core.bot_inventory.adapters.noop_service_lifecycle import (
    NoopServiceLifecyclePort,
)
from agentclaw.community.core.bot_inventory.protocols import (
    BusinessSpaceContextProtocol,
)
from agentclaw.community.core.bot_inventory.services.bot_inventory_service import (
    BotInventoryService,
    _cloud_fetch_engines,
    _select_aicoding_runtime_rows,
)
from agentclaw.community.core.bot_inventory.services.lifecycle_view import (
    BotLifecycleView,
)
from agentclaw.community.core.bot_inventory.types import (
    BotAction,
    BotInventoryKind,
    BusinessSpaceRef,
    DeployMode,
    DisplayState,
    ServiceEditLockState,
    ServiceLifecycleCard,
)


CLOUD = {
    "id": 1,
    "bot_id": "c1",
    "bot_name": "Cloud",
    "bot_desc": "cloud bot",
    "active_engine": "teclaw",
    "bot_type": "personal",
    "status": "ACTIVE",
    "owner_id": "u1",
}
LOCAL = {
    "id": 2,
    "bot_id": "l1",
    "bot_name": "Local",
    "bot_desc": "local bot",
    "active_engine": "openclaw",
    "bot_type": "desktop",
    "status": "OFFLINE",
    "owner_id": "u1",
}


def _no_edit_locks():
    view = MagicMock()
    view.states_for_bots.return_value = {}
    return view


class _StubTemplatePort:
    """Recording stub: answers ``ac_templates.ext`` snapshots per bot id."""

    def __init__(self, ext_by_bot_id: dict[str, dict] | None = None) -> None:
        self.calls: list[list[str]] = []
        self.ext_by_bot_id = ext_by_bot_id or {}

    def list_template_configs_by_bot_ids(self, bot_ids: list[str]) -> dict[str, dict]:
        self.calls.append(list(bot_ids))
        wanted = set(bot_ids)
        return {
            bot_id: ext for bot_id, ext in self.ext_by_bot_id.items() if bot_id in wanted
        }


@pytest.fixture
def service():
    bot = MagicMock()
    bot.list_bots_by_conditions.return_value = {"total": 1, "items": [CLOUD]}
    bot.get_bot.return_value = CLOUD
    desktop = MagicMock()
    desktop.list_user_bots.return_value = [LOCAL]
    access = MagicMock()
    access.get_operable_permission_levels.side_effect = lambda **kwargs: {
        int(bot["id"]): PermissionLevel.OWNER for bot in kwargs["bots"]
    }
    return (
        BotInventoryService(
            bot_service=bot,
            desktop_service=desktop,
            access_service=access,
            business_space=NoopBusinessSpaceContext(),
            lifecycle_view=BotLifecycleView(NoopServiceLifecyclePort()),
            edit_lock_view=_no_edit_locks(),
            template_port=_StubTemplatePort(),
        ),
        bot,
        desktop,
    )


@pytest.mark.unit
def test_list_items_combines_filters_and_paginates(service) -> None:
    inventory, _, _ = service

    items, total = inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine=None,
        deploy_mode=None,
        page=1,
        page_size=10,
    )

    assert total == 2
    assert {item.bot_id for item in items} == {"c1", "l1"}


@pytest.mark.unit
def test_cloud_pull_opts_out_of_template_attach(service) -> None:
    # The fan-out still pulls without template attachment: snapshots enter the
    # read model only through the page-slice enrichment (see the
    # test_page_slice_* cases below), so every pulled page keeps skipping the
    # batched template read.
    inventory, bot, _ = service

    inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.CLOUD,
        is_service=None,
        page=1,
        page_size=10,
    )

    assert bot.list_bots_by_conditions.call_args.kwargs["attach_templates"] is False


@pytest.mark.unit
def test_cloud_source_fetches_all_pages_for_exact_total(service) -> None:
    inventory, bot, desktop = service
    cloud_rows = [
        {**CLOUD, "bot_id": f"c{i:04d}", "bot_name": f"Cloud {i:04d}"}
        for i in range(1_200)
    ]

    def list_page(**kwargs):
        page = kwargs["page"]
        page_size = kwargs["page_size"]
        start = (page - 1) * page_size
        return {
            "total": len(cloud_rows),
            "items": cloud_rows[start : start + page_size],
        }

    bot.list_bots_by_conditions.side_effect = list_page
    desktop.list_user_bots.return_value = []

    items, total = inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.CLOUD,
        page=12,
        page_size=100,
    )

    assert total == len(cloud_rows)
    assert len(items) == 100
    assert items[0].bot_id == "c1100"
    assert bot.list_bots_by_conditions.call_count == 6


@pytest.mark.unit
def test_service_bot_expands_to_publication_cards_before_pagination() -> None:
    bot = MagicMock()
    service_row = {
        **CLOUD,
        "id": 10,
        "bot_id": "s1",
        "bot_name": "Service",
        "bot_type": "service",
    }
    bot.list_bots_by_conditions.return_value = {
        "total": 2,
        "items": [CLOUD, service_row],
    }
    desktop = MagicMock()
    desktop.list_user_bots.return_value = []
    lifecycle_port = MagicMock()
    lifecycle_port.cards_for_bots.return_value = {
        "s1": (
            ServiceLifecycleCard(
                publication_id=4,
                version=4,
                display_state=DisplayState.SERVICE_DRAFT,
                status="draft",
                actions=(BotAction.VIEW, BotAction.PUBLISH_STAGING),
                internal_status="draft",
                live_version=3,
                has_draft=True,
            ),
            ServiceLifecycleCard(
                publication_id=3,
                version=3,
                display_state=DisplayState.SERVICE_OFFLINE,
                status="released",
                actions=(BotAction.VIEW,),
                internal_status="released",
                live_version=3,
            ),
        )
    }
    access = MagicMock()
    access.get_operable_permission_levels.return_value = {
        1: PermissionLevel.OWNER,
        10: PermissionLevel.OWNER,
    }
    edit_lock_view = MagicMock()
    edit_lock_view.states_for_bots.return_value = {
        ("s1", "u1"): ServiceEditLockState(
            locked=True,
            holder_user_id="editor-1",
            holder_name="Editor One",
            has_collaborators=True,
            is_owner_holder=False,
        )
    }
    inventory = BotInventoryService(
        bot_service=bot,
        desktop_service=desktop,
        access_service=access,
        business_space=NoopBusinessSpaceContext(),
        lifecycle_view=BotLifecycleView(lifecycle_port),
        edit_lock_view=edit_lock_view,
        template_port=_StubTemplatePort(),
    )

    items, total = inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.CLOUD,
        is_service=True,
        page=1,
        page_size=10,
    )

    assert total == 2
    assert {item.bot_id for item in items} == {"s1"}
    service_items = [item for item in items if item.bot_id == "s1"]
    assert [item.publication_id for item in service_items] == [4, 3]
    assert [item.card_id for item in service_items] == ["service:s1:4", "service:s1:3"]
    assert [item.edit_lock.need_lock for item in service_items] == [True, True]
    assert [item.edit_lock.holder_name for item in service_items] == [
        "Editor One",
        "Editor One",
    ]
    edit_lock_view.states_for_bots.assert_called_once_with(bots=[service_row])
    lifecycle_port.cards_for_bots.assert_called_once_with(bots=[service_row])


@pytest.mark.unit
def test_edit_lock_batch_reads_only_service_bots_on_current_page() -> None:
    first = {
        **CLOUD,
        "id": 10,
        "bot_id": "service-1",
        "bot_name": "A Service",
        "bot_type": "service",
    }
    second = {
        **first,
        "id": 11,
        "bot_id": "service-2",
        "bot_name": "B Service",
    }
    bot = MagicMock()
    bot.list_bots_by_conditions.return_value = {
        "total": 2,
        "items": [first, second],
    }
    desktop = MagicMock()
    desktop.list_user_bots.return_value = []
    access = MagicMock()
    access.get_operable_permission_levels.return_value = {
        10: PermissionLevel.OWNER,
        11: PermissionLevel.OWNER,
    }
    lifecycle_port = MagicMock()
    lifecycle_port.cards_for_bots.return_value = {
        row["bot_id"]: (
            ServiceLifecycleCard(
                publication_id=row["id"],
                version=1,
                display_state=DisplayState.SERVICE_ONLINE,
                status="running",
                actions=(BotAction.VIEW,),
            ),
        )
        for row in (first, second)
    }
    edit_lock_view = MagicMock()
    edit_lock_view.states_for_bots.return_value = {
        ("service-1", "u1"): ServiceEditLockState(
            locked=False,
            holder_user_id=None,
            holder_name=None,
            has_collaborators=False,
            is_owner_holder=False,
        )
    }
    inventory = BotInventoryService(
        bot_service=bot,
        desktop_service=desktop,
        access_service=access,
        business_space=NoopBusinessSpaceContext(),
        lifecycle_view=BotLifecycleView(lifecycle_port),
        edit_lock_view=edit_lock_view,
        template_port=_StubTemplatePort(),
    )

    items, total = inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.CLOUD,
        page=1,
        page_size=1,
    )

    assert total == 2
    assert [item.bot_id for item in items] == ["service-1"]
    edit_lock_view.states_for_bots.assert_called_once_with(bots=[first])


@pytest.mark.unit
def test_non_service_filter_excludes_service_cards_before_total(service) -> None:
    inventory, bot, _ = service
    service_row = {
        **CLOUD,
        "id": 10,
        "bot_id": "s1",
        "bot_name": "Service",
        "bot_type": "service",
    }
    bot.list_bots_by_conditions.return_value = {
        "total": 2,
        "items": [CLOUD, service_row],
    }

    items, total = inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine=None,
        deploy_mode=None,
        is_service=False,
        page=1,
        page_size=10,
    )

    assert total == 2
    assert {item.bot_id for item in items} == {"c1", "l1"}


@pytest.mark.unit
def test_local_deploy_with_service_filter_returns_empty_without_source_calls(
    service,
) -> None:
    inventory, bot, desktop = service

    items, total = inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.LOCAL,
        is_service=True,
        page=1,
        page_size=10,
    )

    assert total == 0
    assert items == []
    bot.list_bots_by_conditions.assert_not_called()
    desktop.list_user_bots.assert_not_called()


@pytest.mark.unit
def test_grant_filter_is_applied_to_both_sources_before_total_and_pagination(
    service,
) -> None:
    inventory, bot, desktop = service
    bot.list_bots_by_conditions.side_effect = lambda **kwargs: {
        "total": 1 if "c1" in (kwargs["bot_ids"] or []) else 0,
        "items": [CLOUD] if "c1" in (kwargs["bot_ids"] or []) else [],
    }
    desktop.list_user_bots.return_value = [
        LOCAL,
        {**LOCAL, "bot_id": "l2", "bot_name": "Local 2"},
        {**LOCAL, "bot_id": "l3", "bot_name": "Local 3"},
    ]

    items, total = inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine=None,
        deploy_mode=None,
        bot_ids=["l1", "l3"],
        page=2,
        page_size=1,
    )

    assert total == 2
    assert [item.bot_id for item in items] == ["l3"]
    assert bot.list_bots_by_conditions.call_args.kwargs["bot_ids"] == ["l1", "l3"]


@pytest.mark.unit
def test_cloud_grant_filter_is_forwarded_to_every_upstream_page(service) -> None:
    inventory, bot, desktop = service
    desktop.list_user_bots.return_value = []
    cloud_rows = [
        {**CLOUD, "bot_id": f"c{i:03d}", "bot_name": f"Cloud {i:03d}"}
        for i in range(201)
    ]

    def list_page(**kwargs):
        assert kwargs["bot_ids"] == ["c000", "c200"]
        page = kwargs["page"]
        page_size = kwargs["page_size"]
        start = (page - 1) * page_size
        return {
            "total": len(cloud_rows),
            "items": cloud_rows[start : start + page_size],
        }

    bot.list_bots_by_conditions.side_effect = list_page

    inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.CLOUD,
        bot_ids=["c000", "c200"],
        page=1,
        page_size=10,
    )

    assert bot.list_bots_by_conditions.call_count == 2


@pytest.mark.unit
def test_team_space_lists_all_owners_and_scopes_actions_by_bot_permission() -> None:
    team_space = BusinessSpaceRef(
        space_id="22",
        name="Alpha",
        kind="team",
    )
    owner_bot = {
        **CLOUD,
        "id": 10,
        "bot_id": "service-owner",
        "bot_name": "Owner Service",
        "bot_type": "service",
        "space_id": "22",
    }
    editor_bot = {
        **owner_bot,
        "id": 11,
        "bot_id": "service-editor",
        "bot_name": "Editor Service",
        "owner_id": "other-owner",
    }
    viewer_bot = {
        **owner_bot,
        "id": 12,
        "bot_id": "service-viewer",
        "bot_name": "Viewer Service",
        "owner_id": "third-owner",
    }
    rows = [owner_bot, editor_bot, viewer_bot]
    bot = MagicMock()
    bot.list_bots_by_conditions.return_value = {"total": 3, "items": rows}
    desktop = MagicMock()
    access = MagicMock()
    access.get_operable_permission_levels.return_value = {
        10: PermissionLevel.OWNER,
        11: PermissionLevel.MEMBER,
        12: PermissionLevel.NONE,
    }
    business_space = MagicMock(spec=BusinessSpaceContextProtocol)
    business_space.bot_space.return_value = team_space
    lifecycle_port = MagicMock()
    lifecycle_port.cards_for_bots.return_value = {
        row["bot_id"]: (
            ServiceLifecycleCard(
                publication_id=row["id"],
                version=1,
                display_state=DisplayState.SERVICE_ONLINE,
                status="online",
                actions=(BotAction.VIEW, BotAction.EDIT, BotAction.DELETE),
            ),
        )
        for row in rows
    }
    edit_lock_view = MagicMock()
    edit_lock_view.states_for_bots.return_value = {
        ("service-owner", "u1"): ServiceEditLockState(
            locked=False,
            holder_user_id=None,
            holder_name=None,
            has_collaborators=False,
            is_owner_holder=False,
        ),
        ("service-editor", "other-owner"): ServiceEditLockState(
            locked=True,
            holder_user_id="u1",
            holder_name="Current User",
            has_collaborators=True,
            is_owner_holder=False,
        ),
    }
    inventory = BotInventoryService(
        bot_service=bot,
        desktop_service=desktop,
        access_service=access,
        business_space=business_space,
        lifecycle_view=BotLifecycleView(lifecycle_port),
        edit_lock_view=edit_lock_view,
        template_port=_StubTemplatePort(),
    )

    items, total = inventory.list_items(
        owner_id="u1",
        space=team_space,
        keyword=None,
        engine=None,
        deploy_mode=None,
        page=1,
        page_size=10,
    )

    assert total == 3
    assert {item.owner_entity_id for item in items} == {
        "u1",
        "other-owner",
        "third-owner",
    }
    bot.list_bots_by_conditions.assert_called_once_with(
        owner_id=None,
        space_id="22",
        bot_name=None,
        engine=None,
        status=None,
        bot_ids=None,
        attach_templates=False,
        page=1,
        page_size=200,
    )
    desktop.list_user_bots.assert_not_called()
    by_id = {item.bot_id: item for item in items}
    assert by_id["service-owner"].actions == (
        BotAction.VIEW,
        BotAction.EDIT,
        BotAction.DELETE,
    )
    assert by_id["service-editor"].actions == (
        BotAction.VIEW,
        BotAction.EDIT,
    )
    assert by_id["service-editor"].disabled_actions == {
        "delete": "Bot Owner permission required"
    }
    assert by_id["service-viewer"].actions == (BotAction.VIEW,)
    assert by_id["service-viewer"].disabled_actions == {
        "edit": "Bot editor permission required",
        "delete": "Bot editor permission required",
    }
    assert by_id["service-owner"].edit_lock is not None
    assert by_id["service-editor"].edit_lock is not None
    assert by_id["service-viewer"].edit_lock is None
    edit_lock_view.states_for_bots.assert_called_once_with(bots=[editor_bot, owner_bot])
    assert all(item.space == team_space for item in items)


def test_actions_for_level_keeps_edit_for_non_service_editors() -> None:
    """Non-service cards: collaborators (MEMBER/ADMIN) keep the edit action —
    the skills/skill-sets endpoints gate on PermissionLevel.MEMBER — while
    owner-scoped actions stay disabled; NONE is view-only; OWNER is unchanged.
    """
    actions = (BotAction.VIEW, BotAction.EDIT, BotAction.RESTART, BotAction.DELETE)

    owner_actions, owner_disabled = BotInventoryService._actions_for_level(
        kind=BotInventoryKind.PERSONAL_CLOUD,
        actions=actions,
        disabled={},
        level=PermissionLevel.OWNER,
    )
    assert owner_actions == actions
    assert owner_disabled == {}

    for level in (PermissionLevel.MEMBER, PermissionLevel.ADMIN):
        kept_actions, disabled = BotInventoryService._actions_for_level(
            kind=BotInventoryKind.PERSONAL_CLOUD,
            actions=actions,
            disabled={},
            level=level,
        )
        assert kept_actions == (BotAction.VIEW, BotAction.EDIT)
        assert disabled == {
            "restart": "Bot editor permission required",
            "delete": "Bot editor permission required",
        }

    none_actions, none_disabled = BotInventoryService._actions_for_level(
        kind=BotInventoryKind.PERSONAL_CLOUD,
        actions=actions,
        disabled={},
        level=PermissionLevel.NONE,
    )
    assert none_actions == (BotAction.VIEW,)
    assert none_disabled == {
        "edit": "Bot editor permission required",
        "restart": "Bot editor permission required",
        "delete": "Bot editor permission required",
    }


def test_service_upgrade_action_requires_admin() -> None:
    actions = (BotAction.VIEW, BotAction.UPGRADE, BotAction.DELETE)

    member_actions, member_disabled = BotInventoryService._actions_for_level(
        kind=BotInventoryKind.SERVICE,
        actions=actions,
        disabled={},
        level=PermissionLevel.MEMBER,
    )
    assert member_actions == (BotAction.VIEW,)
    assert member_disabled == {
        "delete": "Bot Owner permission required",
        "upgrade": "Bot Admin permission required",
    }

    admin_actions, admin_disabled = BotInventoryService._actions_for_level(
        kind=BotInventoryKind.SERVICE,
        actions=actions,
        disabled={},
        level=PermissionLevel.ADMIN,
    )
    assert admin_actions == (BotAction.VIEW, BotAction.UPGRADE)
    assert admin_disabled == {"delete": "Bot Owner permission required"}


def _inventory_with(bot_rows: list[dict], stub: _StubTemplatePort) -> BotInventoryService:
    bot = MagicMock()
    bot.list_bots_by_conditions.return_value = {"total": len(bot_rows), "items": bot_rows}
    desktop = MagicMock()
    desktop.list_user_bots.return_value = []
    access = MagicMock()
    access.get_operable_permission_levels.side_effect = lambda **kwargs: {
        int(row["id"]): PermissionLevel.OWNER for row in kwargs["bots"]
    }
    return BotInventoryService(
        bot_service=bot,
        desktop_service=desktop,
        access_service=access,
        business_space=NoopBusinessSpaceContext(),
        lifecycle_view=BotLifecycleView(NoopServiceLifecyclePort()),
        edit_lock_view=_no_edit_locks(),
        template_port=stub,
    )


def _template_bot(bot_id: str, row_id: int) -> dict:
    return {
        **CLOUD,
        "id": row_id,
        "bot_id": bot_id,
        "bot_name": f"Bot {bot_id}",
        "template_type": "applicationCoding",
    }


@pytest.mark.unit
def test_page_slice_attaches_template_config_verbatim() -> None:
    # Three template-backed bots, page_size=2 -> the port sees only the
    # returned page's template-backed ids, and the stored ext is attached
    # verbatim (2026-09-01 passthrough decision — secrets echo to the owner).
    stub = _StubTemplatePort(
        {
            "b1": {"devflow_workflow": "w1", "token": "raw-secret"},
            "b2": {"template_key": "normalCC", "runtime": "codefuse"},
            "b3": {"devflow_workflow": "w3"},
        }
    )
    inventory = _inventory_with(
        [_template_bot("b1", 11), _template_bot("b2", 12), _template_bot("b3", 13)],
        stub,
    )

    items, total = inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.CLOUD,
        page=1,
        page_size=2,
    )

    assert total == 3
    assert len(items) == 2
    assert len(stub.calls) == 1
    assert set(stub.calls[0]) == {"b1", "b2"}
    assert items[0].template_type == "applicationCoding"
    assert items[0].template_config == {"devflow_workflow": "w1", "token": "raw-secret"}
    assert items[1].template_config == {"template_key": "normalCC", "runtime": "codefuse"}


@pytest.mark.unit
def test_page_slice_without_template_bots_skips_the_port() -> None:
    stub = _StubTemplatePort()
    inventory = _inventory_with([CLOUD], stub)

    items, _ = inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.CLOUD,
        page=1,
        page_size=20,
    )

    assert items
    assert stub.calls == []


@pytest.mark.unit
def test_page_slice_missing_template_row_leaves_config_none() -> None:
    stub = _StubTemplatePort({})  # template row absent for the bot
    inventory = _inventory_with([_template_bot("b1", 11)], stub)

    items, _ = inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine=None,
        deploy_mode=DeployMode.CLOUD,
        page=1,
        page_size=20,
    )

    assert items[0].template_type == "applicationCoding"
    assert items[0].template_config is None


# ── aicoding engine expansion (PR #1719 engine/form vocabulary split) ──────
# ``engine=aicoding`` is claude_code's internal runtime form: the inventory
# expands it to two fetch batches (legacy ``active_engine='aicoding'`` and
# post-split ``active_engine='claude_code'``) then collapses them with
# ``uses_aicoding_runtime``. These cases mock the bot port by engine value.


def _by_engine_page(rows_by_engine: dict):
    """side_effect: dispatch on kwargs['engine'], returning prebuilt rows."""

    def _list(**kwargs):
        rows = rows_by_engine.get(kwargs["engine"], [])
        return {"total": len(rows), "items": list(rows)}

    return _list


def _cloud_row(
    bot_id: str,
    active_engine: str,
    template_type: str | None = None,
    bot_type: str = "personal",
) -> dict:
    return {
        "id": abs(sum(ord(c) for c in bot_id)) % 100_000,
        "bot_id": bot_id,
        "bot_name": bot_id,
        "bot_desc": "",
        "active_engine": active_engine,
        "template_type": template_type,
        "bot_type": bot_type,
        "status": "ACTIVE",
        "owner_id": "u1",
    }


def _list_cloud(inventory, bot, rows_by_engine, *, page=1, page_size=10, engine="aicoding"):
    bot.list_bots_by_conditions.side_effect = _by_engine_page(rows_by_engine)
    return inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine=engine,
        deploy_mode=DeployMode.CLOUD,
        page=page,
        page_size=page_size,
    )


@pytest.mark.unit
def test_aicoding_engine_expands_to_two_batches(service) -> None:
    inventory, bot, _ = service

    items, total = _list_cloud(
        inventory,
        bot,
        {
            "aicoding": [_cloud_row("a1", "aicoding")],
            "claude_code": [_cloud_row("c1", "claude_code", "applicationCoding")],
        },
    )

    engines_called = [c.kwargs["engine"] for c in bot.list_bots_by_conditions.call_args_list]
    assert engines_called == ["aicoding", "claude_code"]
    assert {item.bot_id for item in items} == {"a1", "c1"}
    assert total == 2


@pytest.mark.unit
def test_aicoding_expansion_includes_legacy_and_new_form_rows(service) -> None:
    inventory, bot, _ = service

    items, total = _list_cloud(
        inventory,
        bot,
        {
            "aicoding": [_cloud_row("legacy1", "aicoding", None)],
            "claude_code": [
                _cloud_row("new_app", "claude_code", "applicationCoding"),
                _cloud_row("new_personal", "claude_code", "personalCoding"),
            ],
        },
    )

    assert {item.bot_id for item in items} == {"legacy1", "new_app", "new_personal"}
    assert total == 3


@pytest.mark.unit
def test_aicoding_expansion_excludes_plain_claude_code(service) -> None:
    inventory, bot, _ = service

    items, total = _list_cloud(
        inventory,
        bot,
        {
            "aicoding": [_cloud_row("legacy1", "aicoding")],
            "claude_code": [
                _cloud_row("plain_normalcc", "claude_code", "normalCC"),
                _cloud_row("plain_none", "claude_code", None),
                _cloud_row("form_app", "claude_code", "applicationCoding"),
            ],
        },
    )

    assert {item.bot_id for item in items} == {"legacy1", "form_app"}
    assert total == 2


@pytest.mark.unit
def test_aicoding_expansion_dedups_duplicate_bot_id(service) -> None:
    inventory, bot, _ = service

    items, total = _list_cloud(
        inventory,
        bot,
        {
            "aicoding": [_cloud_row("dup", "aicoding")],
            "claude_code": [_cloud_row("dup", "claude_code", "applicationCoding")],
        },
    )

    assert [item.bot_id for item in items] == ["dup"]
    assert total == 1


@pytest.mark.unit
def test_aicoding_expansion_paginates_after_filter(service) -> None:
    inventory, bot, _ = service

    items, total = _list_cloud(
        inventory,
        bot,
        {
            "aicoding": [_cloud_row(f"a{i}", "aicoding") for i in range(3)],
            "claude_code": [
                _cloud_row("p1", "claude_code", "normalCC"),
                _cloud_row("p2", "claude_code", None),
            ],
        },
        page=1,
        page_size=2,
    )

    # total is taken from the collapsed set (3 aicoding), not the raw 5 rows.
    assert total == 3
    assert len(items) == 2


@pytest.mark.unit
def test_aicoding_expansion_stops_when_upstream_total_is_unreliable(service) -> None:
    """A short page with no reliable ``total`` still terminates the fan-out via
    the ``len(page_items) < fetch_size`` break (the loop's second exit arm), not
    only the total-reaching-exhaustion arm."""
    inventory, bot, _ = service

    def _list(**kwargs):
        if kwargs["engine"] == "aicoding":
            return {"items": [_cloud_row("a1", "aicoding")]}  # no ``total`` key
        if kwargs["engine"] == "claude_code":
            return {"items": [_cloud_row("c1", "claude_code", "applicationCoding")]}
        return {"items": []}

    bot.list_bots_by_conditions.side_effect = _list

    items, total = inventory.list_items(
        owner_id="u1",
        space=NoopBusinessSpaceContext().resolve_current(
            owner_id="u1", header_space_id=None
        ),
        keyword=None,
        engine="aicoding",
        deploy_mode=DeployMode.CLOUD,
        page=1,
        page_size=10,
    )

    assert {item.bot_id for item in items} == {"a1", "c1"}
    assert total == 2


@pytest.mark.unit
def test_aicoding_expansion_forwards_owner_scoping_and_attach_opt_out(service) -> None:
    inventory, bot, _ = service

    _list_cloud(
        inventory,
        bot,
        {
            "aicoding": [_cloud_row("a1", "aicoding")],
            "claude_code": [_cloud_row("c1", "claude_code", "applicationCoding")],
        },
    )

    # Both batches inherit the owner scope and the template-attach opt-out.
    for call in bot.list_bots_by_conditions.call_args_list:
        assert call.kwargs["owner_id"] == "u1"
        assert call.kwargs["attach_templates"] is False


@pytest.mark.unit
def test_aicoding_expansion_skips_non_cloud_bot_types(service) -> None:
    """A fetched row carrying a non-cloud bot_type (e.g. a desktop row leaking
    through) is dropped by the visibility filter, not surfaced as an item."""
    inventory, bot, _ = service

    items, total = _list_cloud(
        inventory,
        bot,
        {
            "aicoding": [
                _cloud_row("a1", "aicoding"),
                _cloud_row("sneaky", "aicoding", bot_type="desktop"),
            ],
            "claude_code": [_cloud_row("c1", "claude_code", "applicationCoding")],
        },
    )

    assert {item.bot_id for item in items} == {"a1", "c1"}
    assert total == 2


@pytest.mark.unit
def test_claude_code_engine_does_not_expand(service) -> None:
    """Regression guard: engine=claude_code stays a single batch with no filter."""
    inventory, bot, _ = service

    items, total = _list_cloud(
        inventory,
        bot,
        {
            "claude_code": [
                _cloud_row("form_app", "claude_code", "applicationCoding"),
                _cloud_row("plain", "claude_code", "normalCC"),
            ],
        },
        engine="claude_code",
    )

    assert [c.kwargs["engine"] for c in bot.list_bots_by_conditions.call_args_list] == [
        "claude_code"
    ]
    # No collapse filter: the plain claude_code row is kept.
    assert {item.bot_id for item in items} == {"form_app", "plain"}
    assert total == 2


@pytest.mark.unit
def test_non_aicoding_engine_single_batch_unchanged(service) -> None:
    """Regression guard: a normal engine stays a single batch with no filter."""
    inventory, bot, _ = service

    items, total = _list_cloud(
        inventory,
        bot,
        {"teclaw": [_cloud_row("t1", "teclaw")]},
        engine="teclaw",
    )

    assert [c.kwargs["engine"] for c in bot.list_bots_by_conditions.call_args_list] == [
        "teclaw"
    ]
    assert {item.bot_id for item in items} == {"t1"}
    assert total == 1


@pytest.mark.unit
def test_cloud_fetch_engines_branches() -> None:
    # aicoding expands only when the normalized value matches the form literal.
    assert _cloud_fetch_engines("aicoding") == ["aicoding", "claude_code"]
    assert _cloud_fetch_engines("AICODING") == ["aicoding", "claude_code"]
    assert _cloud_fetch_engines("aicoding ") == ["aicoding", "claude_code"]
    # Non-matching engines pass through verbatim as a single batch.
    assert _cloud_fetch_engines("claude_code") == ["claude_code"]
    assert _cloud_fetch_engines("teclaw") == ["teclaw"]
    assert _cloud_fetch_engines(None) == [None]


@pytest.mark.unit
def test_select_aicoding_runtime_rows_branches() -> None:
    rows = [
        _cloud_row("a", "aicoding"),
        _cloud_row("b", "claude_code", "applicationCoding"),
        _cloud_row("c", "claude_code", "personalCoding"),
        _cloud_row("d", "claude_code", "normalCC"),
        _cloud_row("e", "claude_code", None),
        _cloud_row("f", "teclaw"),
        # duplicate bot_id 'a' from the other batch — defensive dedup keeps one
        _cloud_row("a", "claude_code", "applicationCoding"),
    ]

    out = _select_aicoding_runtime_rows(rows)

    assert [r["bot_id"] for r in out] == ["a", "b", "c"]
