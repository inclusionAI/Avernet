"""消息模型：BCN 下发给玩家的节点任务如何解析。

主持人 undercover.py 用 state_machine 协作生成临时自定义协作，每个 bot_task
节点下发给 worker 时，BCS 把节点 prompt 组装成 chat.send 的 message.content：

    [State Machine Task]
    node_id: speak_3 / vote_3 /遺言 / ...
    display_name: 3号发言 / 3号投票 / ...
    [Input]            <- 公开发言历史（run.input），without any word
    [Upstream Outputs] <- 只含本轮排在这个玩家前面的原话（直系父节点产物）
    [Instruction]      <- 含玩家的词、座位、轮次、钝度、字数、票号写法

worker 只回一条最终话术，作为该节点的 artifact，主持人脚本逐字解析。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# 主持人 undercover.py 里定义的固定常量（见 SPEECH_MAX_CHARS / VOTE_MAX_CHARS）
DEFAULT_SPEECH_MAX = 25
DEFAULT_VOTE_MAX = 10


class NodePhase:
    SPEECH = "speech"
    VOTE = "vote"
    VOTE_START = "vote_start"
    WILL = "will"   # 遗言
    PREP = "prep"   # 预备
    UNKNOWN = "unknown"


@dataclass
class NodeTask:
    """解析后的协作节点任务。message.content 原文见 raw_content。"""

    node_id: str
    display_name: str
    input: str                      # [Input] 公开历史
    upstream_outputs: str           # [Upstream Outputs] 直系父节点产物
    instruction: str                # [Instruction] 含词、座位、轮次、限制
    raw_content: str

    # ---- 从 instruction 内提取的事实 ----
    seat: int | None = None
    word: str | None = None
    round_num: int | None = None
    bluntness: int | None = None
    max_chars: int = DEFAULT_SPEECH_MAX
    is_pk: bool = False
    allow_abstain: bool = True
    phase: str = NodePhase.UNKNOWN
    # 从 [Input] JSON 的 alive 列表 / instruction 的「可以投：…」提取
    alive_seats: tuple[int, ...] = ()
    allowed_targets: tuple[int, ...] = ()

    @property
    def is_vote(self) -> bool:
        return self.phase == NodePhase.VOTE or (
            self.phase != NodePhase.WILL and "投票" in self.instruction
        )

    @property
    def is_speech(self) -> bool:
        return self.phase == NodePhase.SPEECH


def _outer_task_instruction(instruction: str) -> str:
    """去掉 cmd_render_ping 嵌在遗言任务里的公开发言历史。"""
    before, marker, rest = instruction.partition("到目前为止全场说过的话：\n")
    if not marker:
        return instruction
    _history, separator, after = rest.partition("\n\n")
    return before + after if separator else before


def classify_phase(node_id: str, instruction: str, display_name: str) -> str:
    # 历史发言是引用材料，不是当前任务指令，不能参与 SPEECH/VOTE 关键词分类。
    outer_instruction = _outer_task_instruction(instruction)
    if "vote_start" in node_id or "投票预备" in display_name:
        return NodePhase.VOTE_START
    if is_vote_keyword(node_id, outer_instruction):
        return NodePhase.VOTE
    if is_speech_keyword(node_id, outer_instruction):
        return NodePhase.SPEECH
    if "遗言" in outer_instruction or "遗言" in display_name:
        return NodePhase.WILL
    if "预备" in outer_instruction or "预备" in display_name:
        return NodePhase.PREP
    return NodePhase.UNKNOWN


def is_vote_keyword(node_id: str, instruction: str) -> bool:
    if "vote" in node_id:
        return True
    return bool(re.search(r"投票|我投\d|票箱|开投", instruction))


def is_speech_keyword(node_id: str, instruction: str) -> bool:
    if "speak" in node_id:
        return True
    return bool(re.search(r"发言|描述你的词|说钝一点", instruction))


def _extract_seat_word_round(instruction: str) -> tuple[int | None, str | None, int | None, int | None, int, bool, bool]:
    """从 instruction 文本里抓座位、词、轮次、钝度、maxchars、是否 PK、能否弃权。"""
    is_pk = "PK" in instruction
    allow_abstain = "禁止弃权" not in instruction and "不能弃权" not in instruction

    seat_m = re.search(r"你是\s*(\d+)\s*号", instruction)
    seat = int(seat_m.group(1)) if seat_m else None

    word_m = re.search(r"词是【(.+?)】|【(.+?)】", instruction)
    word = (word_m.group(1) or word_m.group(2)) if word_m else None

    round_m = re.search(r"第\s*(\d+)\s*轮", instruction)
    round_num = int(round_m.group(1)) if round_m else None

    blunt_m = re.search(r"(?:钝度|钝到|至少)\s*(\d+)", instruction)
    bluntness = int(blunt_m.group(1)) if blunt_m else None
    if bluntness is None and round_num is not None:
        # 与 undercover.py 的 bluntness_n 一致的钝度阶梯：4 → 3 → 2
        bluntness = {1: 4, 2: 3}.get(round_num, 2)

    max_m = re.search(r"不超过\s*(\d+)\s*字", instruction)
    max_chars = int(max_m.group(1)) if max_m else DEFAULT_SPEECH_MAX

    return seat, word, round_num, bluntness, max_chars, is_pk, allow_abstain


def _input_json(input_text: str) -> dict | None:
    """[Input] 是 BCS `serde_json::to_string_pretty(run.input)`。"""
    if not input_text or not input_text.strip():
        return None
    try:
        data = json.loads(input_text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _extract_alive_seats(input_text: str) -> tuple[int, ...]:
    """run.input.alive 形如 ["1号 阿和", "5号 小满"]。"""
    data = _input_json(input_text)
    if data is None:
        return ()
    alive = data.get("alive")
    if not isinstance(alive, list):
        return ()
    seats: set[int] = set()
    for item in alive:
        if isinstance(item, int) and not isinstance(item, bool):
            seats.add(item)
        elif isinstance(item, str):
            m = re.match(r"\s*(\d+)\s*号", item)
            if m:
                seats.add(int(m.group(1)))
    return tuple(sorted(seats))


def _extract_allowed_targets(instruction: str) -> tuple[int, ...]:
    """instruction 里的 `可以投：1号、5号；`，PK 时只列合法候选。"""
    m = re.search(r"可以投[：:]\s*(.+?)(?:[；;。\n]|$)", instruction)
    if not m:
        return ()
    return tuple(sorted({int(n) for n in re.findall(r"(\d+)\s*号", m.group(1))}))


def _split_sections(text: str) -> dict[str, str]:
    """按独立行上的固定节段标题切分，忽略 JSON 字符串里的同名文本。"""
    header_re = re.compile(
        r"^[ \t]*(\[(?:Input|Upstream Outputs|Instruction)\])[ \t]*\r?$",
        re.MULTILINE,
    )
    headers = list(header_re.finditer(text))
    sections: dict[str, str] = {}
    for index, header in enumerate(headers):
        start = header.end()
        if text[start:start + 1] == "\n":
            start += 1
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        sections[header.group(1)] = text[start:end].strip()
    return sections


def parse_node_task(content: str) -> NodeTask:
    """把 BCS 下发的 chat.send message.content 解析成结构化 NodeTask。

    content 为 chat.send.params.message.content 里 text 块拼接后的文本。
    若解析不出 [State Machine Task] 可回退：把纯 instruction 当 [Instruction]。
    """
    if "State Machine Task" in content:
        head, _, rest = content.partition("State Machine Task")
        node_id_m = re.search(r"node_id:\s*(\S+)", rest)
        display_m = re.search(r"display_name:\s*(.+)", rest)
        node_id = node_id_m.group(1) if node_id_m else "unknown"
        display_name = display_m.group(1).strip() if display_m else ""
        sections = _split_sections(rest)

        task = NodeTask(
            node_id=node_id,
            display_name=display_name,
            input=sections.get("[Input]", ""),
            upstream_outputs=sections.get("[Upstream Outputs]", ""),
            instruction=sections.get("[Instruction]", ""),
            raw_content=content,
        )
    else:
        task = NodeTask(
            node_id="unknown", display_name="",
            input="", upstream_outputs="", instruction=content, raw_content=content,
        )

    task.seat, task.word, task.round_num, task.bluntness, task.max_chars, task.is_pk, task.allow_abstain = \
        _extract_seat_word_round(task.instruction)
    task.phase = classify_phase(task.node_id, task.instruction, task.display_name)
    task.alive_seats = _extract_alive_seats(task.input)
    task.allowed_targets = _extract_allowed_targets(task.instruction)
    return task