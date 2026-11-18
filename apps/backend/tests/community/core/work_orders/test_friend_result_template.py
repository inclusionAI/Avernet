"""Friend-request notification copy (PRD v1.1 §3.2 / §3.1)."""

from agentclaw.community.core.work_orders.models import (
    WorkOrderEventType,
    friend_result_content,
    notification_title_for,
)


def test_human_approved_includes_approver_and_target_bot_name() -> None:
    text = friend_result_content(
        approved=True,
        applicant_is_bot=False,
        approver_name="张三",
        applicant_bot_name="",
        target_bot_name="代码专家",
    )

    assert text == "张三已通过你添加 Bot「代码专家」的好友申请。"


def test_human_approved_omits_unresolved_approver_without_raw_id() -> None:
    for approver in (None, "", "   "):
        text = friend_result_content(
            approved=True,
            applicant_is_bot=False,
            approver_name=approver,
            applicant_bot_name="",
            target_bot_name="代码专家",
        )

        assert text == "你添加 Bot「代码专家」的好友申请已通过。"
        assert "None" not in text


def test_human_rejected_appends_reason_line_only_when_remark_present() -> None:
    with_remark = friend_result_content(
        approved=False,
        applicant_is_bot=False,
        approver_name="张三",
        applicant_bot_name="",
        target_bot_name="代码专家",
        review_remark="目标 Bot 不接受外部好友",
    )
    without_remark = friend_result_content(
        approved=False,
        applicant_is_bot=False,
        approver_name="张三",
        applicant_bot_name="",
        target_bot_name="代码专家",
        review_remark="   ",
    )

    assert with_remark == (
        "张三已驳回你添加 Bot「代码专家」的好友申请。\n"
        "拒绝原因：目标 Bot 不接受外部好友"
    )
    assert without_remark == "张三已驳回你添加 Bot「代码专家」的好友申请。"


def test_bot_result_uses_both_bot_names() -> None:
    text = friend_result_content(
        approved=True,
        applicant_is_bot=True,
        approver_name="张三",
        applicant_bot_name="申请方 Bot",
        target_bot_name="目标 Bot",
    )

    assert text == (
        "张三已通过你的 Bot「申请方 Bot」添加 Bot「目标 Bot」为好友的申请。"
    )


def test_bot_result_degrades_when_approver_missing() -> None:
    text = friend_result_content(
        approved=False,
        applicant_is_bot=True,
        approver_name=None,
        applicant_bot_name="申请方 Bot",
        target_bot_name="目标 Bot",
        review_remark="重复申请",
    )

    assert text == (
        "你的 Bot「申请方 Bot」添加 Bot「目标 Bot」为好友的申请已驳回。\n"
        "拒绝原因：重复申请"
    )


def test_result_copy_never_uses_legacy_rejected_term() -> None:
    for approved in (True, False):
        for applicant_is_bot in (True, False):
            text = friend_result_content(
                approved=approved,
                applicant_is_bot=applicant_is_bot,
                approver_name="张三",
                applicant_bot_name="申请方 Bot",
                target_bot_name="目标 Bot",
                review_remark="备注",
            )
            assert "未通过" not in text


def test_friend_approval_cards_share_one_pending_title() -> None:
    human = notification_title_for(WorkOrderEventType.HUMAN2BOT_FRIEND_APPLIED.value)
    bot = notification_title_for(WorkOrderEventType.BOT2BOT_FRIEND_APPLIED.value)

    assert human == bot == "好友申请待审批"