"""Decode the referee's existing text task envelope without consulting other players."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .domain import PlayerTask


@dataclass(frozen=True)
class PrivateSeat:
    seat: int
    word: str


def parse_task(text: str, previous: PrivateSeat | None = None) -> PlayerTask:
    # build_chat_send_frame wraps V3 StateMachine deliveries with recipient context.
    # Only unwrap the anchored transport envelope; never search inside public history.
    if text.startswith("[BCS Group Context]\n"):
        _, sep, text = text.partition("\n\n[消息内容]\n")
        if not sep:
            raise ValueError("Missing BCS message content delimiter")
    # bcs-message-flow's build_task_frame adds this sender label to task text.
    # Strip only the anchored, single-line envelope, never markers inside quoted history.
    text = re.sub(r"^\[from:[^\]\r\n]*\][ \t]?", "", text, count=1)
    public_input, upstream, node_id = "", "", ""
    instruction = text
    if text.startswith("[State Machine Task]\n"):
        header, sep, rest = text.partition("\n\n[Input]\n")
        match = re.search(r"^node_id: (\S+)$", header, re.MULTILINE)
        if not sep or not match:
            raise ValueError("Invalid state-machine task header")
        node_id = match[1]
        # Input is JSON emitted by build_node_prompt, not a keyword-scanned prompt.
        value, end = json.JSONDecoder().raw_decode(rest)
        public_input = json.dumps(value, ensure_ascii=False)
        tail = rest[end:].lstrip("\n")
        if not tail.startswith("[Upstream Outputs]"):
            raise ValueError("Unsupported task envelope (expected upstream outputs)")
        body = tail[len("[Upstream Outputs]") :]
        # The referee instruction is last. A quoted upstream heading cannot replace it.
        upstream, sep, instruction = body.rpartition("\n\n[Instruction]\n")
        if not sep:
            raise ValueError("Missing node instruction")
        upstream = upstream.strip()
    if node_id == "vote_start":
        return PlayerTask("ready", node_id, instruction, public_input, "", 0, "", 100)
    if re.fullmatch(r"speak_\d+", node_id):
        kind = "speech"
    elif re.fullmatch(r"vote_\d+", node_id):
        kind = "vote"
    elif not node_id and instruction.startswith("你在第") and "被投出局了" in instruction:
        kind = "eulogy"
    else:
        raise ValueError("Unsupported player task")
    seat_m = re.search(r"你是\s*(\d+)\s*号", instruction)
    word_m = re.search(r"(?:你的词是|词是)【([^】\r\n]+)】", instruction)
    if kind == "eulogy":
        if previous is None:
            raise ValueError("Eulogy requires this session's earlier private seat")
        seat, word = previous.seat, previous.word
    elif seat_m and word_m:
        seat, word = int(seat_m[1]), word_m[1]
    else:
        raise ValueError("Missing private seat or word in referee instruction")
    max_m = re.search(r"不超过\s*(\d+)\s*(?:个)?字", instruction)
    max_chars = int(max_m[1]) if max_m else (10 if kind == "vote" else 25)
    if not 1 <= max_chars <= 100:
        raise ValueError("Unsupported output length")
    allowed: tuple[int, ...] = ()
    abstain = "禁止弃权" not in instruction and "不能弃权" not in instruction
    if kind == "vote":
        candidates = re.search(r"^可以投[：:]([^；;\r\n]*)", instruction, re.MULTILINE)
        if not candidates:
            raise ValueError("Missing legal vote candidates")
        targets = {int(n) for n in re.findall(r"(\d+)号", candidates[1])}
        targets.discard(seat)
        data = json.loads(public_input)
        if isinstance(data, dict) and "alive" in data:
            alive = data["alive"]
            if not isinstance(alive, list):
                raise ValueError("Invalid alive list")
            live_seats = set()
            for entry in alive:
                m = re.match(r"(\d+)号", str(entry))
                if m:
                    live_seats.add(int(m[1]))
            targets &= live_seats
        allowed = tuple(sorted(targets))
        if not allowed and not abstain:
            raise ValueError("PK vote has no legal targets")
    return PlayerTask(
        kind,
        node_id,
        instruction,
        public_input,
        upstream,
        seat,
        word,
        max_chars,
        allowed,
        abstain,
    )


def text_content(message: object) -> str:
    if not isinstance(message, dict):
        raise ValueError("Missing message")
    blocks = message.get("content")
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        raise ValueError("Missing text content")
    texts = [
        b["text"]
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
    ]
    if not texts:
        raise ValueError("No text blocks")
    return "\n".join(texts)
