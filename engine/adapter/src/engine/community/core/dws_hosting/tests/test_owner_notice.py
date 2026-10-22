from engine.community.core.dws_hosting.execution import invocation_for
from engine.community.core.dws_hosting.owner_notice import owner_notice
from .test_execution import build_execution
from .test_context import permitted_context


def test_notice_uses_verified_name_and_clear_reason(tmp_path):
    _, ledger, turn, _ = build_execution(tmp_path, text="对呢对呢")
    context = permitted_context(turn)
    context = context.model_copy(update={
        "sender": context.sender.model_copy(update={"names": ("郭欣",)})
    })
    ledger.reserve(turn, invocation_for(turn, context))
    record = ledger.require_manual("binding", turn.turn_id, "reply_review_rejected", ended=True)
    text = owner_notice(turn, record, "")
    assert "郭欣发来消息" in text
    assert "消息摘录：对呢对呢" in text
    assert "未通过内容校验" in text
    assert "没有自动发送" in text
    assert turn.turn_id not in text and turn.conversation_id not in text
