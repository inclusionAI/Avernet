"""Coding Bot runtime identity files resolve to their CLI homes."""

from agentclaw.community.core.services.identity_addressing import (
    build_arca_identity_mapper,
)


def test_aicoding_identity_mapper_pins_claude_and_codex_cli_homes() -> None:
    mapper = build_arca_identity_mapper("staff", "382641", "20260702_bot", "aicoding")

    assert mapper("identity/CLAUDE.md") == "/home/admin/.claude/CLAUDE.md"
    assert mapper("identity/AGENTS.md") == "/home/admin/.codex/AGENTS.md"


def test_claude_code_identity_mapper_keeps_the_legacy_address() -> None:
    mapper = build_arca_identity_mapper("staff", "u_1", "bot_cc", "claude_code")

    assert mapper("identity/CLAUDE.md") == (
        "/home/admin/.claude_code/workspace/.claude/CLAUDE.md"
    )


def test_aicoding_identity_mapper_still_requires_the_identity_namespace() -> None:
    mapper = build_arca_identity_mapper("staff", "u_1", "bot_1", "aicoding")

    try:
        mapper("workspace/PROMPT.md")
    except ValueError as exc:
        assert "identity/" in str(exc)
    else:
        raise AssertionError("expected a non-identity path to be refused")
