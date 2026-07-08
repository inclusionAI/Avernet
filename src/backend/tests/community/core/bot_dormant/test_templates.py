import pytest

from agentclaw.community.core.bot_dormant.templates import (
    render_warn,
    render_recycle,
    render_external_fallback,
)

# Neutral action-link pattern used across these tests. In production the pattern
# is deployment config (DormantNotifyConfig.action_link_pattern, from the
# `dormant.action_link_pattern` yaml key) and DormantBotService passes it into
# each render function.
_TEST_ACTION_LINK_PATTERN = "https://example.com/bots/{bot_id}"


@pytest.mark.unit
def test_render_warn_includes_dynamic_fields():
    # New signature: cooldown_days + M → remaining_days = M - cooldown_days.
    # cooldown_days=1, M=3 → remaining_days=2, so "2 天后" appears in output.
    out = render_warn(
        bot_name="客服小助手",
        days_inactive=8,
        cooldown_days=1,
        M=3,
        bot_id="bot_abc",
        action_link_pattern=_TEST_ACTION_LINK_PATTERN,
    )
    assert "客服小助手" in out
    assert "8 天" in out
    assert "2 天后" in out
    assert "https://example.com/bots/bot_abc" in out


@pytest.mark.unit
def test_render_recycle_includes_warning_about_next_day():
    out = render_recycle(
        bot_name="客服小助手",
        bot_id="bot_abc",
        action_link_pattern=_TEST_ACTION_LINK_PATTERN,
    )
    assert "客服小助手" in out
    assert "回收" in out
    assert "激活" in out
    assert "次日" in out or "次日可能再次" in out
    assert "https://example.com/bots/bot_abc" in out


@pytest.mark.unit
def test_render_external_fallback_includes_all_governance_fields():
    out = render_external_fallback(
        bot_name="低效 bot",
        governance_source="economy_governance",
        governance_dimension="token_waste",
        reason="近 30 天 token 消耗过高",
        bot_id="bot_xyz",
        action_link_pattern=_TEST_ACTION_LINK_PATTERN,
    )
    assert "低效 bot" in out
    assert "economy_governance" in out
    assert "token_waste" in out
    assert "近 30 天 token 消耗过高" in out
    assert "白名单" in out
    assert "https://example.com/bots/bot_xyz" in out


@pytest.mark.unit
def test_render_external_fallback_handles_missing_fields():
    out = render_external_fallback(
        bot_name="x",
        governance_source=None,
        governance_dimension=None,
        reason=None,
        bot_id="x",
        action_link_pattern=_TEST_ACTION_LINK_PATTERN,
    )
    assert "unknown" in out  # 缺失字段 fallback 'unknown'
    assert "未提供原因" in out


@pytest.mark.unit
def test_empty_pattern_renders_empty_link():
    """Community build: no action_link_pattern configured → empty pattern → empty
    link. Rendering must not crash; the notification copy still renders."""
    out = render_recycle(bot_name="MyBot", bot_id="b1")  # default action_link_pattern=""
    assert "MyBot" in out
    assert "[查看详情]()" in out  # empty action_link, no crash


@pytest.mark.unit
def test_render_warn_first_day_shows_full_M():
    content = render_warn(
        bot_name="MyBot",
        days_inactive=56,
        cooldown_days=0,
        M=3,
        bot_id="b1",
        action_link_pattern=_TEST_ACTION_LINK_PATTERN,
    )
    assert "将在 3 天后被自动回收" in content
    assert "已连续 56 天无活动" in content
    assert "example.com/bots/b1" in content


@pytest.mark.unit
def test_render_warn_mid_cooldown_counts_down():
    for cd, remaining in [(0, 3), (1, 2), (2, 1)]:
        c = render_warn(
            bot_name="x",
            days_inactive=10,
            cooldown_days=cd,
            M=3,
            bot_id="b1",
            action_link_pattern=_TEST_ACTION_LINK_PATTERN,
        )
        assert f"将在 {remaining} 天后被自动回收" in c


@pytest.mark.unit
def test_render_warn_clamps_negative_remaining_to_zero():
    """Defensive: scheduler race could yield cooldown > M; render must not show negative."""
    content = render_warn(
        bot_name="x",
        days_inactive=99,
        cooldown_days=10,
        M=3,
        bot_id="b1",
        action_link_pattern=_TEST_ACTION_LINK_PATTERN,
    )
    assert "将在 0 天后被自动回收" in content
