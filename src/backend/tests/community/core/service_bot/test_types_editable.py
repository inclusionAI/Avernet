from agentclaw.community.core.service_bot.types import is_editable_bot


def test_personal_always_editable():
    assert is_editable_bot("personal", "online")
    assert is_editable_bot("personal", "draft")


def test_service_draft_editable():
    assert is_editable_bot("service", "draft")


def test_service_online_locked():
    assert not is_editable_bot("service", "online")


def test_service_verify_locked():
    assert not is_editable_bot("service", "verify")
