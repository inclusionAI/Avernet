from agentclaw.community.core.bot_management.render_screen.scope import resolve_render_screen_scope


def test_resolve_render_screen_scope_owner_for_normal_cc_bot():
    bot = {
        "bot_id": "bot_001",
        "owner_id": "user_001",
        "active_engine": "claude_code",
        "template_type": "normalCC",
    }

    assert resolve_render_screen_scope(bot) == "owner"


def test_resolve_render_screen_scope_owner_for_normal_template_bot():
    bot = {
        "bot_id": "bot_001",
        "owner_id": "user_001",
        "active_engine": "claude_code",
        "template_type": "normal",
    }

    assert resolve_render_screen_scope(bot) == "owner"


def test_resolve_render_screen_scope_owner_for_empty_template_bot():
    bot = {
        "bot_id": "bot_001",
        "owner_id": "user_001",
        "active_engine": "claude_code",
        "template_type": "",
    }

    assert resolve_render_screen_scope(bot) == "owner"


def test_resolve_render_screen_scope_owner_for_none_template_bot():
    bot = {
        "bot_id": "bot_001",
        "owner_id": "user_001",
        "active_engine": "claude_code",
        "template_type": None,
    }

    assert resolve_render_screen_scope(bot) == "owner"


def test_resolve_render_screen_scope_bot_for_legacy_application_coding():
    bot = {
        "bot_id": "bot_001",
        "owner_id": "user_001",
        "active_engine": "claude_code",
        "template_type": "applicationCoding",
    }

    assert resolve_render_screen_scope(bot) == "bot"


def test_resolve_render_screen_scope_bot_for_legacy_personal_coding():
    bot = {
        "bot_id": "bot_001",
        "owner_id": "user_001",
        "active_engine": "claude_code",
        "template_type": "personalCoding",
    }

    assert resolve_render_screen_scope(bot) == "bot"


def test_resolve_render_screen_scope_bot_for_dynamic_template():
    bot = {
        "bot_id": "bot_001",
        "owner_id": "user_001",
        "active_engine": "claude_code",
        "template_type": "architect",
    }

    assert resolve_render_screen_scope(bot) == "bot"


def test_resolve_render_screen_scope_owner_for_non_claude_code_bot():
    bot = {
        "bot_id": "bot_001",
        "owner_id": "user_001",
        "active_engine": "openclaw",
        "template_type": "chat",
    }

    assert resolve_render_screen_scope(bot) == "owner"


def test_resolve_render_screen_scope_owner_for_none_bot():
    assert resolve_render_screen_scope(None) == "owner"
