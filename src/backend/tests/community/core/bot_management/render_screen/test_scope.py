from agentclaw.community.core.bot_management.render_screen.scope import resolve_render_screen_scope


def test_resolve_render_screen_scope_owner_for_normal_bot():
    bot = {
        "bot_id": "bot_001",
        "owner_id": "user_001",
        "active_engine": "claude_code",
        "template_type": "normalCC",
    }

    assert resolve_render_screen_scope(bot) == "owner"


def test_resolve_render_screen_scope_bot_for_dynamic_template_member_management():
    bot = {
        "bot_id": "bot_001",
        "owner_id": "user_001",
        "active_engine": "claude_code",
        "template_type": "architect",
        "template_config": {
            "capabilities": {
                "member_management": True,
            },
        },
    }

    assert resolve_render_screen_scope(bot) == "bot"


def test_resolve_render_screen_scope_bot_for_nested_template_ext_member_management():
    bot = {
        "bot_id": "bot_001",
        "owner_id": "user_001",
        "active_engine": "claude_code",
        "template_type": "customAgent",
        "ext": {
            "template_config": {
                "bot_template_config": {
                    "advanced_config": {
                        "member_management": True,
                    },
                },
            },
        },
    }

    assert resolve_render_screen_scope(bot) == "bot"


def test_resolve_render_screen_scope_bot_for_openclaw_with_member_management_flag():
    # 只要模板明确开启了 member_management，各引擎 Bot 均支持成员协作
    bot = {
        "bot_id": "bot_001",
        "owner_id": "user_001",
        "active_engine": "openclaw",
        "template_type": "chat",
        "template_config": {
            "bot_template_config": {
                "advanced_config": {
                    "member_management": True,
                },
            },
        },
    }

    assert resolve_render_screen_scope(bot) == "bot"
