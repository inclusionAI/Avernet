"""把 BCS 下发的节点提示解析成游戏任务。

真实格式来自主持人脚本渲染后的 state machine 节点：
``[State Machine Task]`` 区块 + ``[Instruction]`` 正文；遗言来自
manager-worker 的 ``chat.send`` 文本。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

SPEECH_MAX_CHARS = 25
VOTE_MAX_CHARS = 10
EULOGY_MAX_CHARS = 35

_VOTE_START_TEXT = "投票开始，请大家投出一票，等待主持人公布结果。"


class TaskKind(str, Enum):
    SPEECH = "speech"
    VOTE = "vote"
    VOTE_START = "vote_start"
    EULOGY = "eulogy"
    ONBOARDING = "onboarding"
    UNKNOWN = "unknown"


class TaskParseError(ValueError):
    """无法识别任务形状。"""


@dataclass(frozen=True)
class GameTask:
    kind: TaskKind
    node_id: str
    instruction: str
    raw_text: str
    input_text: str = ""
    upstream: str = ""
    seat: int | None = None
    word: str | None = None
    round: int | None = None
    is_pk: bool = False
    max_chars: int = SPEECH_MAX_CHARS
    legal_targets: tuple[int, ...] = ()
    allow_abstain: bool = True
    candidate_seats: tuple[int, ...] = field(default_factory=tuple)


def _search_int(pattern: str, text: str) -> int | None:
    match = re.search(pattern, text)
    return int(match.group(1)) if match else None


def _seat_word(instruction: str) -> tuple[int | None, str | None]:
    patterns = (
        r"你是\s*(\d+)\s*号，你的词是【([^】]+)】",
        r"你是\s*(\d+)\s*号，词是【([^】]+)】",
    )
    for pattern in patterns:
        match = re.search(pattern, instruction)
        if match:
            return int(match.group(1)), match.group(2).strip()
    return None, None


def _legal_targets(instruction: str, self_seat: int | None) -> tuple[int, ...]:
    match = re.search(r"可以投：([^\n]+)", instruction)
    if match:
        return tuple(int(value) for value in re.findall(r"(\d+)\s*号", match.group(1)))
    roster = re.search(r"全场：([^\n]+)", instruction)
    if roster:
        seats = [int(value) for value in re.findall(r"(\d+)\s*号", roster.group(1))]
        return tuple(seat for seat in seats if seat != self_seat)
    return ()


def _between(text: str, start: str, end: str) -> str:
    begin = text.find(start)
    if begin < 0:
        return ""
    begin += len(start)
    finish = text.find(end, begin)
    if finish < 0:
        return ""
    return text[begin:finish].strip("\n")


def parse_task(text: str, *, known_word: str | None = None, known_seat: int | None = None) -> GameTask:
    """解析一条节点提示；识别不了时返回 ``UNKNOWN`` 而不是猜测成别的任务。"""

    raw = text.strip()
    if "请说一句遗言" in raw:
        max_chars = _search_int(r"不超过\s*(\d+)\s*个字", raw) or EULOGY_MAX_CHARS
        seats = tuple(sorted({int(value) for value in re.findall(r"(\d+)\s*号", raw)}))
        return GameTask(
            kind=TaskKind.EULOGY,
            node_id="eulogy",
            instruction=raw,
            raw_text=raw,
            input_text=raw,
            upstream=raw,
            seat=known_seat,
            word=known_word,
            max_chars=max_chars,
            candidate_seats=seats,
        )

    if "bcs-cli onboard" in raw or "bcs-coordination" in raw:
        return GameTask(kind=TaskKind.ONBOARDING, node_id="onboarding", instruction=raw, raw_text=raw)

    if "[State Machine Task]" not in raw:
        return GameTask(kind=TaskKind.UNKNOWN, node_id="", instruction=raw, raw_text=raw)

    node_id = ""
    node_match = re.search(r"^node_id:\s*(\S+)", raw, flags=re.MULTILINE)
    if node_match:
        node_id = node_match.group(1)
    _prefix, _separator, instruction = raw.rpartition("[Instruction]")
    instruction = instruction.strip()
    input_text = _between(raw, "[Input]", "\n[Upstream Outputs]")
    upstream = _between(raw, "[Upstream Outputs]", "\n[Instruction]")

    if "投票预备确认" in instruction or node_id == "vote_start":
        return GameTask(
            kind=TaskKind.VOTE_START,
            node_id=node_id or "vote_start",
            instruction=instruction,
            raw_text=raw,
            input_text=input_text,
            upstream=upstream,
            seat=known_seat,
            word=known_word,
            max_chars=VOTE_MAX_CHARS,
        )

    seat, word = _seat_word(instruction)
    seat = seat if seat is not None else known_seat
    word = word or known_word
    round_number = _search_int(r"第\s*(\d+)\s*轮", instruction)
    is_pk = "PK" in instruction or "本次 PK" in instruction

    if "我投N号" in instruction or node_id.startswith("vote_"):
        max_chars = _search_int(r"不超过\s*(\d+)\s*字", instruction) or VOTE_MAX_CHARS
        pk_vote = is_pk or "禁止弃权" in instruction
        return GameTask(
            kind=TaskKind.VOTE,
            node_id=node_id,
            instruction=instruction,
            raw_text=raw,
            input_text=input_text,
            upstream=upstream,
            seat=seat,
            word=word,
            round=round_number,
            is_pk=pk_vote,
            max_chars=max_chars,
            legal_targets=_legal_targets(instruction, seat),
            allow_abstain=not pk_vote,
        )

    if "描述你的词" in instruction or node_id.startswith("speak_"):
        max_chars = _search_int(r"不超过\s*(\d+)\s*字", instruction) or SPEECH_MAX_CHARS
        return GameTask(
            kind=TaskKind.SPEECH,
            node_id=node_id,
            instruction=instruction,
            raw_text=raw,
            input_text=input_text,
            upstream=upstream,
            seat=seat,
            word=word,
            round=round_number,
            is_pk=is_pk,
            max_chars=max_chars,
            allow_abstain=False,
        )

    return GameTask(
        kind=TaskKind.UNKNOWN,
        node_id=node_id,
        instruction=instruction,
        raw_text=raw,
        input_text=input_text,
        upstream=upstream,
        seat=seat,
        word=word,
        round=round_number,
        is_pk=is_pk,
    )



def vote_start_text() -> str:
    return _VOTE_START_TEXT
