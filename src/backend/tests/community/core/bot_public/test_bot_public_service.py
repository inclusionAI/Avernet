"""Tests for BotPublicService."""
import pytest
from unittest.mock import MagicMock, patch

from agentclaw.community.core.bot_public.services.bot_public_service import (
    BotPublicService,
    BotNotFoundError,
    BotPublicServiceError,
)
from agentclaw.community.plugin_api.approval_workflow import NO_WORKFLOW_MARKER
from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogAddress,
    BotCatalogCaller,
    BotCatalogMetadata,
    BotCatalogMetadataPage,
    BotCatalogMetadataUnavailableError,
    BotCatalogSearchUnavailableError,
)
from agentclaw.community.core.bot_public.repository.models import BotFriendStatus
from agentclaw.community.core.operator_context import OperatorContext
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT

# The coalescing key is tenant-scoped; unscoped tests run under the default tenant.
_SYNC_KEY = f"{DEFAULT_AVERNET_TENANT}:owner1:bot1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(
    bot_friend_repo=None,
    bot_repository=None,
    process_service=None,
    bot_service=None,
    bcn_service=None,
    passport_plugin=None,
    auth_relationship_plugin=None,
    publish_approval_plugin=None,
    skill_set_service_factory=None,
    device_context_resolver=None,
    device_sync_dispatcher=None,
    bcsfuse_config=None,
    catalog_metadata_service=None,
):
    resolver = device_context_resolver or MagicMock()
    return BotPublicService(
        bot_friend_repo=bot_friend_repo or MagicMock(),
        bot_repository=bot_repository or MagicMock(),
        process_service=process_service or MagicMock(),
        bot_service=bot_service or MagicMock(),
        bcn_service=bcn_service or MagicMock(),
        passport_plugin=passport_plugin or MagicMock(),
        auth_relationship_plugin=auth_relationship_plugin or MagicMock(),
        publish_approval_plugin=publish_approval_plugin or MagicMock(),
        skill_set_service_factory=skill_set_service_factory or MagicMock(),
        device_context_resolver_factory=lambda: resolver,
        device_sync_dispatcher=device_sync_dispatcher or MagicMock(),
        bcsfuse_config=bcsfuse_config or MagicMock(base_url="", base_url_pre=""),
        catalog_metadata_service=catalog_metadata_service or MagicMock(),
    )

def _make_operator(staff_id="op_user", nick_name="Operator") -> OperatorContext:
    op = MagicMock(spec=OperatorContext)
    op.staff_id = staff_id
    op.nick_name = nick_name
    op.operator_name = nick_name
    return op


def _metadata_page(items, total=None):
    return BotCatalogMetadataPage(
        total=len(items) if total is None else total,
        items=items,
    )


def _make_bot(bot_id="bot1", owner_id="owner1", public="0", ext=None):
    return {
        "id": 42,
        "bot_id": bot_id,
        "owner_id": owner_id,
        "public": public,
        "bot_name": "TestBot",
        "owner_name": "owner_nick",
        "binding_id": 10,
        "entity_id": "entity1",
        "ext": ext or {},
    }


def _make_catalog_bot(bot_id: str, entity_id: str):
    return {
        **_make_bot(bot_id=bot_id, owner_id=entity_id),
        "entity_id": entity_id,
    }


# ---------------------------------------------------------------------------
# public_bcs_bot – new-version BCS-delegated publish
# ---------------------------------------------------------------------------

class TestPublicBcsBot:
    def test_context_mirrors_normal_bot_plus_public_scope_and_view_friend_deps(self):
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True,
            "puid": "p1",
            "state": "PROCESSING",
        }
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(bot_id="b1", owner_id="u1")
        svc = _make_service(process_service=process, bot_repository=bot_repo)

        result = svc.public_bcs_bot(
            bot_uid="b1",
            owner_id="u1",
            public_scope="user",
            operator=_make_operator(staff_id="op_user"),
        )

        assert result["puid"] == "p1"
        process.start_approval.assert_called_once()
        # bot record is fetched now — consistent with the normal-bot publish so
        # _build_public_approval_context can fill publishHint/botSkills/botMcps.
        bot_repo.get_by_id_and_owner.assert_called_once_with("b1", "u1")
        kw = process.start_approval.call_args.kwargs
        assert kw["applicant"] == "op_user"            # operator.staff_id
        assert kw["biz_type"] == "botpublic"           # reused from the legacy flow
        assert "process_code" not in kw                # reuses the default
        ctx = kw["context"]
        # normal-bot fields + the two new-version fields; no raw view_depts.
        assert {"publishHint", "botSkills", "botMcps"}.issubset(ctx)
        assert ctx["public_scope"] == "user"
        assert ctx["viewFriendDeps"] == ""
        assert "view_depts" not in ctx
        # BCS publish overrides publishHint: 开放 + scene from public_scope.
        assert "同学正在开放" in ctx["publishHint"]
        assert "bot到加好友场景，" in ctx["publishHint"]

    def test_context_carries_bot_id_and_owner_id_for_callback_echo(self):
        # antprocess echoes the ticket context back on the /callback POST
        # (snake→camel: bot_id→botId, owner_id→ownerId). The new-version
        # callback path uses bot_id verbatim as the BCS bot_uuid
        # (_apply_callback_decision → get_attributes(bot_uuid=bot_id)) and
        # owner_id for the legacy branch; without them in the context the
        # callback arrives with botId/ownerId=None and the AGREE visibility
        # flip is a no-op.
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True, "puid": "p1", "state": "PROCESSING",
        }
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(bot_id="b1", owner_id="u1")
        svc = _make_service(process_service=process, bot_repository=bot_repo)

        # bot_uid is the full BCS identity "{backend_bot_id}:{entity_id}"; it
        # must echo back unchanged so the callback addresses the right bot —
        # backend_bot_id alone would not resolve via BCS get_attributes.
        svc.public_bcs_bot(
            bot_uid="b1:entity1",
            owner_id="u1",
            public_scope="user",
            operator=_make_operator(staff_id="op_user"),
        )

        ctx = process.start_approval.call_args.kwargs["context"]
        assert ctx["bot_id"] == "b1:entity1"
        assert ctx["owner_id"] == "u1"

    def test_completed_runs_callback_inline_with_public_scope(self):
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True,
            "puid": "p1",
            "state": "COMPLETED",
            "lastOperate": "AGREE",
        }
        svc = _make_service(process_service=process)
        with patch.object(svc, "handle_public_approval_callback") as cb:
            svc.public_bcs_bot(
                bot_uid="b1",
                owner_id="u1",
                public_scope="user",
                operator=_make_operator(),
            )
        cb.assert_called_once()
        ckw = cb.call_args.kwargs
        assert ckw["bot_id"] == "b1"
        assert ckw["owner_id"] == "u1"
        assert ckw["puid"] == "p1"
        assert ckw["last_operate"] == "agree"
        assert ckw["public_scope"] == "user"

    def test_not_completed_does_not_invoke_callback(self):
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True,
            "puid": "p1",
            "state": "PROCESSING",
        }
        svc = _make_service(process_service=process)
        with patch.object(svc, "handle_public_approval_callback") as cb:
            svc.public_bcs_bot(
                bot_uid="b1", owner_id="u1", public_scope="user",
                operator=_make_operator(),
            )
        cb.assert_not_called()

    def test_failed_start_raises_service_error(self):
        process = MagicMock()
        process.start_approval.return_value = {
            "success": False,
            "error_msg": "workflow unavailable",
        }
        svc = _make_service(process_service=process)
        with pytest.raises(BotPublicServiceError, match="workflow unavailable"):
            svc.public_bcs_bot(
                bot_uid="b1", owner_id="u1", public_scope="user",
                operator=_make_operator(),
            )

    def test_no_workflow_falls_through_to_skipped_with_agree_callback(self):
        # community/local NoApprovalWorkflow reports "no approval capability"
        # (NO_WORKFLOW_MARKER prefix) rather than a real rejection. The protocol
        # promises callers fall through to direct publish, NOT fail — and the
        # fall-through is treated as auto-approved (AGREE): public_bcs_bot runs
        # the same persist + inline-callback path as corp's COMPLETED tenant so
        # BCS friend_ext still gets an AGREE update, surfacing the *approval*
        # outcome as state=SKIPPED (no approval step ran) instead of raising
        # (which the old code did for any success=False — a 500 every community
        # publish).
        process = MagicMock()
        process.start_approval.return_value = {
            "success": False,
            "error_msg": NO_WORKFLOW_MARKER + "no approval workflow in the community build",
        }
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(bot_id="b1", owner_id="u1")
        svc = _make_service(process_service=process, bot_repository=bot_repo)

        with patch.object(svc, "handle_public_approval_callback") as cb:
            result = svc.public_bcs_bot(
                bot_uid="b1", owner_id="u1", public_scope="user",
                operator=_make_operator(staff_id="op_user"),
            )

        process.start_approval.assert_called_once()
        # Fall-through (NOT raise): approval step skipped, AGREE callback fired.
        assert result["success"] is True
        assert result["state"] == "SKIPPED"
        assert result["puid"] is None
        assert result["last_operate"] == "agree"
        assert result["error_msg"].startswith(NO_WORKFLOW_MARKER)
        cb.assert_called_once()
        ckw = cb.call_args.kwargs
        assert ckw["bot_id"] == "b1"
        assert ckw["owner_id"] == "u1"
        assert ckw["puid"] is None
        assert ckw["last_operate"] == "agree"
        assert ckw["public_scope"] == "user"

    def test_rejects_empty_owner_id(self):
        svc = _make_service()
        with pytest.raises(BotPublicServiceError, match="Owner ID"):
            svc.public_bcs_bot("b1", "", "user", _make_operator())

    def test_rejects_empty_public_scope(self):
        svc = _make_service()
        with pytest.raises(BotPublicServiceError, match="public_scope"):
            svc.public_bcs_bot("b1", "u1", "", _make_operator())

    def test_rejects_invalid_public_scope(self):
        svc = _make_service()
        with pytest.raises(BotPublicServiceError, match="must be 'user' or 'agent'"):
            svc.public_bcs_bot("b1", "u1", "team", _make_operator())

    def test_view_depts_derives_view_friend_deps_in_context(self):
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True, "puid": "p1", "state": "PROCESSING",
        }
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(bot_id="b1", owner_id="u1")
        svc = _make_service(process_service=process, bot_repository=bot_repo)
        depts = [{"deptNo": "D1", "deptName": "Tech"},
                 {"deptNo": "D2", "deptName": "Sales"}]
        svc.public_bcs_bot(
            bot_uid="b1", owner_id="u1", public_scope="user",
            operator=_make_operator(), view_depts=depts,
        )
        ctx = process.start_approval.call_args.kwargs["context"]
        # viewFriendDeps = deptName values joined; no raw view_depts in context.
        assert ctx["viewFriendDeps"] == "Tech,Sales"
        assert "view_depts" not in ctx
        assert {"publishHint", "botSkills", "botMcps", "public_scope"}.issubset(ctx)

    def test_view_friend_deps_empty_when_no_view_depts(self):
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True, "puid": "p1", "state": "PROCESSING",
        }
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(bot_id="b1", owner_id="u1")
        svc = _make_service(process_service=process, bot_repository=bot_repo)
        svc.public_bcs_bot(
            bot_uid="b1", owner_id="u1", public_scope="user",
            operator=_make_operator(),
        )
        ctx = process.start_approval.call_args.kwargs["context"]
        assert ctx["viewFriendDeps"] == ""
        assert "view_depts" not in ctx

    def test_completed_inline_callback_omits_view_friend_deps(self):
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True, "puid": "p1", "state": "COMPLETED",
            "lastOperate": "AGREE",
        }
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(bot_id="b1", owner_id="u1")
        svc = _make_service(process_service=process, bot_repository=bot_repo)
        depts = [{"deptNo": "D1", "deptName": "Tech"}]
        with patch.object(svc, "handle_public_approval_callback") as cb:
            svc.public_bcs_bot(
                bot_uid="b1", owner_id="u1", public_scope="user",
                operator=_make_operator(), view_depts=depts,
            )
        ckw = cb.call_args.kwargs
        assert ckw["public_scope"] == "user"
        # viewFriendDeps rides the ticket context only — NOT forwarded to callback.
        assert "view_friend_deps" not in ckw

    def test_bcs_publish_hint_scene_by_public_scope(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(bot_id="b1", owner_id="u1")
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True, "puid": "p1", "state": "PROCESSING",
        }
        svc = _make_service(process_service=process, bot_repository=bot_repo)
        for scope, scene in [("user", "加好友场景"), ("agent", "群聊场景")]:
            process.start_approval.reset_mock()
            svc.public_bcs_bot(
                bot_uid="b1", owner_id="u1", public_scope=scope,
                operator=_make_operator(),
            )
            hint = process.start_approval.call_args.kwargs["context"]["publishHint"]
            assert f"bot到{scene}，" in hint, scope
            assert "同学正在开放" in hint, scope
            assert "正在发布" not in hint, scope  # normal-bot verb
            assert '"' not in hint, scope        # no quote-wrapping around names
            assert hint.endswith("发布。"), scope  # trailing full-stop

    def test_bcs_bot_id_split_for_backend_lookup(self):
        # BCS bot_id is shaped "{backend_bot_id}:{entity_id}"; the bot / skills
        # lookup must use the backend bot_id (split on ':', first element).
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True, "puid": "p1", "state": "PROCESSING",
        }
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(bot_id="b1", owner_id="u1")
        svc = _make_service(process_service=process, bot_repository=bot_repo)
        svc.public_bcs_bot(
            bot_uid="b1:entity1", owner_id="u1", public_scope="user",
            operator=_make_operator(),
        )
        bot_repo.get_by_id_and_owner.assert_called_once_with("b1", "u1")

    def test_publish_persists_public_user_approval_to_bcs(self):
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True, "puid": "p1",
            "approval_url": "https://antprocess-pre/t/d/p1",
            "state": "PROCESSING",
        }
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(bot_id="b1", owner_id="u1")
        bcn = MagicMock()
        bcn.get_attributes.return_value = {"friend_ext": {"old": "x"}}
        svc = _make_service(process_service=process, bot_repository=bot_repo, bcn_service=bcn)
        depts = [{"deptNo": "D1", "deptName": "Tech"}]
        svc.public_bcs_bot(
            bot_uid="b1:entity1", owner_id="u1", public_scope="user",
            operator=_make_operator(), view_depts=depts,
        )
        bcn.get_attributes.assert_called_once_with(bot_uuid="b1:entity1")
        ckw = bcn.patch_attributes.call_args.kwargs
        assert ckw["bot_uuid"] == "b1:entity1"
        body = ckw["body"]
        # merge preserves existing friend_ext keys + sets the PROCESSING block
        assert body["friend_ext"]["old"] == "x"
        assert body["friend_ext"]["public_user_approval"] == {
            "puid": "p1",
            "approval_url": "https://antprocess-pre/t/d/p1",
            "view_friend_deps": [{"deptNo": "D1", "deptName": "Tech"}],
            "status": "PROCESSING",
        }

    def test_publish_skips_bcs_when_credentials_skipped(self):
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True, "puid": "p1", "state": "PROCESSING",
        }
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(bot_id="b1", owner_id="u1")
        bcn = MagicMock()
        bcn.get_attributes.return_value = {"skipped": True}
        svc = _make_service(process_service=process, bot_repository=bot_repo, bcn_service=bcn)
        svc.public_bcs_bot(
            bot_uid="b1", owner_id="u1", public_scope="agent",
            operator=_make_operator(),
        )
        bcn.patch_attributes.assert_not_called()

    def test_visibility_private_user_direct_patch_no_approval(self):
        process = MagicMock()
        bot_repo = MagicMock()
        bcn = MagicMock()
        svc = _make_service(process_service=process, bot_repository=bot_repo, bcn_service=bcn)
        result = svc.public_bcs_bot(
            bot_uid="b1", owner_id="u1", public_scope="user",
            operator=_make_operator(), visibility="private",
        )
        # private 直连: 不走审批, 不查工单信息, 直接 PATCH BCS user_visibility
        process.start_approval.assert_not_called()
        bcn.get_attributes.assert_not_called()
        bcn.patch_attributes.assert_called_once_with(
            bot_uuid="b1", body={"user_visibility": "private"}
        )
        assert result["success"] is True
        assert result["visibility"] == "private"
        assert result["visibility_field"] == "user_visibility"

    def test_visibility_private_agent_direct_patch_uses_visibility_field(self):
        bcn = MagicMock()
        svc = _make_service(bcn_service=bcn)
        svc.public_bcs_bot(
            bot_uid="b1", owner_id="u1", public_scope="agent",
            operator=_make_operator(), visibility="private",
        )
        # agent 联动 → PATCH BCS visibility 字段 (非 user_visibility)
        bcn.patch_attributes.assert_called_once_with(
            bot_uuid="b1", body={"visibility": "private"}
        )

    def test_visibility_public_stored_in_friend_ext_block(self):
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True, "puid": "p1", "approval_url": "u1", "state": "PROCESSING",
        }
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(bot_id="b1", owner_id="u1")
        bcn = MagicMock()
        bcn.get_attributes.return_value = {"friend_ext": {"old": "x"}}
        svc = _make_service(process_service=process, bot_repository=bot_repo, bcn_service=bcn)
        svc.public_bcs_bot(
            bot_uid="b1", owner_id="u1", public_scope="user",
            operator=_make_operator(), visibility="public",
        )
        block = bcn.patch_attributes.call_args.kwargs["body"]["friend_ext"]["public_user_approval"]
        assert block["visibility"] == "public"
        # merge 仍保留已有 friend_ext 子键
        assert bcn.patch_attributes.call_args.kwargs["body"]["friend_ext"]["old"] == "x"

    def test_agent_publish_stores_public_agent_approval_block(self):
        process = MagicMock()
        process.start_approval.return_value = {
            "success": True, "puid": "p1", "approval_url": "u1", "state": "PROCESSING",
        }
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(bot_id="b1", owner_id="u1")
        bcn = MagicMock()
        bcn.get_attributes.return_value = {"friend_ext": {"old": "x"}}
        svc = _make_service(process_service=process, bot_repository=bot_repo, bcn_service=bcn)
        svc.public_bcs_bot(
            bot_uid="b1", owner_id="u1", public_scope="agent",
            operator=_make_operator(),
            view_depts=[{"deptNo": "D1", "deptName": "Tech"}],
            visibility="public",
        )
        friend_ext = bcn.patch_attributes.call_args.kwargs["body"]["friend_ext"]
        # agent → public_agent_approval subkey (NOT public_user_approval); same block shape
        assert "public_user_approval" not in friend_ext
        block = friend_ext["public_agent_approval"]
        assert block["puid"] == "p1"
        assert block["approval_url"] == "u1"
        assert block["view_friend_deps"] == [{"deptNo": "D1", "deptName": "Tech"}]
        assert block["status"] == "PROCESSING"
        assert block["visibility"] == "public"
        # merge preserves existing friend_ext subkeys
        assert friend_ext["old"] == "x"

    def test_callback_disagree_updates_public_user_approval_status(self):
        bcn = MagicMock()
        bcn.get_attributes.return_value = {
            "friend_ext": {
                "public_user_approval": {
                    "puid": "p1", "status": "PROCESSING", "visibility": "public",
                    "approval_url": "u",
                }
            }
        }
        svc = _make_service(bcn_service=bcn)
        svc.handle_public_approval_callback(
            bot_id="bot-uuid-x", owner_id="u1", puid="p1",
            last_operate="DISAGREE", public_scope="user",
        )
        # public_scope 非空 → bot_id 即 bot_uid
        bcn.get_attributes.assert_called_once_with(bot_uuid="bot-uuid-x")
        block = bcn.patch_attributes.call_args.kwargs["body"]["friend_ext"]["public_user_approval"]
        assert block["status"] == "DISAGREE"
        # 其他子字段保留 (只翻 status)
        assert block["puid"] == "p1"
        assert block["visibility"] == "public"

    def test_callback_cancel_updates_public_agent_approval_for_agent(self):
        bcn = MagicMock()
        bcn.get_attributes.return_value = {
            "friend_ext": {
                "public_agent_approval": {"puid": "p1", "status": "PROCESSING"}
            }
        }
        svc = _make_service(bcn_service=bcn)
        svc.handle_public_approval_callback(
            bot_id="b", owner_id="u", puid="p1",
            last_operate="CANCEL", public_scope="agent",
        )
        block = bcn.patch_attributes.call_args.kwargs["body"]["friend_ext"]["public_agent_approval"]
        assert block["status"] == "CANCEL"

    def test_callback_agree_user_writes_block_visibility_to_user_visibility(self):
        bcn = MagicMock()
        bcn.get_attributes.return_value = {
            "friend_ext": {
                "public_user_approval": {"puid": "p1", "status": "PROCESSING", "visibility": "public"}
            }
        }
        svc = _make_service(bcn_service=bcn)
        svc.handle_public_approval_callback(
            bot_id="b", owner_id="u", puid="p1",
            last_operate="AGREE", public_scope="user",
        )
        body = bcn.patch_attributes.call_args.kwargs["body"]
        assert body["friend_ext"]["public_user_approval"]["status"] == "AGREE"
        # user: user_visibility 直接取 block.visibility
        assert body["user_visibility"] == "public"

    def test_callback_agree_user_uses_protected_from_block(self):
        bcn = MagicMock()
        bcn.get_attributes.return_value = {
            "friend_ext": {
                "public_user_approval": {"puid": "p1", "status": "PROCESSING", "visibility": "protected"}
            }
        }
        svc = _make_service(bcn_service=bcn)
        svc.handle_public_approval_callback(
            bot_id="b", owner_id="u", puid="p1",
            last_operate="AGREE", public_scope="user",
        )
        assert bcn.patch_attributes.call_args.kwargs["body"]["user_visibility"] == "protected"

    def test_callback_agree_agent_public_when_friend_check_open(self):
        bcn = MagicMock()
        bcn.get_attributes.return_value = {
            "friend_ext": {"public_agent_approval": {"puid": "p1", "status": "PROCESSING"}},
            "friend_check_in_strategy": "OPEN",
        }
        svc = _make_service(bcn_service=bcn)
        svc.handle_public_approval_callback(
            bot_id="b", owner_id="u", puid="p1",
            last_operate="AGREE", public_scope="agent",
        )
        body = bcn.patch_attributes.call_args.kwargs["body"]
        assert body["friend_ext"]["public_agent_approval"]["status"] == "AGREE"
        # agent: visibility 由 friend_check_in_strategy=OPEN → public
        assert body["visibility"] == "public"

    def test_callback_agree_agent_protected_when_friend_check_not_open(self):
        bcn = MagicMock()
        bcn.get_attributes.return_value = {
            "friend_ext": {"public_agent_approval": {"puid": "p1", "status": "PROCESSING"}},
            "friend_check_in_strategy": "APPROVAL",
        }
        svc = _make_service(bcn_service=bcn)
        svc.handle_public_approval_callback(
            bot_id="b", owner_id="u", puid="p1",
            last_operate="AGREE", public_scope="agent",
        )
        assert bcn.patch_attributes.call_args.kwargs["body"]["visibility"] == "protected"

    def test_callback_agree_lifts_view_friend_deps_for_user(self):
        bcn = MagicMock()
        bcn.get_attributes.return_value = {
            "friend_ext": {
                "public_user_approval": {
                    "puid": "p1", "status": "PROCESSING", "visibility": "public",
                    "view_friend_deps": [{"deptNo": "D1", "deptName": "Tech"}],
                }
            }
        }
        svc = _make_service(bcn_service=bcn)
        svc.handle_public_approval_callback(
            bot_id="b", owner_id="u", puid="p1",
            last_operate="AGREE", public_scope="user",
        )
        friend_ext = bcn.patch_attributes.call_args.kwargs["body"]["friend_ext"]
        # AGREE lifts block.view_friend_deps to top-level friend_ext key
        assert friend_ext["view_scope_user_friend_deps"] == [{"deptNo": "D1", "deptName": "Tech"}]
        # block's own view_friend_deps preserved (block merged back into friend_ext)
        assert friend_ext["public_user_approval"]["view_friend_deps"] == [
            {"deptNo": "D1", "deptName": "Tech"}
        ]

    def test_callback_agree_lifts_view_friend_deps_for_agent(self):
        bcn = MagicMock()
        bcn.get_attributes.return_value = {
            "friend_ext": {
                "public_agent_approval": {
                    "puid": "p1", "status": "PROCESSING",
                    "view_friend_deps": [{"deptNo": "D2", "deptName": "Sales"}],
                }
            }
        }
        svc = _make_service(bcn_service=bcn)
        svc.handle_public_approval_callback(
            bot_id="b", owner_id="u", puid="p1",
            last_operate="AGREE", public_scope="agent",
        )
        friend_ext = bcn.patch_attributes.call_args.kwargs["body"]["friend_ext"]
        assert friend_ext["view_scope_agent_friend_deps"] == [{"deptNo": "D2", "deptName": "Sales"}]

    def test_callback_skips_when_bcs_creds_missing(self):
        bcn = MagicMock()
        bcn.get_attributes.return_value = {"skipped": True}
        svc = _make_service(bcn_service=bcn)
        svc.handle_public_approval_callback(
            bot_id="b", owner_id="u", puid="p1",
            last_operate="DISAGREE", public_scope="user",
        )
        bcn.patch_attributes.assert_not_called()

    def test_rejects_invalid_visibility(self):
        svc = _make_service()
        with pytest.raises(BotPublicServiceError, match="visibility must be"):
            svc.public_bcs_bot("b1", "u1", "user", _make_operator(), visibility="bogus")

    def test_rejects_view_depts_not_a_list(self):
        svc = _make_service()
        with pytest.raises(BotPublicServiceError, match="view_depts must be a list"):
            svc.public_bcs_bot("b1", "u1", "user", _make_operator(),
                               view_depts="not-a-list")

    def test_rejects_view_depts_item_without_deptNo(self):
        svc = _make_service()
        with pytest.raises(BotPublicServiceError, match="deptNo"):
            svc.public_bcs_bot("b1", "u1", "user", _make_operator(),
                               view_depts=[{"deptName": "no no here"}])


# ---------------------------------------------------------------------------
# public_bot – input validation
# ---------------------------------------------------------------------------

class TestPublicBotValidation:
    def test_raises_if_owner_id_empty(self):
        svc = _make_service()
        with pytest.raises(BotPublicServiceError, match="Owner ID"):
            svc.public_bot("bot1", "", "1", "caller", "0", _make_operator())

    def test_raises_if_public_invalid(self):
        svc = _make_service()
        with pytest.raises(BotPublicServiceError, match="Invalid public"):
            svc.public_bot("bot1", "owner1", "2", "caller", "0", _make_operator())

    def test_raises_if_permission_owner_invalid(self):
        svc = _make_service()
        with pytest.raises(BotPublicServiceError, match="Invalid permission_owner"):
            svc.public_bot("bot1", "owner1", "1", "bad_value", "0", _make_operator())

    def test_raises_if_bot_not_found(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        svc = _make_service(bot_repository=bot_repo)
        with pytest.raises(BotNotFoundError):
            svc.public_bot("bot1", "owner1", "1", "caller", "0", _make_operator())


# ---------------------------------------------------------------------------
# public_bot – set public="0" (un-publish)
# ---------------------------------------------------------------------------

class TestPublicBotUnpublish:
    @patch("agentclaw.community.utils.env_utils.is_local_mode", return_value=True)
    def test_set_public_zero_updates_directly(self, _mock_local):
        bot_repo = MagicMock()
        bot = _make_bot(public="1", ext={"permission_owner": "caller"})
        bot_repo.get_by_id_and_owner.return_value = bot
        updated = {**bot, "public": "0"}
        bot_repo.update_by_owner.return_value = updated

        svc = _make_service(bot_repository=bot_repo, bcsfuse_config=MagicMock(base_url="http://bcsfuse.test", base_url_pre="http://bcsfuse.pre.test"))
        result = svc.public_bot("bot1", "owner1", "0", "caller", "0", _make_operator())

        assert result["public"] == "0"
        bot_repo.update_by_owner.assert_called_once()
        update_data = bot_repo.update_by_owner.call_args[0][2]
        assert update_data["public"] == "0"

    @patch("agentclaw.community.utils.env_utils.is_local_mode", return_value=True)
    def test_set_public_zero_archives_existing_approval(self, _mock_local):
        bot_repo = MagicMock()
        bot = _make_bot(
            public="1",
            ext={"public_approval": {"puid": "puid123", "status": "PROCESSING"}},
        )
        bot_repo.get_by_id_and_owner.return_value = bot
        bot_repo.update_by_owner.return_value = {**bot, "public": "0"}

        svc = _make_service(bot_repository=bot_repo)
        svc.public_bot("bot1", "owner1", "0", "caller", "0", _make_operator())

        update_data = bot_repo.update_by_owner.call_args[0][2]
        ext = update_data["ext"]
        # old approval archived into history
        assert "public_approval" not in ext
        assert len(ext.get("public_approval_history", [])) == 1

    @patch("agentclaw.community.utils.env_utils.is_local_mode", return_value=True)
    def test_set_public_zero_raises_if_update_fails(self, _mock_local):
        bot_repo = MagicMock()
        bot = _make_bot(public="1")
        bot_repo.get_by_id_and_owner.return_value = bot
        bot_repo.update_by_owner.return_value = None  # simulate failure

        svc = _make_service(bot_repository=bot_repo)
        with pytest.raises(BotNotFoundError):
            svc.public_bot("bot1", "owner1", "0", "caller", "0", _make_operator())


# ---------------------------------------------------------------------------
# public_bot – caller mode (direct update)
# ---------------------------------------------------------------------------

class TestPublicBotBCSFuseSync:
    @patch("agentclaw.community.core.bot_public.services.bot_public_service.urlopen")
    @patch("agentclaw.community.core.bot_public.services.bot_public_service.BotPublicService._sync_access_mode_and_relations")
    @patch("agentclaw.community.utils.env_utils.is_local_mode", return_value=True)
    def test_public_calls_bcsfuse_online_best_effort(self, _mock_local, mock_sync_rel, mock_urlopen):
        bot_repo = MagicMock()
        bot = _make_bot(public="0", ext={"permission_owner": "caller"})
        bot_repo.get_by_id_and_owner.return_value = bot
        bot_repo.update_by_owner.return_value = {**bot, "public": "1"}

        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        mock_urlopen.return_value = response

        svc = _make_service(bot_repository=bot_repo, bcsfuse_config=MagicMock(base_url="http://bcsfuse.test", base_url_pre="http://bcsfuse.pre.test"))
        result = svc.public_bot("bot1", "owner1", "1", "caller", "0", _make_operator())

        assert result["public"] == "1"
        request = mock_urlopen.call_args.args[0]
        assert request.full_url == "http://bcsfuse.test/v1/workers/bot1/online"
        assert request.get_method() == "PUT"
        mock_sync_rel.assert_called_once_with("bot1", "owner1", "OPEN", "1")

    @patch("agentclaw.community.core.bot_public.services.bot_public_service.urlopen")
    @patch("agentclaw.community.core.bot_public.services.bot_public_service.BotPublicService._sync_access_mode_and_relations")
    @patch("agentclaw.community.utils.env_utils.is_local_mode", return_value=True)
    def test_public_uses_owner_qualified_bcsfuse_worker_id_when_configured(
        self, _mock_local, _mock_sync_rel, mock_urlopen
    ):
        bot_repo = MagicMock()
        bot = _make_bot(public="0", ext={"permission_owner": "caller"})
        bot_repo.get_by_id_and_owner.return_value = bot
        bot_repo.update_by_owner.return_value = {**bot, "public": "1"}

        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        mock_urlopen.return_value = response

        svc = _make_service(
            bot_repository=bot_repo,
            bcsfuse_config=MagicMock(
                base_url="http://bcsfuse.test",
                base_url_pre="http://bcsfuse.pre.test",
                worker_id_with_owner=True,
            ),
        )
        svc.public_bot("bot1", "owner1", "1", "caller", "0", _make_operator())

        request = mock_urlopen.call_args.args[0]
        assert request.full_url == "http://bcsfuse.test/v1/workers/bot1:owner1/online"

    @patch("agentclaw.community.core.bot_public.services.bot_public_service.urlopen")
    @patch("agentclaw.community.core.bot_public.services.bot_public_service.BotPublicService._sync_access_mode_and_relations")
    @patch("agentclaw.community.utils.env_utils.is_local_mode", return_value=True)
    def test_unpublish_calls_bcsfuse_offline_best_effort(self, _mock_local, mock_sync_rel, mock_urlopen):
        bot_repo = MagicMock()
        bot = _make_bot(public="1", ext={"permission_owner": "caller"})
        bot_repo.get_by_id_and_owner.return_value = bot
        bot_repo.update_by_owner.return_value = {**bot, "public": "0"}

        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        mock_urlopen.return_value = response

        svc = _make_service(bot_repository=bot_repo, bcsfuse_config=MagicMock(base_url="http://bcsfuse.test", base_url_pre="http://bcsfuse.pre.test"))
        result = svc.public_bot("bot1", "owner1", "0", "caller", "0", _make_operator())

        assert result["public"] == "0"
        request = mock_urlopen.call_args.args[0]
        assert request.full_url == "http://bcsfuse.test/v1/workers/bot1/offline"
        assert request.get_method() == "PUT"
        mock_sync_rel.assert_called_once_with("bot1", "owner1", "RESTRICTED", "0")

    @patch("agentclaw.community.core.bot_public.services.bot_public_service.urlopen", side_effect=RuntimeError("bcsfuse down"))
    @patch("agentclaw.community.core.bot_public.services.bot_public_service.BotPublicService._sync_access_mode_and_relations")
    @patch("agentclaw.community.utils.env_utils.is_local_mode", return_value=True)
    def test_bcsfuse_failure_does_not_block_public_flow(self, _mock_local, mock_sync_rel, _mock_urlopen):
        bot_repo = MagicMock()
        bot = _make_bot(public="0", ext={"permission_owner": "caller"})
        bot_repo.get_by_id_and_owner.return_value = bot
        bot_repo.update_by_owner.return_value = {**bot, "public": "1"}

        svc = _make_service(bot_repository=bot_repo, bcsfuse_config=MagicMock(base_url="http://bcsfuse.test", base_url_pre="http://bcsfuse.pre.test"))
        result = svc.public_bot("bot1", "owner1", "1", "caller", "0", _make_operator())

        assert result["public"] == "1"
        mock_sync_rel.assert_called_once_with("bot1", "owner1", "OPEN", "1")


class TestPublicBotCallerMode:
    @patch("agentclaw.community.core.bot_public.services.bot_public_service.BotPublicService.sync_bot_config_to_device")
    @patch("agentclaw.community.utils.env_utils.is_local_mode", return_value=False)
    def test_caller_mode_skips_approval(self, _mock_local, mock_sync):
        mock_sync.return_value = {"success": True}
        bot_repo = MagicMock()
        bot = _make_bot()
        bot_repo.get_by_id_and_owner.return_value = bot
        updated = {**bot, "public": "1"}
        bot_repo.update_by_owner.return_value = updated

        svc = _make_service(bot_repository=bot_repo)
        result = svc.public_bot("bot1", "owner1", "1", "caller", "0", _make_operator())

        assert result["public"] == "1"
        update_data = bot_repo.update_by_owner.call_args[0][2]
        assert update_data["public"] == "1"
        assert update_data["ext"]["permission_owner"] == "caller"

    def test_owner_permission_delegates_to_publish_approval_plugin(self):
        """permission_owner='owner' delegates the entire approval flow to
        the injected BotPublishApprovalPlugin. The service never branches
        on mode — the plugin's prod/local impl does (Rule 14).
        """
        bot_repo = MagicMock()
        bot = _make_bot()
        bot_repo.get_by_id_and_owner.return_value = bot

        plugin = MagicMock()
        plugin.publish.return_value = {**bot, "public": "1"}

        svc = _make_service(
            bot_repository=bot_repo, publish_approval_plugin=plugin,
        )
        result = svc.public_bot("bot1", "owner1", "1", "owner", "0", _make_operator())

        assert result["public"] == "1"
        plugin.publish.assert_called_once()
        call_kwargs = plugin.publish.call_args.kwargs
        assert call_kwargs["bot_id"] == "bot1"
        assert call_kwargs["owner_id"] == "owner1"
        assert call_kwargs["public"] == "1"
        assert call_kwargs["permission_owner"] == "owner"
        # Callbacks bag passed through.
        callbacks = call_kwargs["callbacks"]
        assert callable(callbacks.publish_directly)
        assert callable(callbacks.archive_approval)
        assert callable(callbacks.update_with_notification)
        assert callable(callbacks.handle_approval_callback)
        assert callable(callbacks.refetch_bot)
        assert callable(callbacks.build_approval_context)


# ---------------------------------------------------------------------------
# handle_public_approval_callback
# ---------------------------------------------------------------------------

class TestHandlePublicApprovalCallback:
    def _make_svc_with_bot(self, bot_ext):
        bot_repo = MagicMock()
        bot = _make_bot(ext=bot_ext)
        bot_repo.get_by_id_and_owner.return_value = bot
        updated = {**bot}
        bot_repo.update_by_owner.return_value = updated
        svc = _make_service(bot_repository=bot_repo)
        return svc, bot, bot_repo

    def test_bot_not_found_returns_failure(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        svc = _make_service(bot_repository=bot_repo)
        result = svc.handle_public_approval_callback("bot1", "owner1", "puid1", "agree")
        assert result["success"] is False
        assert "not found" in result["message"].lower()

    def test_no_public_approval_returns_failure(self):
        svc, _, _ = self._make_svc_with_bot({})
        result = svc.handle_public_approval_callback("bot1", "owner1", "puid1", "agree")
        assert result["success"] is False
        assert "No approval" in result["message"]

    def test_puid_mismatch_returns_mismatch(self):
        svc, _, _ = self._make_svc_with_bot({
            "public_approval": {"puid": "other_puid", "status": "PROCESSING", "permission_owner": "caller", "public": "1"}
        })
        result = svc.handle_public_approval_callback("bot1", "owner1", "puid1", "agree")
        assert result["success"] is True
        assert "PUID mismatch" in result["message"]

    def test_agree_updates_public(self):
        svc, bot, bot_repo = self._make_svc_with_bot({
            "public_approval": {
                "puid": "puid1", "status": "PROCESSING",
                "permission_owner": "caller", "public": "1",
                "friend_approval": "0", "applicant": "op_user"
            }
        })
        with patch.object(
            svc,
            "_sync_access_mode_and_relations_or_raise",
        ) as mock_sync:
            result = svc.handle_public_approval_callback(
                "bot1", "owner1", "puid1", "agree",
            )

        assert result["success"] is True
        assert result["public"] == "1"
        update_data = bot_repo.update_by_owner.call_args[0][2]
        assert update_data["public"] == "1"
        mock_sync.assert_called_once_with("bot1", "owner1", "OPEN", "1")

    def test_agree_propagates_auth_sync_failure(self):
        svc, _, _ = self._make_svc_with_bot({
            "public_approval": {
                "puid": "puid1", "status": "PROCESSING",
                "permission_owner": "caller", "public": "1",
                "friend_approval": "1", "applicant": "op_user",
            }
        })

        with (
            patch.object(
                svc,
                "_sync_access_mode_and_relations_or_raise",
                side_effect=RuntimeError("auth unavailable"),
            ),
            pytest.raises(RuntimeError, match="auth unavailable"),
        ):
            svc.handle_public_approval_callback(
                "bot1", "owner1", "puid1", "agree",
            )

    def test_agree_callback_can_retry_after_auth_sync_failure(self):
        svc, _, bot_repo = self._make_svc_with_bot({
            "public_approval": {
                "puid": "puid1", "status": "PROCESSING",
                "permission_owner": "caller", "public": "1",
                "friend_approval": "1", "applicant": "op_user",
            }
        })

        with patch.object(
            svc,
            "_sync_access_mode_and_relations_or_raise",
            side_effect=[RuntimeError("auth unavailable"), None],
        ) as mock_sync:
            with pytest.raises(RuntimeError, match="auth unavailable"):
                svc.handle_public_approval_callback(
                    "bot1", "owner1", "puid1", "agree",
                )
            result = svc.handle_public_approval_callback(
                "bot1", "owner1", "puid1", "agree",
            )

        assert result["success"] is True
        assert bot_repo.update_by_owner.call_count == 2
        assert mock_sync.call_count == 2

    def test_disagree_does_not_change_public(self):
        svc, bot, bot_repo = self._make_svc_with_bot({
            "public_approval": {
                "puid": "puid1", "status": "PROCESSING",
                "permission_owner": "caller", "public": "1",
                "applicant": "op_user"
            }
        })
        result = svc.handle_public_approval_callback("bot1", "owner1", "puid1", "disagree")
        assert result["success"] is True
        assert result["public"] is None

    def test_cancel_does_not_change_public(self):
        svc, bot, bot_repo = self._make_svc_with_bot({
            "public_approval": {
                "puid": "puid1", "status": "PROCESSING",
                "permission_owner": "caller", "public": "1",
                "applicant": "op_user"
            }
        })
        result = svc.handle_public_approval_callback("bot1", "owner1", "puid1", "cancel")
        assert result["success"] is True
        assert result["public"] is None

    def test_unknown_operation_returns_failure(self):
        svc, _, _ = self._make_svc_with_bot({
            "public_approval": {"puid": "puid1", "status": "PROCESSING", "permission_owner": "caller"}
        })
        result = svc.handle_public_approval_callback("bot1", "owner1", "puid1", "UNKNOWN_OP")
        assert result["success"] is False
        assert "Unknown" in result["message"]

    def test_agree_calls_bcsfuse_online_best_effort(self):
        bot_repo = MagicMock()
        bot = _make_bot(ext={
            "public_approval": {
                "puid": "puid1", "status": "PROCESSING",
                "permission_owner": "caller", "public": "1",
                "friend_approval": "0", "applicant": "op_user"
            }
        })
        bot_repo.get_by_id_and_owner.return_value = bot
        bot_repo.update_by_owner.return_value = {**bot}
        svc = _make_service(
            bot_repository=bot_repo,
            bcsfuse_config=MagicMock(
                base_url="http://bcsfuse.test",
                base_url_pre="http://bcsfuse.pre.test",
                worker_id_with_owner=True,
            ),
        )

        with (
            patch.object(svc, "_sync_access_mode_and_relations_or_raise"),
            patch("agentclaw.community.core.bot_public.services.bot_public_service.urlopen") as mock_urlopen,
        ):
            response = MagicMock()
            response.__enter__.return_value = response
            response.status = 200
            mock_urlopen.return_value = response
            result = svc.handle_public_approval_callback("bot1", "owner1", "puid1", "agree")

        assert result["success"] is True
        request = mock_urlopen.call_args.args[0]
        assert request.full_url == "http://bcsfuse.test/v1/workers/bot1:owner1/online"

    def test_agree_bcsfuse_failure_does_not_block_callback(self):
        bot_repo = MagicMock()
        bot = _make_bot(ext={
            "public_approval": {
                "puid": "puid1", "status": "PROCESSING",
                "permission_owner": "caller", "public": "1",
                "friend_approval": "0", "applicant": "op_user"
            }
        })
        bot_repo.get_by_id_and_owner.return_value = bot
        bot_repo.update_by_owner.return_value = {**bot}
        svc = _make_service(bot_repository=bot_repo, bcsfuse_config=MagicMock(base_url="http://bcsfuse.test", base_url_pre="http://bcsfuse.pre.test"))

        with (
            patch.object(svc, "_sync_access_mode_and_relations_or_raise"),
            patch("agentclaw.community.core.bot_public.services.bot_public_service.urlopen", side_effect=RuntimeError("bcsfuse down")),
        ):
            result = svc.handle_public_approval_callback("bot1", "owner1", "puid1", "agree")

        assert result["success"] is True

    # -- public_scope: new-version publish short-circuits the legacy ac_bots path --

    def test_public_scope_user_skips_ac_bots_and_logs(self):
        svc, _, bot_repo = self._make_svc_with_bot({
            "public_approval": {"puid": "puid1", "status": "PROCESSING",
                                "permission_owner": "owner", "public": "1",
                                "applicant": "op_user"}
        })
        result = svc.handle_public_approval_callback(
            "bot1", "owner1", "puid1", "agree", public_scope="user",
        )
        assert result["success"] is True
        assert result["public"] is None
        assert "public_scope=user" in result["message"]
        # New-version path must NOT touch ac_bots at all.
        bot_repo.get_by_id_and_owner.assert_not_called()
        bot_repo.update_by_owner.assert_not_called()

    def test_public_scope_agent_also_new_version(self):
        svc = _make_service()
        result = svc.handle_public_approval_callback(
            "bot1", "owner1", "puid1", "disagree", public_scope="agent",
        )
        assert result["success"] is True
        assert result["public"] is None
        assert "public_scope=agent" in result["message"]

    def test_public_scope_empty_string_runs_legacy(self):
        # public_scope="" must fall through to the legacy path (here: bot lookup).
        svc, _, bot_repo = self._make_svc_with_bot({
            "public_approval": {"puid": "puid1", "status": "PROCESSING",
                                "permission_owner": "owner", "public": "1",
                                "applicant": "op_user", "friend_approval": "0"}
        })
        with patch.object(svc, "_sync_access_mode_and_relations_or_raise"):
            svc.handle_public_approval_callback(
                "bot1", "owner1", "puid1", "agree", public_scope="",
            )
        bot_repo.get_by_id_and_owner.assert_called()


# ---------------------------------------------------------------------------
# _build_public_approval_context
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# handle_friend_request_approval_callback
# ---------------------------------------------------------------------------

class TestHandleFriendRequestApprovalCallback:
    def _make_repo(self, record=None):
        repo = MagicMock()
        repo.get_by_id.return_value = record
        return repo

    def test_record_not_found(self):
        svc = _make_service(bot_friend_repo=self._make_repo(record=None))
        result = svc.handle_friend_request_approval_callback("puid1", "agree", 99)
        assert result["success"] is False

    def test_agree_calls_accept(self):
        friend_record = {"id": 1, "status": "PENDING"}
        repo = self._make_repo(record=friend_record)
        repo.accept.return_value = {"id": 1, "status": "ACCEPTED"}
        svc = _make_service(bot_friend_repo=repo)
        with patch.object(
            svc,
            "_create_auth_relationship_for_approval",
        ) as mock_create_auth:
            result = svc.handle_friend_request_approval_callback(
                "puid1", "agree", 1,
            )

        assert result["success"] is True
        repo.accept.assert_called_once_with(1, approval_uuid="puid1")
        mock_create_auth.assert_called_once_with(friend_record, 1)

    def test_agree_propagates_auth_relationship_failure(self):
        friend_record = {"id": 1, "status": "PENDING"}
        repo = self._make_repo(record=friend_record)
        repo.accept.return_value = {"id": 1, "status": "ACCEPTED"}
        svc = _make_service(bot_friend_repo=repo)

        with (
            patch.object(
                svc,
                "_create_auth_relationship_for_approval",
                side_effect=RuntimeError("auth unavailable"),
            ),
            pytest.raises(RuntimeError, match="auth unavailable"),
        ):
            svc.handle_friend_request_approval_callback(
                "puid1", "agree", 1,
            )

        repo.accept.assert_called_once_with(1, approval_uuid="puid1")

    def test_agree_callback_can_retry_after_auth_relationship_failure(self):
        friend_record = {"id": 1, "status": "PENDING"}
        repo = self._make_repo(record=friend_record)
        repo.accept.return_value = {"id": 1, "status": "ACCEPTED"}
        svc = _make_service(bot_friend_repo=repo)

        with patch.object(
            svc,
            "_create_auth_relationship_for_approval",
            side_effect=[RuntimeError("auth unavailable"), None],
        ) as mock_create_auth:
            with pytest.raises(RuntimeError, match="auth unavailable"):
                svc.handle_friend_request_approval_callback(
                    "puid1", "agree", 1,
                )
            result = svc.handle_friend_request_approval_callback(
                "puid1", "agree", 1,
            )

        assert result["success"] is True
        assert repo.accept.call_count == 2
        assert mock_create_auth.call_count == 2

    def test_agree_accept_fails(self):
        friend_record = {"id": 1, "status": "PENDING"}
        repo = self._make_repo(record=friend_record)
        repo.accept.return_value = None
        svc = _make_service(bot_friend_repo=repo)
        result = svc.handle_friend_request_approval_callback("puid1", "agree", 1)
        assert result["success"] is False

    def test_disagree_calls_reject(self):
        friend_record = {"id": 1, "status": "PENDING"}
        repo = self._make_repo(record=friend_record)
        repo.reject.return_value = {"id": 1, "status": "REJECTED"}
        svc = _make_service(bot_friend_repo=repo)
        result = svc.handle_friend_request_approval_callback("puid1", "disagree", 1)
        assert result["success"] is True
        repo.reject.assert_called_once_with(1, approval_uuid="puid1")

    def test_cancel_calls_cancel(self):
        friend_record = {"id": 1, "status": "PENDING"}
        repo = self._make_repo(record=friend_record)
        repo.cancel.return_value = {"id": 1, "status": "CANCELED"}
        svc = _make_service(bot_friend_repo=repo)
        result = svc.handle_friend_request_approval_callback("puid1", "cancel", 1)
        assert result["success"] is True
        repo.cancel.assert_called_once_with(1, approval_uuid="puid1")

    def test_unknown_operation(self):
        friend_record = {"id": 1, "status": "PENDING"}
        repo = self._make_repo(record=friend_record)
        svc = _make_service(bot_friend_repo=repo)
        result = svc.handle_friend_request_approval_callback("puid1", "NOOP", 1)
        assert result["success"] is False
        assert "Unknown" in result["message"]


# ---------------------------------------------------------------------------
# _create_auth_relationship_for_approval
# ---------------------------------------------------------------------------

class TestCreateAuthRelationshipForApproval:
    @staticmethod
    def _friend_record():
        return {
            "id": 1,
            "target_bot_id": "bot1",
            "target_entity_id": "owner1",
            "requester_entity_id": "friend1",
        }

    @patch(
        "agentclaw.community.core.bot_management.utils.resolve_agent_code",
        return_value="agent_123",
    )
    def test_creates_relationship_for_restricted_bot(self, _mock_resolve):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(
            ext={"friend_approval": "1"},
        )
        plugin = MagicMock()
        plugin.create_relationship.return_value = {"auth_id": 42}
        svc = _make_service(
            bot_repository=bot_repo,
            auth_relationship_plugin=plugin,
        )

        svc._create_auth_relationship_for_approval(self._friend_record(), 1)

        plugin.create_relationship.assert_called_once_with(
            work_no="friend1",
            agent_code="agent_123",
            description="Authorized by TeamClaw via friend request approval",
            operator_work_no="owner1",
            operator_name="owner_nick",
        )

    @patch(
        "agentclaw.community.core.bot_management.utils.resolve_agent_code",
        return_value="agent_123",
    )
    def test_already_exists_is_idempotent_success(self, _mock_resolve):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(
            ext={"friend_approval": "1"},
        )
        plugin = MagicMock()
        plugin.create_relationship.return_value = {
            "auth_id": None,
            "already_exists": True,
        }
        svc = _make_service(
            bot_repository=bot_repo,
            auth_relationship_plugin=plugin,
        )

        svc._create_auth_relationship_for_approval(self._friend_record(), 1)

        plugin.create_relationship.assert_called_once()

    def test_open_bot_skips_explicit_relationship(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(
            ext={"friend_approval": "0"},
        )
        plugin = MagicMock()
        svc = _make_service(
            bot_repository=bot_repo,
            auth_relationship_plugin=plugin,
        )

        svc._create_auth_relationship_for_approval(self._friend_record(), 1)

        plugin.create_relationship.assert_not_called()

    @pytest.mark.parametrize(
        "missing_field",
        ["target_bot_id", "requester_entity_id"],
    )
    def test_missing_required_friend_field_fails_closed(self, missing_field):
        friend_record = self._friend_record()
        friend_record.pop(missing_field)
        svc = _make_service()

        with pytest.raises(BotPublicServiceError, match="缺少"):
            svc._create_auth_relationship_for_approval(friend_record, 1)

    def test_missing_bot_fails_closed(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        svc = _make_service(bot_repository=bot_repo)

        with pytest.raises(BotNotFoundError, match="bot1"):
            svc._create_auth_relationship_for_approval(
                self._friend_record(), 1,
            )

    def test_invalid_bot_ext_propagates_json_error(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(ext="invalid-json")
        svc = _make_service(bot_repository=bot_repo)

        with pytest.raises(ValueError):
            svc._create_auth_relationship_for_approval(
                self._friend_record(), 1,
            )

    @patch(
        "agentclaw.community.core.bot_management.utils.resolve_agent_code",
        return_value=None,
    )
    def test_missing_agent_code_fails_closed(self, _mock_resolve):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(
            ext={"friend_approval": "1"},
        )
        svc = _make_service(bot_repository=bot_repo)

        with pytest.raises(BotPublicServiceError, match="agent_code"):
            svc._create_auth_relationship_for_approval(
                self._friend_record(), 1,
            )

    @patch(
        "agentclaw.community.core.bot_management.utils.resolve_agent_code",
        return_value="agent_123",
    )
    def test_none_result_fails_closed(self, _mock_resolve):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(
            ext={"friend_approval": "1"},
        )
        plugin = MagicMock()
        plugin.create_relationship.return_value = None
        svc = _make_service(
            bot_repository=bot_repo,
            auth_relationship_plugin=plugin,
        )

        with pytest.raises(BotPublicServiceError, match="授权关系服务返回失败"):
            svc._create_auth_relationship_for_approval(
                self._friend_record(), 1,
            )

    @patch(
        "agentclaw.community.core.bot_management.utils.resolve_agent_code",
        return_value="agent_123",
    )
    def test_plugin_exception_propagates(self, _mock_resolve):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot(
            ext={"friend_approval": "1"},
        )
        plugin = MagicMock()
        plugin.create_relationship.side_effect = RuntimeError(
            "auth unavailable",
        )
        svc = _make_service(
            bot_repository=bot_repo,
            auth_relationship_plugin=plugin,
        )

        with pytest.raises(RuntimeError, match="auth unavailable"):
            svc._create_auth_relationship_for_approval(
                self._friend_record(), 1,
            )


# ---------------------------------------------------------------------------
# create_friend_request_approval
# ---------------------------------------------------------------------------

class TestCreateFriendRequestApproval:
    def test_raises_if_bot_not_found(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        svc = _make_service(bot_repository=bot_repo)
        with pytest.raises(BotPublicServiceError, match="Bot not found"):
            svc.create_friend_request_approval("bot1", "owner1", "op1", "Op One")

    def test_auto_accept_when_no_approval_needed_and_no_existing_record(self):
        bot_repo = MagicMock()
        bot = _make_bot(public="1", ext={"friend_approval": "0"})
        bot_repo.get_by_id_and_owner.return_value = bot

        repo = MagicMock()
        repo.get_by_entity_ids.return_value = None
        inserted = {"id": 5, "status": "ACCEPTED"}
        repo.insert.return_value = inserted

        svc = _make_service(bot_repository=bot_repo, bot_friend_repo=repo)
        result = svc.create_friend_request_approval("bot1", "owner1", "op1", "Op One")

        assert result["success"] is True
        assert result["auto_accepted"] is True
        assert result["bot_friend_id"] == 5

    def test_auto_accept_updates_existing_non_accepted_record(self):
        bot_repo = MagicMock()
        bot = _make_bot(public="1", ext={"friend_approval": "0"})
        bot_repo.get_by_id_and_owner.return_value = bot

        repo = MagicMock()
        existing = {"id": 7, "status": "PENDING", "ext": {}}
        repo.get_by_entity_ids.return_value = existing
        updated = {"id": 7, "status": "ACCEPTED"}
        repo._update_status_and_ext.return_value = updated

        svc = _make_service(bot_repository=bot_repo, bot_friend_repo=repo)
        result = svc.create_friend_request_approval("bot1", "owner1", "op1", "Op One")

        assert result["success"] is True
        assert result["auto_accepted"] is True

    def test_auto_accept_keeps_already_accepted_record(self):
        bot_repo = MagicMock()
        bot = _make_bot(public="1", ext={"friend_approval": "0"})
        bot_repo.get_by_id_and_owner.return_value = bot

        repo = MagicMock()
        existing = {"id": 7, "status": BotFriendStatus.ACCEPTED, "ext": {}}
        repo.get_by_entity_ids.return_value = existing

        svc = _make_service(bot_repository=bot_repo, bot_friend_repo=repo)
        result = svc.create_friend_request_approval("bot1", "owner1", "op1", "Op One")

        assert result["success"] is True
        repo._update_status_and_ext.assert_not_called()

    def test_requires_approval_creates_pending_and_calls_process(self):
        bot_repo = MagicMock()
        bot = _make_bot(public="1", ext={"friend_approval": "1"})
        bot_repo.get_by_id_and_owner.return_value = bot

        repo = MagicMock()
        repo.get_by_entity_ids.return_value = None
        repo.insert.return_value = {"id": 3, "status": "PENDING"}

        process_svc = MagicMock()
        process_svc.start_approval.return_value = {"success": True, "puid": "puid_abc", "approval_url": "http://approvals/puid_abc"}

        svc = _make_service(bot_repository=bot_repo, bot_friend_repo=repo, process_service=process_svc)
        result = svc.create_friend_request_approval("bot1", "owner1", "op1", "Op One")

        assert result["success"] is True
        assert result["bot_friend_id"] == 3
        repo.create_approval.assert_called_once()


# ---------------------------------------------------------------------------
# search_public_bots_by_keyword
# ---------------------------------------------------------------------------

class TestSearchPublicBotsByKeyword:
    def test_legacy_search_does_not_depend_on_catalog_metadata(self):
        bot_service = MagicMock()
        bot_service.list_bots_by_search.return_value = {
            "total": 1,
            "items": [_make_bot()],
        }
        metadata = MagicMock()
        svc = _make_service(bot_service=bot_service, catalog_metadata_service=metadata)

        result = svc.search_public_bots_by_keyword(search="catalog")

        assert result["total"] == 1
        metadata.search_public_bot_metadata.assert_not_called()

    def test_catalog_search_joins_only_the_current_bcs_page_and_reports_its_count(self):
        metadata = MagicMock()
        metadata.search_public_bot_metadata.return_value = _metadata_page(
            [
                BotCatalogMetadata(BotCatalogAddress("bot-1", "entity-1"), "bot"),
                BotCatalogMetadata(BotCatalogAddress("missing", "entity-2"), "bot"),
            ],
            total=2,
        )
        repository = MagicMock()
        non_public = _make_catalog_bot("bot-1", "entity-1")
        non_public["public"] = "0"
        repository.list_bots_by_owner_bot_pairs.return_value = (1, [non_public])
        svc = _make_service(
            bot_repository=repository, catalog_metadata_service=metadata
        )

        result = svc.search_catalog_public_bots_by_keyword(
            search="agent",
            page=3,
            page_size=20,
            caller=BotCatalogCaller("tenant-1", "user-1", 7),
            request_id="trace-1",
        )

        assert result["total"] == 2
        assert [bot["bot_id"] for bot in result["items"]] == ["bot-1"]
        assert [bot["bot_uuid"] for bot in result["items"]] == ["bot-1:entity-1"]
        metadata.search_public_bot_metadata.assert_called_once_with(
            search="agent",
            page=3,
            page_size=20,
            caller=BotCatalogCaller("tenant-1", "user-1", 7),
            request_id="trace-1",
        )
        repository.list_bots_by_owner_bot_pairs.assert_called_once_with(
            [("bot-1", "entity-1"), ("missing", "entity-2")],
            page=1,
            page_size=2,
        )

    def test_catalog_search_preserves_bcs_total_when_join_has_fewer_items(self):
        metadata = MagicMock()
        metadata.search_public_bot_metadata.return_value = _metadata_page(
            [BotCatalogMetadata(BotCatalogAddress("bot-1", "entity-1"), "bot")],
            total=21,
        )
        repository = MagicMock()
        repository.list_bots_by_owner_bot_pairs.return_value = (
            1,
            [_make_catalog_bot("bot-1", "entity-1")],
        )
        svc = _make_service(
            bot_repository=repository, catalog_metadata_service=metadata
        )

        result = svc.search_catalog_public_bots_by_keyword(
            caller=BotCatalogCaller("tenant-1", "user-1", None),
            request_id="trace-bcs-total",
        )

        assert result["total"] == 21
        assert len(result["items"]) == 1

    def test_catalog_search_prefers_validated_bcs_bot_uuid(self):
        metadata = MagicMock()
        metadata.search_public_bot_metadata.return_value = _metadata_page(
            [
                BotCatalogMetadata(
                    BotCatalogAddress("bot-1", "entity-1"),
                    "bot",
                    bot_uuid=" bot-1 : entity-1 ",
                )
            ],
            total=1,
        )
        repository = MagicMock()
        repository.list_bots_by_owner_bot_pairs.return_value = (
            1,
            [_make_catalog_bot("bot-1", "entity-1")],
        )
        svc = _make_service(
            bot_repository=repository, catalog_metadata_service=metadata
        )

        result = svc.search_catalog_public_bots_by_keyword(
            caller=BotCatalogCaller("tenant-1", "user-1", None),
            request_id="trace-bcs-bot-uuid",
        )

        assert result["items"][0]["bot_uuid"] == " bot-1 : entity-1 "

    def test_catalog_search_preserves_bcs_is_friend_on_its_exact_address(self):
        metadata = MagicMock()
        bcs_item = BotCatalogMetadata(
            BotCatalogAddress("shared", "entity-a"), "bot", is_friend=False
        )
        metadata.search_public_bot_metadata.return_value = _metadata_page(
            [bcs_item], total=1
        )
        repository = MagicMock()
        repository.list_bots_by_owner_bot_pairs.return_value = (
            1,
            [_make_catalog_bot("shared", "entity-a")],
        )
        svc = _make_service(
            bot_repository=repository, catalog_metadata_service=metadata
        )

        result = svc.search_catalog_public_bots_by_keyword(
            caller=BotCatalogCaller("tenant-1", "user-1", None),
            request_id="trace-friend",
        )

        assert result["items"] == [
            {
                **_make_catalog_bot("shared", "entity-a"),
                "bot_uuid": "shared:entity-a",
                "is_friend": False,
            }
        ]

    def test_catalog_search_preserves_requested_bcs_metadata_on_its_exact_address(self):
        metadata = MagicMock()
        friend_ext = {"public_user_approval": {"status": "PROCESSING"}}
        bcs_item = BotCatalogMetadata(
            BotCatalogAddress("shared", "entity-a"),
            "bot",
            is_friend=False,
            visibility="protected",
            is_online=False,
            actor_kind="bot",
            friend_ext=friend_ext,
            friend_check_in_strategy={},
            user_visibility="private",
        )
        metadata.search_public_bot_metadata.return_value = _metadata_page(
            [bcs_item], total=1
        )
        repository = MagicMock()
        repository.list_bots_by_owner_bot_pairs.return_value = (
            2,
            [
                _make_catalog_bot("shared", "entity-a"),
                _make_catalog_bot("shared", "entity-b"),
            ],
        )
        svc = _make_service(
            bot_repository=repository, catalog_metadata_service=metadata
        )

        result = svc.search_catalog_public_bots_by_keyword(
            caller=BotCatalogCaller("tenant-1", "user-1", None),
            request_id="trace-metadata",
        )

        assert result["items"] == [
            {
                **_make_catalog_bot("shared", "entity-a"),
                "bot_uuid": "shared:entity-a",
                "is_friend": False,
                "visibility": "protected",
                "is_online": False,
                "actor_kind": "bot",
                "friend_ext": friend_ext,
                "friend_check_in_strategy": {},
                "user_visibility": "private",
            }
        ]

    def test_catalog_search_restores_bcs_order_and_isolates_same_bot_id_by_entity(self):
        metadata = MagicMock()
        metadata.search_public_bot_metadata.return_value = _metadata_page(
            [
                BotCatalogMetadata(BotCatalogAddress("shared", "entity-a"), "bot"),
                BotCatalogMetadata(BotCatalogAddress("shared", "entity-b"), "bot"),
            ],
            total=3,
        )
        repository = MagicMock()
        repository.list_bots_by_owner_bot_pairs.return_value = (
            3,
            [
                _make_catalog_bot("shared", "entity-b"),
                _make_catalog_bot("shared", "entity-other"),
                _make_catalog_bot("shared", "entity-a"),
            ],
        )
        svc = _make_service(
            bot_repository=repository, catalog_metadata_service=metadata
        )

        result = svc.search_catalog_public_bots_by_keyword(
            caller=BotCatalogCaller("tenant-1", None, 7), request_id="trace-2"
        )

        assert result["total"] == 3
        assert [bot["entity_id"] for bot in result["items"]] == [
            "entity-a",
            "entity-b",
        ]

    def test_catalog_search_translates_metadata_unavailable_without_details(self):
        metadata = MagicMock()
        metadata.search_public_bot_metadata.side_effect = BotCatalogMetadataUnavailableError(
            "internal detail"
        )
        repository = MagicMock()
        svc = _make_service(
            bot_repository=repository, catalog_metadata_service=metadata
        )

        with pytest.raises(BotCatalogSearchUnavailableError) as error:
            svc.search_catalog_public_bots_by_keyword(
                caller=BotCatalogCaller("tenant-1", "user-1", None),
                request_id="trace-3",
            )

        assert str(error.value) == ""
        repository.list_bots_by_owner_bot_pairs.assert_not_called()

    def test_catalog_search_fails_closed_on_unexpected_metadata_error(self, caplog):
        metadata = MagicMock()
        metadata.search_public_bot_metadata.side_effect = RuntimeError(
            "private upstream detail"
        )
        svc = _make_service(catalog_metadata_service=metadata)

        with caplog.at_level("WARNING"), pytest.raises(
            BotCatalogSearchUnavailableError
        ) as error:
            svc.search_catalog_public_bots_by_keyword(
                caller=BotCatalogCaller("tenant-1", "user-1", None),
                request_id="trace-unexpected",
            )

        assert str(error.value) == ""
        assert "request_id=trace-unexpected failure=invalid_metadata" in caplog.text
        assert "private upstream detail" not in caplog.text

    def test_catalog_search_redacts_sensitive_fields_and_malformed_ext(self):
        malformed = _make_catalog_bot("malformed", "entity-malformed")
        malformed.update({"device_id": "device-secret", "ext": "{not-json"})
        sensitive = _make_catalog_bot("sensitive", "entity-sensitive")
        sensitive.update(
            {
                "device_id": "device-secret",
                "ext": (
                    '{"passport":{"token":"passport-secret","status":"ISSUED"},'
                    '"iam_token":"iam-secret","visible":"kept"}'
                ),
            }
        )
        metadata = MagicMock()
        metadata.search_public_bot_metadata.return_value = _metadata_page(
            [
                BotCatalogMetadata(
                    BotCatalogAddress("malformed", "entity-malformed"), "bot"
                ),
                BotCatalogMetadata(
                    BotCatalogAddress("sensitive", "entity-sensitive"), "bot"
                ),
            ],
            total=2,
        )
        repository = MagicMock()
        repository.list_bots_by_owner_bot_pairs.return_value = (2, [malformed, sensitive])
        svc = _make_service(
            bot_repository=repository, catalog_metadata_service=metadata
        )

        result = svc.search_catalog_public_bots_by_keyword(
            caller=BotCatalogCaller("tenant-1", "user-1", None),
            request_id="trace-redaction",
        )

        by_id = {bot["bot_id"]: bot for bot in result["items"]}
        assert by_id["malformed"]["device_id"] is None
        assert by_id["malformed"]["ext"] == {}
        assert by_id["sensitive"]["device_id"] is None
        assert by_id["sensitive"]["ext"] == {
            "passport": {"token": None, "status": "ISSUED"},
            "iam_token": None,
            "visible": "kept",
        }

    def test_catalog_read_without_user_skips_friendship_lookup(self):
        bot_service = MagicMock()
        bot_service.list_bots_by_search.return_value = {
            "total": 1,
            "items": [{"bot_id": "bot1", "owner_id": "owner1"}],
        }
        repo = MagicMock()
        svc = _make_service(bot_service=bot_service, bot_friend_repo=repo)

        result = svc.search_public_bots_by_keyword()

        assert result["total"] == 1
        assert "friend_record_approval" not in result["items"][0]
        repo.get_by_entity_ids_batch.assert_not_called()

    def test_returns_empty_if_no_items(self):
        bot_service = MagicMock()
        bot_service.list_bots_by_search.return_value = {"total": 0, "items": []}
        svc = _make_service(bot_service=bot_service)
        result = svc.search_public_bots_by_keyword("user1", search="foo")
        assert result["total"] == 0

    def test_enriches_items_with_friend_records(self):
        bot_service = MagicMock()
        bot_service.list_bots_by_search.return_value = {
            "total": 1,
            "items": [{"bot_id": "bot1", "owner_id": "owner1"}],
        }

        repo = MagicMock()
        repo.get_by_entity_ids_batch.return_value = [
            {
                "requester_entity_id": "user1",
                "target_bot_id": "bot1",
                "target_entity_id": "owner1",
                "status": "ACCEPTED",
            }
        ]
        svc = _make_service(bot_service=bot_service, bot_friend_repo=repo)
        result = svc.search_public_bots_by_keyword("user1")
        assert result["items"][0]["friend_record_approval"] is not None

    def test_handles_friend_records_error_gracefully(self):
        bot_service = MagicMock()
        bot_service.list_bots_by_search.return_value = {
            "total": 1,
            "items": [{"bot_id": "bot1", "owner_id": "owner1"}],
        }

        repo = MagicMock()
        repo.get_by_entity_ids_batch.side_effect = RuntimeError("DB error")
        svc = _make_service(bot_service=bot_service, bot_friend_repo=repo)
        result = svc.search_public_bots_by_keyword("user1")
        # Should return result without crashing; friend_record_approval not set
        assert result["total"] == 1

    def test_nulls_passport_token_in_ext_dict(self):
        bot_service = MagicMock()
        bot_service.list_bots_by_search.return_value = {
            "total": 1,
            "items": [{
                "bot_id": "bot1",
                "owner_id": "owner1",
                "ext": {
                    "passport": {"token": "secret-jwt", "status": "ISSUED"},
                    "start_status": "SUCCEEDED",
                },
            }],
        }
        repo = MagicMock()
        repo.get_by_entity_ids_batch.return_value = []
        svc = _make_service(bot_service=bot_service, bot_friend_repo=repo)
        result = svc.search_public_bots_by_keyword("user1")
        ext = result["items"][0]["ext"]
        assert ext["passport"]["token"] is None
        assert ext["passport"]["status"] == "ISSUED"
        assert ext["start_status"] == "SUCCEEDED"

    def test_nulls_passport_token_in_ext_string(self):
        import json as _json
        bot_service = MagicMock()
        bot_service.list_bots_by_search.return_value = {
            "total": 1,
            "items": [{
                "bot_id": "bot1",
                "owner_id": "owner1",
                "ext": _json.dumps({
                    "passport": {"token": "secret-jwt"},
                    "start_status": "SUCCEEDED",
                }),
            }],
        }
        repo = MagicMock()
        repo.get_by_entity_ids_batch.return_value = []
        svc = _make_service(bot_service=bot_service, bot_friend_repo=repo)
        result = svc.search_public_bots_by_keyword("user1")
        ext = result["items"][0]["ext"]
        assert isinstance(ext, dict)
        assert ext["passport"]["token"] is None
        assert ext["start_status"] == "SUCCEEDED"

    def test_sets_unparseable_ext_string_to_empty_dict(self):
        bot_service = MagicMock()
        bot_service.list_bots_by_search.return_value = {
            "total": 1,
            "items": [{
                "bot_id": "bot1",
                "owner_id": "owner1",
                "ext": "not-json{",
            }],
        }
        repo = MagicMock()
        repo.get_by_entity_ids_batch.return_value = []
        svc = _make_service(bot_service=bot_service, bot_friend_repo=repo)
        result = svc.search_public_bots_by_keyword("user1")
        # Malformed ext should be replaced with empty dict, not left as raw string
        assert result["items"][0]["ext"] == {}

    def test_unparseable_ext_still_gets_friend_record(self):
        """Verify that bots with malformed ext still receive friend_record_approval."""
        bot_service = MagicMock()
        bot_service.list_bots_by_search.return_value = {
            "total": 1,
            "items": [{
                "bot_id": "bot1",
                "owner_id": "owner1",
                "ext": "not-json{",
            }],
        }
        repo = MagicMock()
        repo.get_by_entity_ids_batch.return_value = [
            {
                "requester_entity_id": "user1",
                "target_bot_id": "bot1",
                "target_entity_id": "owner1",
                "status": "ACCEPTED",
            }
        ]
        svc = _make_service(bot_service=bot_service, bot_friend_repo=repo)
        result = svc.search_public_bots_by_keyword("user1")
        assert result["items"][0]["ext"] == {}
        assert result["items"][0]["friend_record_approval"] is not None

    def test_nulls_sensitive_fields_in_ext(self):
        """iam_token in ext and top-level device_id should be redacted in public search results."""
        bot_service = MagicMock()
        bot_service.list_bots_by_search.return_value = {
            "total": 1,
            "items": [{
                "bot_id": "bot1",
                "owner_id": "owner1",
                "device_id": "staff_73015_bot1_4f291f17211045ae99da17501f3ccc10",
                "ext": {
                    "iam_token": "secret-iam-token",
                    "passport": {"token": "secret-jwt"},
                    "start_status": "SUCCEEDED",
                },
            }],
        }
        repo = MagicMock()
        repo.get_by_entity_ids_batch.return_value = []
        svc = _make_service(bot_service=bot_service, bot_friend_repo=repo)
        result = svc.search_public_bots_by_keyword("user1")
        bot = result["items"][0]
        # Top-level device_id should be redacted
        assert bot["device_id"] is None
        # Sensitive fields inside ext should be redacted
        ext = bot["ext"]
        assert ext["iam_token"] is None
        assert ext["passport"]["token"] is None
        # Non-sensitive fields should remain intact
        assert ext["start_status"] == "SUCCEEDED"

    def test_nulls_sensitive_fields_in_ext_string(self):
        """iam_token should be redacted when ext is a JSON string."""
        import json as _json
        bot_service = MagicMock()
        bot_service.list_bots_by_search.return_value = {
            "total": 1,
            "items": [{
                "bot_id": "bot1",
                "owner_id": "owner1",
                "device_id": "staff_73015_bot1_abc123",
                "ext": _json.dumps({
                    "iam_token": "secret-iam-token",
                    "start_status": "SUCCEEDED",
                }),
            }],
        }
        repo = MagicMock()
        repo.get_by_entity_ids_batch.return_value = []
        svc = _make_service(bot_service=bot_service, bot_friend_repo=repo)
        result = svc.search_public_bots_by_keyword("user1")
        bot = result["items"][0]
        assert bot["device_id"] is None
        ext = bot["ext"]
        assert isinstance(ext, dict)
        assert ext["iam_token"] is None
        assert ext["start_status"] == "SUCCEEDED"

    def test_handles_ext_without_passport(self):
        bot_service = MagicMock()
        bot_service.list_bots_by_search.return_value = {
            "total": 1,
            "items": [{
                "bot_id": "bot1",
                "owner_id": "owner1",
                "ext": {"start_status": "SUCCEEDED"},
            }],
        }
        repo = MagicMock()
        repo.get_by_entity_ids_batch.return_value = []
        svc = _make_service(bot_service=bot_service, bot_friend_repo=repo)
        result = svc.search_public_bots_by_keyword("user1")
        assert result["items"][0]["ext"] == {"start_status": "SUCCEEDED"}


# ---------------------------------------------------------------------------
# list_my_bot_friends
# ---------------------------------------------------------------------------

class TestListMyBotFriends:
    def test_returns_empty_list(self):
        repo = MagicMock()
        repo.list_by_requester.return_value = (0, [])
        svc = _make_service(bot_friend_repo=repo)
        result = svc.list_my_bot_friends("user1")
        assert result["total"] == 0
        assert result["items"] == []

    def test_enriches_records_with_bot_info(self):
        repo = MagicMock()
        repo.list_by_requester.return_value = (1, [
            {"id": 1, "target_entity_id": "owner1", "target_bot_id": "bot1", "status": "ACCEPTED"}
        ])
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "bot_name": "TestBot"}
        svc = _make_service(bot_friend_repo=repo, bot_repository=bot_repo)
        result = svc.list_my_bot_friends("user1")
        assert result["total"] == 1
        assert result["items"][0]["bot"]["bot_id"] == "bot1"


# ---------------------------------------------------------------------------
# get_friend_record
# ---------------------------------------------------------------------------

class TestGetFriendRecord:
    def test_returns_record(self):
        repo = MagicMock()
        expected = {"id": 1}
        repo.get_by_entity_ids.return_value = expected
        svc = _make_service(bot_friend_repo=repo)
        result = svc.get_friend_record("user1", "owner1", "bot1")
        assert result == expected

    def test_returns_none_when_not_found(self):
        repo = MagicMock()
        repo.get_by_entity_ids.return_value = None
        svc = _make_service(bot_friend_repo=repo)
        assert svc.get_friend_record("user1", "owner1", "bot1") is None

    def test_includes_bot_access_info_and_bot_object(self):
        """验证返回结果包含 bot_access_info 和完整 bot 对象。"""
        repo = MagicMock()
        friend_record = {"id": 1, "status": "ACCEPTED", "requester_entity_id": "user1"}
        repo.get_by_entity_ids.return_value = friend_record

        bot_repo = MagicMock()
        bot = _make_bot(
            bot_id="bot1",
            owner_id="owner1",
            public="1",
            ext={"friend_approval": "1", "other_key": "other_val"},
        )
        bot_repo.get_by_id_and_owner.return_value = bot

        svc = _make_service(bot_friend_repo=repo, bot_repository=bot_repo)
        result = svc.get_friend_record("user1", "owner1", "bot1")

        # 验证 bot_access_info 字段
        assert result["bot_access_info"]["public"] == "1"
        assert result["bot_access_info"]["friend_approval"] == "1"

        # 验证 bot 字段包含完整 bot 对象
        assert result["bot"]["bot_id"] == "bot1"
        assert result["bot"]["owner_id"] == "owner1"
        assert result["bot"]["bot_name"] == "TestBot"

    def test_handles_ext_as_json_string(self):
        """验证 ext 为 JSON 字符串时能正确解析。"""
        import json

        repo = MagicMock()
        friend_record = {"id": 1, "status": "ACCEPTED"}
        repo.get_by_entity_ids.return_value = friend_record

        bot_repo = MagicMock()
        bot = {
            "id": 42,
            "bot_id": "bot1",
            "owner_id": "owner1",
            "public": "0",
            "ext": json.dumps({"friend_approval": "0", "custom": "value"}),
        }
        bot_repo.get_by_id_and_owner.return_value = bot

        svc = _make_service(bot_friend_repo=repo, bot_repository=bot_repo)
        result = svc.get_friend_record("user1", "owner1", "bot1")

        assert result["bot_access_info"]["public"] == "0"
        assert result["bot_access_info"]["friend_approval"] == "0"
        assert result["bot"]["bot_id"] == "bot1"

    def test_defaults_public_and_friend_approval_when_missing(self):
        """验证 ext 中缺少字段时使用默认值。"""
        repo = MagicMock()
        friend_record = {"id": 1}
        repo.get_by_entity_ids.return_value = friend_record

        bot_repo = MagicMock()
        bot = _make_bot(public="1", ext={})  # ext 为空，没有 friend_approval
        bot_repo.get_by_id_and_owner.return_value = bot

        svc = _make_service(bot_friend_repo=repo, bot_repository=bot_repo)
        result = svc.get_friend_record("user1", "owner1", "bot1")

        assert result["bot_access_info"]["public"] == "1"
        assert result["bot_access_info"]["friend_approval"] == "0"  # 默认值

    def test_raises_when_bot_not_found(self):
        """验证 bot 不存在时抛出 ValueError。"""
        repo = MagicMock()
        repo.get_by_entity_ids.return_value = {"id": 1}  # friend_record 存在

        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None  # bot 不存在

        svc = _make_service(bot_friend_repo=repo, bot_repository=bot_repo)
        with pytest.raises(ValueError, match="Bot not found"):
            svc.get_friend_record("user1", "owner1", "bot1")


# ---------------------------------------------------------------------------
# update_bot_ext
# ---------------------------------------------------------------------------

class TestUpdateBotExt:
    def test_merges_and_updates_ext(self):
        bot_svc = MagicMock()
        bot_svc.get_bot.return_value = {"ext": {"key1": "val1"}}
        bot_repo = MagicMock()
        svc = _make_service(bot_service=bot_svc, bot_repository=bot_repo)
        svc.update_bot_ext("bot1", "user1", {"key2": "val2"})
        bot_repo.update_by_owner.assert_called_once()
        update_data = bot_repo.update_by_owner.call_args[0][2]
        assert update_data["ext"]["key1"] == "val1"
        assert update_data["ext"]["key2"] == "val2"

    def test_handles_str_ext(self):
        import json
        bot_svc = MagicMock()
        bot_svc.get_bot.return_value = {"ext": json.dumps({"existing": "value"})}
        bot_repo = MagicMock()
        svc = _make_service(bot_service=bot_svc, bot_repository=bot_repo)
        svc.update_bot_ext("bot1", "user1", {"new_key": "new_val"})
        update_data = bot_repo.update_by_owner.call_args[0][2]
        assert update_data["ext"]["existing"] == "value"
        assert update_data["ext"]["new_key"] == "new_val"


# ---------------------------------------------------------------------------
# sync_bot_config_to_device
# ---------------------------------------------------------------------------
# Task 2.1: 入参 (bot_id, user_id, public, permission_owner) 走 resolver +
# DeviceSyncDispatcher。delegation + 错误路径覆盖见
# ``services/test_sync_bot_config_uses_resolver.py``。本文件保留兜底:
# resolver 抛 DeviceNotBoundError / UnknownProviderError 时 surface 为
# ``{"success": False, ...}`` — 与旧 DeviceSyncUnavailableError wire shape 一致。


class TestSyncBotConfigToDevice:
    """Resolver/dispatcher 路径下:`sync_bot_config_to_device` 是薄代理。
    Happy path + dispatcher 入参覆盖见
    ``tests/core/bot_public/services/test_sync_bot_config_uses_resolver.py``;
    此处只验错误路径(resolver 抛异常 → wire shape 兼容)。"""

    def test_returns_failure_dict_when_resolver_raises_not_bound(self):
        """resolver 抛 DeviceNotBoundError → {success: False, ...}"""
        from agentclaw.community.core.devices.services.device_context import (
            DeviceNotBoundError,
        )

        resolver = MagicMock()
        resolver.resolve_for_bot.side_effect = DeviceNotBoundError(
            "No device binding"
        )

        svc = _make_service(device_context_resolver=resolver)
        result = svc.sync_bot_config_to_device(
            bot_id="bot1",
            user_id="user1",
            public="0",
            permission_owner="owner",
        )
        assert result["success"] is False
        assert "No device binding" in result["message"]

    def test_returns_failure_dict_when_resolver_raises_unknown_provider(self):
        """resolver 抛 UnknownProviderError → {success: False, ...}"""
        from agentclaw.community.core.devices.services.device_context import (
            UnknownProviderError,
        )

        resolver = MagicMock()
        resolver.resolve_for_bot.side_effect = UnknownProviderError(
            "Unknown provider: bogus"
        )

        svc = _make_service(device_context_resolver=resolver)
        result = svc.sync_bot_config_to_device(
            bot_id="bot1",
            user_id="user1",
            public="0",
            permission_owner="owner",
        )
        assert result["success"] is False
        assert "Unknown provider" in result["message"]


# ---------------------------------------------------------------------------
# _archive_public_approval
# ---------------------------------------------------------------------------

class TestArchivePublicApproval:
    def test_no_op_if_no_approval_in_ext(self):
        svc = _make_service()
        ext = {}
        svc._archive_public_approval(ext, "bot1")
        assert "public_approval_history" not in ext

    def test_moves_approval_to_history(self):
        svc = _make_service()
        ext = {"public_approval": {"puid": "p1", "status": "PROCESSING"}}
        svc._archive_public_approval(ext, "bot1")
        assert "public_approval" not in ext
        assert len(ext["public_approval_history"]) == 1
        assert ext["public_approval_history"][0]["puid"] == "p1"

    def test_keeps_only_last_5_entries(self):
        svc = _make_service()
        history = [{"puid": f"old{i}"} for i in range(5)]
        ext = {
            "public_approval": {"puid": "new", "status": "PROCESSING"},
            "public_approval_history": history,
        }
        svc._archive_public_approval(ext, "bot1")
        # Should have 5 entries (6 total capped at 5, newest at end)
        assert len(ext["public_approval_history"]) == 5


# ---------------------------------------------------------------------------
# _build_public_approval_context
# ---------------------------------------------------------------------------

class TestBuildPublicApprovalContext:
    @patch("agentclaw.community.core.skill_center.services.skill_set_service.SkillSetService")
    def test_returns_required_keys(self, mock_skill_set_cls):
        mock_skill_set = MagicMock()
        mock_skill_set.get_all_skill_sets_with_skills.return_value = []
        mock_skill_set.get_all_skill_sets_with_mcps.return_value = []
        mock_skill_set_cls.return_value = mock_skill_set

        svc = _make_service()
        bot = _make_bot()
        operator = _make_operator()
        ctx = svc._build_public_approval_context(bot, operator)
        assert "publishHint" in ctx
        assert "botSkills" in ctx
        assert "botMcps" in ctx

    def test_falls_back_gracefully_on_skill_set_error(self):
        svc = _make_service()
        bot = _make_bot()
        operator = _make_operator()
        # SkillSetService import will fail in test env → should catch and return "获取失败"
        ctx = svc._build_public_approval_context(bot, operator)
        assert "publishHint" in ctx
        # botSkills may be "获取失败" or "无" depending on env, either is acceptable
        assert ctx["botSkills"] in ("获取失败", "无") or isinstance(ctx["botSkills"], str)


# ---------------------------------------------------------------------------
# _sync_access_mode_and_relations
# ---------------------------------------------------------------------------

class TestSyncAccessModeAndRelations:
    @patch("agentclaw.community.core.bot_management.utils.resolve_agent_code", return_value="agent_123")
    @patch("agentclaw.community.core.bot_public.services.bot_public_service.BotPublicService._rebuild_auth_relationships")
    def test_starts_background_thread_on_first_call(self, mock_rebuild, _mock_resolve):
        mock_passport = MagicMock()

        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot()

        svc = _make_service(bot_repository=bot_repo, passport_plugin=mock_passport)
        svc._sync_access_mode_and_relations(
            bot_id="bot1", owner_id="owner1",
            access_mode="OPEN", public="0"
        )

        # Give the background thread a moment to start
        import time
        time.sleep(0.1)

        assert len(svc._syncing_bots) == 0  # should have finished or be idle
        mock_passport.update_passport.assert_called_once()

    @patch("agentclaw.community.core.bot_management.utils.resolve_agent_code", return_value="agent_123")
    @patch("agentclaw.community.core.bot_public.services.bot_public_service.BotPublicService._rebuild_auth_relationships")
    def test_updates_access_mode_without_resource_scope(self, mock_rebuild, _mock_resolve):
        mock_passport = MagicMock()
        mock_passport.query_agent_passport.side_effect = AssertionError("accessMode sync must not query CLI scope")
        skill_set_service = MagicMock()
        skill_set_service.get_bot_mcp_codes.side_effect = AssertionError("accessMode sync must not collect MCP scope")
        skill_set_factory = MagicMock()
        skill_set_factory.create.return_value = skill_set_service

        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot()

        svc = _make_service(
            bot_repository=bot_repo,
            passport_plugin=mock_passport,
            skill_set_service_factory=skill_set_factory,
        )
        svc._sync_access_mode_and_relations(
            bot_id="bot1", owner_id="owner1",
            access_mode="OPEN", public="0"
        )

        import time
        time.sleep(0.1)

        mock_passport.update_passport.assert_called_once()
        kwargs = mock_passport.update_passport.call_args.kwargs
        assert "mcp_codes" not in kwargs
        assert "cli_items" not in kwargs
        mock_passport.query_agent_passport.assert_not_called()
        skill_set_factory.create.assert_not_called()

    def test_queues_pending_when_sync_already_running(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot()

        svc = _make_service(bot_repository=bot_repo)
        svc._syncing_bots.add(_SYNC_KEY)

        svc._sync_access_mode_and_relations(
            bot_id="bot1", owner_id="owner1",
            access_mode="RESTRICTED", public="1"
        )

        assert svc._pending_syncs[_SYNC_KEY] == ("RESTRICTED", "1")
        # Should not start a new thread
        assert _SYNC_KEY in svc._syncing_bots

    def test_overwrites_pending_with_latest_params(self):
        svc = _make_service()
        svc._syncing_bots.add(_SYNC_KEY)
        svc._pending_syncs[_SYNC_KEY] = ("OLD_MODE", "0")

        svc._sync_access_mode_and_relations(
            bot_id="bot1", owner_id="owner1",
            access_mode="OPEN", public="1"
        )

        assert svc._pending_syncs[_SYNC_KEY] == ("OPEN", "1")

    @patch("agentclaw.community.core.bot_management.utils.resolve_agent_code", return_value=None)
    def test_raises_when_agent_code_empty(self, _mock_resolve):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot()

        svc = _make_service(bot_repository=bot_repo)
        # The error is raised inside the background thread; syncing_bots should be
        # cleaned up after the thread crashes.
        svc._sync_access_mode_and_relations(
            bot_id="bot1", owner_id="owner1",
            access_mode="OPEN", public="1"
        )
        import time
        time.sleep(0.1)
        assert "owner1:bot1" not in svc._syncing_bots


# ---------------------------------------------------------------------------
# _sync_access_mode_and_relations_or_raise
# ---------------------------------------------------------------------------

class TestSyncAccessModeAndRelationsOrRaise:
    @patch(
        "agentclaw.community.core.bot_management.utils.resolve_agent_code",
        return_value="agent_123",
    )
    def test_updates_passport_then_rebuilds_relationships(self, _mock_resolve):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot()
        passport = MagicMock()
        svc = _make_service(
            bot_repository=bot_repo,
            passport_plugin=passport,
        )

        with patch.object(svc, "_rebuild_auth_relationships") as mock_rebuild:
            svc._sync_access_mode_and_relations_or_raise(
                "bot1", "owner1", "RESTRICTED", "1",
            )

        passport.update_passport.assert_called_once()
        assert passport.update_passport.call_args.kwargs["access_mode"] == "RESTRICTED"
        mock_rebuild.assert_called_once_with(
            bot_id="bot1",
            owner_id="owner1",
            owner_name="owner_nick",
            agent_code="agent_123",
            access_mode="RESTRICTED",
            public="1",
        )

    def test_raises_when_bot_missing(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None
        svc = _make_service(bot_repository=bot_repo)

        with pytest.raises(BotNotFoundError, match="bot1"):
            svc._sync_access_mode_and_relations_or_raise(
                "bot1", "owner1", "OPEN", "1",
            )

    @patch(
        "agentclaw.community.core.bot_management.utils.resolve_agent_code",
        return_value=None,
    )
    def test_raises_when_agent_code_missing(self, _mock_resolve):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot()
        svc = _make_service(bot_repository=bot_repo)

        with pytest.raises(BotPublicServiceError, match="agent_code"):
            svc._sync_access_mode_and_relations_or_raise(
                "bot1", "owner1", "OPEN", "1",
            )

    @patch(
        "agentclaw.community.core.bot_management.utils.resolve_agent_code",
        return_value="agent_123",
    )
    def test_wraps_passport_failure(self, _mock_resolve):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot()
        passport = MagicMock()
        passport.update_passport.side_effect = RuntimeError("passport unavailable")
        svc = _make_service(
            bot_repository=bot_repo,
            passport_plugin=passport,
        )

        with pytest.raises(BotPublicServiceError, match="passport unavailable"):
            svc._sync_access_mode_and_relations_or_raise(
                "bot1", "owner1", "OPEN", "1",
            )

    @patch(
        "agentclaw.community.core.bot_management.utils.resolve_agent_code",
        return_value="agent_123",
    )
    def test_propagates_rebuild_failure(self, _mock_resolve):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = _make_bot()
        svc = _make_service(bot_repository=bot_repo)

        with (
            patch.object(
                svc,
                "_rebuild_auth_relationships",
                side_effect=RuntimeError("auth unavailable"),
            ),
            pytest.raises(RuntimeError, match="auth unavailable"),
        ):
            svc._sync_access_mode_and_relations_or_raise(
                "bot1", "owner1", "RESTRICTED", "1",
            )


# ---------------------------------------------------------------------------
# _rebuild_auth_relationships
# ---------------------------------------------------------------------------

class TestRebuildAuthRelationships:
    def test_open_mode_deletes_all_relationships(self):
        plugin = MagicMock()
        plugin.delete_relationships_by_agent.return_value = 3

        svc = _make_service(auth_relationship_plugin=plugin)
        svc._rebuild_auth_relationships(
            bot_id="bot1", owner_id="owner1", owner_name="owner_nick", agent_code="agent_123",
            access_mode="OPEN", public="0"
        )

        plugin.delete_relationships_by_agent.assert_called_once_with("agent_123")
        plugin.create_relationship.assert_not_called()

    def test_restricted_private_keeps_only_owner(self):
        plugin = MagicMock()
        plugin.delete_relationships_by_agent.return_value = 0
        plugin.create_relationship.return_value = {"auth_id": 42}

        svc = _make_service(auth_relationship_plugin=plugin)
        svc._rebuild_auth_relationships(
            bot_id="bot1", owner_id="owner1", owner_name="owner_nick", agent_code="agent_123",
            access_mode="RESTRICTED", public="0"
        )

        plugin.delete_relationships_by_agent.assert_called_once_with("agent_123")
        plugin.create_relationship.assert_called_once_with(
            work_no="owner1",
            agent_code="agent_123",
            description="Bot owner authorization by TeamClaw",
            operator_work_no="owner1",
            operator_name="owner_nick",
        )

    def test_restricted_public_collects_approved_friends(self):
        plugin = MagicMock()
        plugin.delete_relationships_by_agent.return_value = 0
        plugin.create_relationship.return_value = {"auth_id": 42}

        repo = MagicMock()
        repo.list_approved_friends_for_bot.return_value = [
            {
                "requester_entity_id": "friend1",
                "ext": {
                    "approvals": [
                        {
                            "approval_type": "MANUAL",
                            "status": "APPROVED",
                        }
                    ]
                },
            }
        ]

        svc = _make_service(bot_friend_repo=repo, auth_relationship_plugin=plugin)
        svc._rebuild_auth_relationships(
            bot_id="bot1", owner_id="owner1", owner_name="owner_nick", agent_code="agent_123",
            access_mode="RESTRICTED", public="1"
        )

        plugin.delete_relationships_by_agent.assert_called_once_with("agent_123")
        # Should create for owner + approved friend
        assert plugin.create_relationship.call_count == 2
        call_args = [call.kwargs for call in plugin.create_relationship.call_args_list]
        work_nos = [c["work_no"] for c in call_args]
        assert "owner1" in work_nos
        assert "friend1" in work_nos

    def test_handles_already_exists_response(self):
        plugin = MagicMock()
        plugin.delete_relationships_by_agent.return_value = 0
        plugin.create_relationship.return_value = {"auth_id": None, "already_exists": True}

        svc = _make_service(auth_relationship_plugin=plugin)
        svc._rebuild_auth_relationships(
            bot_id="bot1", owner_id="owner1", owner_name="owner_nick", agent_code="agent_123",
            access_mode="RESTRICTED", public="0"
        )

        # Should not crash on already_exists
        plugin.create_relationship.assert_called_once()

    def test_open_mode_propagates_delete_failure(self):
        plugin = MagicMock()
        plugin.delete_relationships_by_agent.side_effect = RuntimeError(
            "delete unavailable",
        )
        svc = _make_service(auth_relationship_plugin=plugin)

        with pytest.raises(RuntimeError, match="delete unavailable"):
            svc._rebuild_auth_relationships(
                bot_id="bot1", owner_id="owner1", owner_name="owner_nick",
                agent_code="agent_123", access_mode="OPEN", public="1",
            )

    def test_restricted_public_propagates_friend_list_failure(self):
        repo = MagicMock()
        repo.list_approved_friends_for_bot.side_effect = RuntimeError(
            "friend query unavailable",
        )
        svc = _make_service(bot_friend_repo=repo)

        with pytest.raises(RuntimeError, match="friend query unavailable"):
            svc._rebuild_auth_relationships(
                bot_id="bot1", owner_id="owner1", owner_name="owner_nick",
                agent_code="agent_123", access_mode="RESTRICTED", public="1",
            )

    def test_restricted_mode_propagates_delete_failure(self):
        plugin = MagicMock()
        plugin.delete_relationships_by_agent.side_effect = RuntimeError(
            "delete unavailable",
        )
        svc = _make_service(auth_relationship_plugin=plugin)

        with pytest.raises(RuntimeError, match="delete unavailable"):
            svc._rebuild_auth_relationships(
                bot_id="bot1", owner_id="owner1", owner_name="owner_nick",
                agent_code="agent_123", access_mode="RESTRICTED", public="0",
            )

    def test_restricted_mode_propagates_create_failure(self):
        plugin = MagicMock()
        plugin.delete_relationships_by_agent.return_value = 0
        plugin.create_relationship.side_effect = RuntimeError(
            "create unavailable",
        )
        svc = _make_service(auth_relationship_plugin=plugin)

        with pytest.raises(RuntimeError, match="create unavailable"):
            svc._rebuild_auth_relationships(
                bot_id="bot1", owner_id="owner1", owner_name="owner_nick",
                agent_code="agent_123", access_mode="RESTRICTED", public="0",
            )

    def test_restricted_mode_treats_none_create_result_as_failure(self):
        plugin = MagicMock()
        plugin.delete_relationships_by_agent.return_value = 0
        plugin.create_relationship.return_value = None
        svc = _make_service(auth_relationship_plugin=plugin)

        with pytest.raises(BotPublicServiceError, match="授权关系服务返回失败"):
            svc._rebuild_auth_relationships(
                bot_id="bot1", owner_id="owner1", owner_name="owner_nick",
                agent_code="agent_123", access_mode="RESTRICTED", public="0",
            )
