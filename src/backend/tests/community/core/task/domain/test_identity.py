from agentclaw.community.core.task.domain.identity import compose_bot_identity


def test_compose_bot_identity_adds_owner_to_pure_bot_id():
    assert compose_bot_identity("default", "146836") == "default:146836"


def test_compose_bot_identity_rebuilds_stale_composite_owner():
    assert compose_bot_identity("default:old-owner", "146836") == "default:146836"


def test_compose_bot_identity_preserves_composite_without_explicit_owner():
    assert compose_bot_identity("default:146836") == "default:146836"
