"""Human-readable owner notices; technical identifiers stay in the ledger."""

import unicodedata

from engine.community.kernel.dws_execution import ExecutionRecord
from engine.community.kernel.dws_hosting import Turn


def _preview(value: str, limit: int) -> str:
    text = " ".join(
        "".join(
            " " if unicodedata.category(char).startswith("C") else char
            for char in value
        ).split()
    )
    return text[:limit] + ("…" if len(text) > limit else "")


def owner_notice(turn: Turn, execution: ExecutionRecord, summary: str) -> str:
    context = execution.invocation.hosting_context
    names = context.sender.names if context and context.sender.identity_verified else ()
    sender = _preview(names[0], 40) if names else "一位钉钉联系人"
    preview = _preview(" / ".join(message.text for message in turn.messages), 200)
    reasons = {
        "dialogue_history_unavailable": "暂时无法获取完整的近期对话，为避免误回复，这条消息没有自动发送。",
        "reply_review_rejected": "自动回复未通过内容校验，为避免误回复，这条消息没有自动发送。",
        "reply_review_timeout": "回复内容校验超时，这条消息没有自动发送。",
        "reply_review_unavailable": "暂时无法完成回复内容校验，这条消息没有自动发送。",
        "ended_result_unavailable": "未能取得完整的回复结果，这条消息没有自动发送。",
        "engine_failed": "生成回复失败，这条消息没有自动发送。",
    }
    reason = reasons.get(execution.reason, "未能完成自动回复，这条消息没有自动发送。")
    if execution.state == "resolved":
        reason = "助手判断需要你本人处理，这条消息没有自动回复。"
        if summary.strip():
            reason += "\n助手说明：" + _preview(summary, 200)
    return (
        f"【钉钉托管提醒】\n{sender}发来消息，需要你处理：\n"
        f"消息摘录：{preview or '（非文本消息）'}\n{reason}\n"
        "请打开钉钉查看并回复。"
    )
