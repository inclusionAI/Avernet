"""
对话格式化与多任务分割 — LLM-as-Judge 专用

提供消息格式化（含 untrusted metadata 清洗）和任务边界切分，
独立于 src/stage2_extract/conversation_parser.py，不依赖 config.py。
"""

from __future__ import annotations

import re
from typing import Any


# ═══════════════════════════════════════════════════════════
#  Untrusted metadata 清洗
# ═══════════════════════════════════════════════════════════

_UNTRUSTED_METADATA_BLOCK = re.compile(
    r"[^\n]*?\(untrusted metadata\):\s*```json\s*[^`]*?```\s*",
    re.DOTALL,
)
_BCS_GROUP_CONTEXT = re.compile(
    r"\[BCS Group Context\]\s*\n(?:[- ].+\n)*\s*",
)
_MESSAGE_CONTENT_MARKER = re.compile(
    r"\[消息内容\]\s*",
)


def _content_to_str(content: Any) -> str:
    """将 content 转换为字符串

    支持多种消息格式：
    - 字符串: 直接返回
    - OpenAI 格式列表: [{"type": "text", "text": "..."}]
    - 单个字典块: {"type": "text", "text": "..."} 或 {"text": "...", "data": "..."}
    - 钉钉图片格式列表: [{"text": "...", "data": "..."}]  (提取 text，忽略 data)
    - 其他: str() 转换

    注意：如果块中同时有 text 和 data 字段，只保留 text，跳过 data
    """
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        # 单个字典块
        # 优先使用 type="text" 的标准格式
        if content.get("type") == "text":
            return content.get("text", "")
        # 支持钉钉格式: 有 text 字段但没有 type 字段（忽略 data 字段）
        elif "text" in content and "type" not in content:
            return content.get("text", "")
        # 纯 data 块（没有 text）返回空
        return ""
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict):
                # 优先使用 type="text" 的标准格式
                if block.get("type") == "text":
                    text = block.get("text", "")
                    if text:
                        texts.append(text)
                # 支持钉钉格式: 有 text 字段但没有 type 字段（忽略 data 字段）
                elif "text" in block and "type" not in block:
                    text = block.get("text", "")
                    if text:
                        texts.append(text)
                # 纯 data 块（没有 text）跳过
            elif isinstance(block, str):
                texts.append(block)
        return "\n".join(texts)
    return str(content)


def _strip_untrusted_metadata(content: Any) -> str:
    """去除用户消息中的所有 untrusted metadata 前缀和 BCS 上下文信息

    支持字符串或 OpenAI 内容块列表格式
    """
    text = _content_to_str(content)
    text = _UNTRUSTED_METADATA_BLOCK.sub("", text)
    text = _BCS_GROUP_CONTEXT.sub("", text)
    text = _MESSAGE_CONTENT_MARKER.sub("", text)
    return text.strip()


def _extract_user_timestamp(content: Any) -> str:
    """从用户消息中提取时间戳前缀，如 '[Fri 2026-04-03 11:52 GMT+8]'

    支持字符串或 OpenAI 内容块列表格式
    """
    stripped = _strip_untrusted_metadata(content)
    match = re.match(r"(\[.+?\])\s*", stripped)
    return match.group(1) if match else ""


def _extract_user_text(content: Any) -> str:
    """去掉 untrusted metadata 前缀和时间戳前缀，返回用户消息正文

    支持字符串或 OpenAI 内容块列表格式
    """
    stripped = _strip_untrusted_metadata(content)
    match = re.match(r"\[.+?\]\s*", stripped)
    return stripped[match.end():] if match else stripped


# ═══════════════════════════════════════════════════════════
#  消息格式化
# ═══════════════════════════════════════════════════════════

def _truncate(text: str, max_len: int) -> str:
    """尾部截断，用于工具参数等短文本"""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def _truncate_middle(text: str, max_len: int) -> str:
    """中间截断，保留头尾，标注省略字数

    Args:
        text: 原始文本
        max_len: 最大长度限制

    Returns:
        截断后的文本，格式为 "头部...[省略N字]...尾部"
    """
    if len(text) <= max_len:
        return text

    # 预留省略标注的空间 "...[省略XXXX字]..." 约 15 字符
    marker_len = 15
    available = max_len - marker_len
    if available < 10:
        # 如果 max_len 太小，回退到尾部截断
        return text[:max_len - 3] + "..."

    head_len = available // 2
    tail_len = available - head_len

    head = text[:head_len]
    tail = text[-tail_len:]
    omitted = len(text) - head_len - tail_len

    return f"{head}...[省略{omitted}字]...{tail}"


def _format_tool_args(args: Any, max_len: int = 200) -> str:
    if isinstance(args, dict):
        truncated = {}
        for k, v in args.items():
            s = str(v)
            truncated[k] = s[:100] + "..." if len(s) > 100 else s
        text = str(truncated)
    else:
        text = str(args)
    return _truncate(text, max_len)


def _is_error_result(msg: dict) -> bool:
    """判断工具结果是否为错误，同时检查 isError 标志和 details.status"""
    is_error = msg.get("isError", False)
    if isinstance(is_error, str):
        is_error = is_error.lower() == "true"
    if not is_error:
        details = msg.get("details")
        if isinstance(details, dict) and details.get("status") == "error":
            is_error = True
    return bool(is_error)


def format_message(msg: dict, with_idx: bool = False, max_content_len: int = 10000) -> str:
    """将单条消息格式化为可读文本行

    - [#N User] <timestamp> <content>
    - [#N Assistant] <text>
    - [#N Assistant -> Tool: <name>] <args 摘要>
    - [#N Tool Result: <name>] <content 截断 300 字> [ERROR]
    - 跳过 thinking 块
    - 无 idx 字段时不加编号前缀
    - 直接跳过包含 base64 图片数据的块（data 字段存在）
    - 超长内容使用中间截断，保留头尾

    Args:
        msg: 消息字典
        with_idx: 是否添加消息序号
        max_content_len: 单条消息内容的最大长度限制
    """
    role = msg.get("role", "")
    content = msg.get("content", "")
    idx = msg.get("idx")
    prefix = f"[#{idx} " if (with_idx and idx is not None) else "["

    if role == "user":
        # 使用 _content_to_str 处理，自动跳过 base64 数据
        content_str = _content_to_str(content)
        # 截断超长内容（中间截断）
        content_str = _truncate_middle(content_str, max_content_len)
        ts = _extract_user_timestamp(content_str)
        text = _extract_user_text(content_str)
        # 再次截断（提取后可能仍然很长）
        text = _truncate_middle(text, max_content_len)
        return f"{prefix}User] {ts} {text}" if ts else f"{prefix}User] {text}"

    elif role == "assistant":
        if isinstance(content, str):
            truncated = _truncate_middle(content, max_content_len)
            return f"{prefix}Assistant] {truncated}"
        elif isinstance(content, list):
            lines = []
            for item in content:
                if isinstance(item, dict):
                    if "thinking" in item or item.get("thinkingSignature"):
                        continue
                    # 使用 _content_to_str 处理，自动跳过 data 字段
                    item_text = _content_to_str(item)
                    if item_text:
                        text = _truncate_middle(item_text, max_content_len)
                        lines.append(f"{prefix}Assistant] {text}")
                    if "name" in item:
                        args_str = _format_tool_args(item.get("arguments", {}))
                        lines.append(f"{prefix}Assistant -> Tool: {item['name']}] {args_str}")
            return "\n".join(lines)
        return f"{prefix}Assistant] {_truncate_middle(str(content), max_content_len)}"

    elif role == "toolResult":
        tool_name = msg.get("toolName", "unknown")
        is_error = _is_error_result(msg)
        # 使用 _content_to_str 处理，跳过 base64 数据
        content_str = _content_to_str(content)
        content_str = _truncate(content_str, 300)
        error_tag = " [ERROR]" if is_error else ""
        return f"{prefix}Tool Result: {tool_name}] {content_str}{error_tag}"

    return ""


def format_conversation(messages: list[dict], with_idx: bool = False) -> str:
    """将消息列表格式化为完整对话文本"""
    lines = []
    for msg in messages:
        formatted = format_message(msg, with_idx=with_idx)
        if formatted:
            lines.append(formatted)
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
#  多任务分割
# ═══════════════════════════════════════════════════════════

def split_messages_at_boundaries(messages: list[dict], task_boundaries: list[dict]) -> list[dict]:
    """按 LLM 返回的任务边界切分 messages 数组

    Args:
        messages: 原始消息列表
        task_boundaries: LLM 返回的任务边界列表，每个元素:
            {"task_index": 1, "start_at_user_message": 1, "end_at_user_message": 2,
             "task_description": "..."}
            start_at_user_message / end_at_user_message 为 1-based 用户消息序号

    Returns:
        按任务切分的列表，task_index 为 0-based
    """
    if not task_boundaries:
        return [{"task_index": 0, "task_description": "", "user_message_range": [1, 1], "message_range": [0, len(messages)], "messages": messages}]

    user_msg_indices = []
    for i, msg in enumerate(messages):
        if msg.get("role") == "user":
            user_msg_indices.append(i)

    if not user_msg_indices:
        return [{"task_index": 0, "task_description": "", "user_message_range": [0, 0], "message_range": [0, len(messages)], "messages": messages}]

    total_user_msgs = len(user_msg_indices)

    result = []
    for boundary_idx, boundary in enumerate(task_boundaries):
        start_user_1based = boundary.get("start_at_user_message", 1)
        end_user_1based = boundary.get("end_at_user_message", start_user_1based)
        task_desc = boundary.get("task_description", "")

        start_user_0based = start_user_1based - 1
        end_user_0based = end_user_1based - 1

        start_user_0based = max(0, min(start_user_0based, total_user_msgs - 1))
        end_user_0based = max(0, min(end_user_0based, total_user_msgs - 1))

        msg_start = user_msg_indices[start_user_0based]

        if boundary_idx + 1 < len(task_boundaries):
            next_start_user_1based = task_boundaries[boundary_idx + 1].get("start_at_user_message", total_user_msgs + 1)
            next_start_user_0based = next_start_user_1based - 1
            next_start_user_0based = min(next_start_user_0based, total_user_msgs - 1)
            msg_end = user_msg_indices[next_start_user_0based]
        else:
            msg_end = len(messages)

        task_messages = messages[msg_start:msg_end]

        result.append({
            "task_index": boundary_idx,
            "task_description": task_desc,
            "user_message_range": [start_user_1based, end_user_1based],
            "message_range": [msg_start, msg_end],
            "messages": task_messages,
        })

    return result