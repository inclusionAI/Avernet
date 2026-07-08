"""Route-B acceptance: expert_chat session lifecycle on live singlebox."""
from __future__ import annotations

import httpx
import pytest

from tests.community._flows.expert_chat.api_lifecycle import EXPERT_CHAT_FLOWS
from tests.community.acceptance._fixtures.live_personal_bot import (
    create_live_personal_bot,
    fresh_id,
)
from tests.community.framework.flow import FlowContext
from tests.community.framework.flow_runner_live import run_flow_live


@pytest.mark.acceptance
def test_expert_chat_session_reaches_live_singlebox_engine(live_backend, acceptance_fs_root):
    user_id = fresh_id("e2e_expert_user")
    headers = {"x-user-id": user_id}

    with httpx.Client(base_url=live_backend, headers=headers, timeout=60.0) as client:
        bot = create_live_personal_bot(
            client,
            user_id=user_id,
            bot_name_prefix="Expert Acceptance",
            bot_desc="expert_chat live session acceptance bot",
        )

    ctx = FlowContext()
    ctx["bot_id"] = bot["bot_id"]
    ctx["owner_id"] = user_id
    try:
        result_ctx = run_flow_live(
            EXPERT_CHAT_FLOWS[0],
            base_url=live_backend,
            fs_root=acceptance_fs_root,
            default_headers=headers,
            initial_context=ctx,
        )
    except AssertionError as exc:
        message = str(exc)
        if "50201" in message or "Bot服务暂不可用" in message:
            pytest.xfail(
                "expert_chat route-B pending: live BaaS invoke-http currently "
                "resolves the local_proc device through an ARCA-only invoke path; "
                "see docs/singlebox-eval/findings/expert_chat-baas-invoke-http-local-proc.md"
            )
        raise

    assert result_ctx["expert_session_reused"] == result_ctx["expert_session"]
