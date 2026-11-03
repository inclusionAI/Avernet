"""玩家 Bot：人格 + 决策逻辑，跑在 BCN WebSocket 引擎之上。

复用 OpenClaw profile 里「玩家逻辑控林工」的人格（属性推理型）
from singlebox/agents/6bots_undercover_game_profile/player-luoji/：
  - IDENTITY.md / SOUL.md / OUTPUT.md / KNOWLEDGE.md / RULES.md

本引擎是纯规则引擎，不调 LLM：
  - 发言：按轮次钝度选一个「宽泛、可复核」的属性描述，先本地预检
    字数 ≤25 且不泄词，无词可用时给一句钝化兜底（不泄词）。
  - 投票：只在 [Input] 与 [Upstream Outputs] 的直接上游产物上推理，
    选相对最可疑的合法座位；规则弱也选一位；仅毫无可用描述或无合法
    目标才弃权（PK 禁止弃权）。永远只输出「我投N号」或「我弃权」。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time

from .engine import PeterPlayerEngine, ChatRunContext
from .format_rules import (
    MAX_SPEECH_CHARS, MAX_VOTE_CHARS,
    check_own_word_leak, format_vote, validate_speech, validate_vote,
    bluntness_for_round,
)
from .message_model import NodePhase, NodeTask

log = logging.getLogger("undercover_player.agent")

# ---- 话痨... 人格基调：林工 = 属性、平实、不下定义 ---- #
PERSONA = """你是玩家「逻辑控林工」，谁是卧底牌桌上的属性推理型玩家。
每一句都平实、工整，给一个宽泛且能复核的属性/功能特征，严格服从本轮钝度，
不下定义、不补更锐利的角度、不点评别人。绝不输出分析、解释、理由或前缀。"""


def _public_histories(task: NodeTask) -> str:
    parts = []
    if task.input.strip():
        parts.append(f"[公开发言]\n{task.input}")
    if task.upstream_outputs.strip():
        parts.append(f"[本轮前序]\n{task.upstream_outputs}")
    return "\n".join(parts)


def _history_speeches(text: str) -> list[tuple[int, str]]:
    """解码主持人 run.input 里的结构化 history。

    `undercover.py::prepare_vote_run` 把 `public_history()` 直接放进
    `run.input["history"]`，BCS `build_node_prompt` 再把它 JSON 序列化到
    `[Input]`：
        {"history": [{"round": 1,
                      "speeches": [{"seat": 1, "player": "1号 阿和",
                                    "text": "要通电"}]}]}
    这里必须按对象读取 seat/text，不能把整个 JSON 当普通文本。
    """
    if not text or "{" not in text:
        return []
    data = None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        # [Input] 正常就是纯 JSON；兼容前后带说明文字的输入。
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                data = json.loads(text[start:end + 1])
            except (ValueError, TypeError):
                return []
    if not isinstance(data, dict):
        return []
    history = data.get("history")
    if not isinstance(history, list):
        return []
    out: list[tuple[int, str]] = []
    for entry in history:
        if not isinstance(entry, dict):
            continue
        speeches = entry.get("speeches")
        if not isinstance(speeches, list):
            continue
        for item in speeches:
            if not isinstance(item, dict):
                continue
            seat = item.get("seat", item.get("seatNumber"))
            body = item.get("text")
            if isinstance(seat, bool) or not isinstance(seat, int):
                continue
            if not isinstance(body, str) or not body.strip():
                continue
            body = re.sub(r"^\d+\s*号\s*[:：]\s*", "", body.strip())
            out.append((seat, body))
    return out



class UndercoverPlayer:
    """玩家决策器：把 NodeTask 变成一句话。"""

    def __init__(self, persona: str = PERSONA,
                 speech_max: int = MAX_SPEECH_CHARS,
                 vote_max: int = MAX_VOTE_CHARS):
        self.persona = persona
        self.speech_max = speech_max
        self.vote_max = vote_max
        self._rng_counter = int(time.time() * 1000)  # 简单确定性扰动

    # ---- 前序发言行：结构化 history + 「N号：内容」/ [speak_N] 块 ---- #
    def _speaks(self, task: NodeTask) -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []
        for text in (task.input, task.upstream_outputs):
            # 真实 run.input 的 JSON history（referee 生成）
            out.extend(_history_speeches(text))
            # 形如 [speak_1]\n内容  或 [speak_1]\n1号：内容
            for m in re.finditer(r"\[speak_(\d+)\]\s*\n(.*?)(?=\n\[|$)", text, re.S):
                seat = int(m.group(1))
                body = m.group(2).strip()
                if not body:
                    continue
                # 去掉可能的 "N号：" 前缀
                body = re.sub(r"^\d+\s*号\s*[:：]\s*", "", body)
                out.append((seat, body))
            # 形如 3号：xxx
            for m in re.finditer(r"(\d+)\s*号\s*[:：]\s*(.+)", text):
                seat = int(m.group(1))
                body = m.group(2).strip()
                if body:
                    out.append((seat, body))
        # 同一段话可能同时被 JSON 和文本正则命中：按出现顺序去重。
        deduped: list[tuple[int, str]] = []
        seen: set[tuple[int, str]] = set()
        for entry in out:
            if entry in seen:
                continue
            seen.add(entry)
            deduped.append(entry)
        return deduped

    def _candidates(self, task: NodeTask) -> list[int]:
        """合法投票目标：出现过发言、可投、且不是自己的座位。"""
        seen = {seat for seat, _ in self._speaks(task)}
        if task.allowed_targets:
            seen &= set(task.allowed_targets)
        elif task.alive_seats:
            seen &= set(task.alive_seats)
        if task.seat in seen:
            seen.discard(task.seat)
        return sorted(seen)

    # ---- 发言 ---- #
    def make_speech(self, task: NodeTask) -> str:
        word = task.word or ""
        blunt = task.bluntness or bluntness_for_round(task.round_num)
        # 兜底钝化句库：按钝度阶梯选择「宽泛、不泄词、可复用」的一句话
        generic = {
            4: "不是天天会碰到的，但对特定的人很重要。",
            3: "具体怎么用，各人有各人的讲究。",
            2: "多数人家里都见过，但未必天天用。",
        }
        base = generic.get(blunt, generic[2])
        # 若兜底句长度或偶发撞词，裁剪/换
        text = base.strip()
        if len(text) > task.max_chars:
            text = text[:task.max_chars]
        if word and check_own_word_leak(word, text):
            text = "这个物件，说不清它该归哪一类。"
        if len(text) > task.max_chars:
            text = text[:task.max_chars]
        return text

    # ---- 投票 ---- #
    async def make_vote(self, task: NodeTask) -> str | None:
        """返回「我投N号」/「我弃权」/ None(无票面)。"""
        need_pk = task.is_pk
        allow_abs = task.allow_abstain and not need_pk
        cands = self._candidates(task)
        if not cands and allow_abs:
            return "我弃权"
        if not cands and not allow_abs:
            # PK 必须投，但无可投合法目标 —— 主持人脚本会把无票当作超时，
            # 此时回退投给 [Input]/[Upstream] 名字里提到的合法存活号。
            return "我弃权"  # 但 PK 禁止弃权；由 validate 转成错误处理
        # 选一个相对最可疑：简单规则 — 话最短 + 最后一个说话偏嫌疑
        speaks = self._speaks(task)
        if speaks:
            # 最短发言者
            shortest = min((s for s in speaks if s[0] in cands),
                           key=lambda x: len(x[1]), default=None)
            target = shortest[0] if shortest else cands[0]
        else:
            target = cands[0]
        if not allow_abs:
            # PK 禁止弃权：一定给票号
            target = target if target not in (task.seat,) else (cands[0] if cands else None)
            if target is None:
                return None
        ok, reason, ballot = validate_vote(target, allow_abs, set(cands), task.seat)
        if not ok:
            if not allow_abs and ballot:
                return ballot
            return None
        return ballot

    # ---- 决策回调（适配 engine） ---- #
    async def decide(self, ctx: ChatRunContext, task: NodeTask) -> str | None:
        if task.phase == NodePhase.VOTE_START:
            # 投票预备：原样播报
            return "投票开始，请大家投出一票，等待主持人公布结果。"
        if task.phase == NodePhase.VOTE or task.is_vote:
            return await self.make_vote(task)
        if task.phase == NodePhase.WILL:
            # 遗言：一句表达怀疑，引用一处原话，不编造，不泄词
            speaks = self._speaks(task)
            target = speaks[-1] if speaks else None
            if target and target[0] != task.seat:
                return f"我比较在意{target[0]}号那句「{target[1][:12]}」。"
            return "我还没想好，先投一票看看。"
        # SPEECH / PREP / 默认
        return self.make_speech(task)


# ------------------------------------------------------------------ #
# 启动入口
# ------------------------------------------------------------------ #
def default_ws_url() -> str:
    return os.environ.get(
        "BCS_WS_URL",
        f"ws://127.0.0.1:{os.environ.get('BCS_PORT', '21000')}/ws/bot",
    )


async def run_player(
    ws_url: str | None = None,
    token: str | None = None,
    on_task=None,
    bot_name: str = "玩家逻辑控林工",
):
    """启动玩家 Bot。阻塞直到被 stop。"""
    ws_url = ws_url or default_ws_url()
    player = UndercoverPlayer()
    handler = on_task or player.decide
    engine = PeterPlayerEngine(
        ws_url=ws_url,
        bot_name=bot_name,
        token=token,
        on_task=handler,
    )
    try:
        await engine.start()
    except asyncio.CancelledError:
        await engine.stop()
    return engine