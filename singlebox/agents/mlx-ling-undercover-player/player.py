"""「谁是卧底」MLX 玩家 Bot —— 游戏逻辑层。

把 BCN 收到的主持人节点任务（[State Machine Task] 消息）解析出来，
映射为微调模型训练时的提示词格式（与 ling-cookbook recipe 的
data/prompting.py 保持一致），调本地模型，再按规则清洗输出。

任务消息格式（Avernet bcs-collaboration-runtime build_node_prompt）：
    [State Machine Task]
    node_id: speak_3
    display_name: 3号发言

    [Input]
    {pretty JSON: opening/game/round/alive/history/rule/...}

    [Upstream Outputs]
    [speak_1]
    <1号原话>

    [Instruction]
    你是 3 号，你的词是【苹果】。第 2 轮发言。...
"""

import asyncio
import json
import re

from rules import clamp_clue, parse_vote, ABSTAIN

# 训练时的提示词格式（与 data/prompting.py 同构；此处内嵌以保持 Bot 独立可分发）
RULES_SPEAK = (
    "规则：\n"
    "1. 发言不超过25个字\n"
    "2. 不要说出你词语里的任何一个字\n"
    "3. 描述要有信息量，但别让人一眼看穿你的词"
)
RULES_VOTE = (
    "规则：\n"
    "1. 根据大家的发言找出可疑的玩家\n"
    "2. 只输出「我投N号」或「我弃权」，不要输出其他内容"
)

PRESTART_LINE = "投票开始，请大家投出一票，等待主持人公布结果。"

HISTORY_WINDOW = 2  # 与 data/prompting.py 一致：第 1 轮 + 最近 2 轮，中间折叠


def render_history(rounds, window=HISTORY_WINDOW):
    """窗口化历史序列化（与 ling-cookbook data/prompting.py 同款）。

    保留第 1 轮（初始描述信息量最大）与最近 window 轮，中间轮折叠成
    「（第x-y轮略）」；prompt 有界（≤~1k 字符），训练/推理分布一致。
    """
    if not rounds:
        return "（暂无）"
    if window is not None and len(rounds) > window + 1:
        kept = [rounds[0]] + rounds[-window:]
    else:
        kept = list(rounds)
    lines, prev = [], None
    for r in kept:
        if prev is not None and r["round"] > prev + 1:
            lines.append(f"（第{prev + 1}-{r['round'] - 1}轮略）")
        lines.append(f"第{r['round']}轮：")
        for seat, text in r["utterances"]:
            lines.append(f"{seat}号：{text}")
        prev = r["round"]
    return "\n".join(lines)


def clamp_history_text(text, limit=800):
    """零样本提示词（遗言）里的裁判原文历史：按行截尾，保最近内容。"""
    if len(text) <= limit:
        return text
    lines = text.splitlines()
    kept = []
    used = 0
    for line in reversed(lines):
        used += len(line) + 1
        if used > limit:
            break
        kept.append(line)
    return "（前略）\n" + "\n".join(reversed(kept))


def parse_task(text):
    """解析 [State Machine Task] 消息 → dict(kind/seat/word/round/rounds/pk/candidates)。"""
    # 遗言/预备任务（bcs_assign_task 直派，不是节点任务，无 [State Machine Task] 头）
    if "[State Machine Task]" not in text:
        if "被投出局" in text and "遗言" in text:
            history = ""
            m = re.search(r"到目前为止全场说过的话：\n(.*?)\n\n请说一句遗言", text, re.S)
            if m:
                history = m.group(1).strip()
            return {"kind": "eulogy", "history": history}
        if "表示你准备好了" in text:
            return {"kind": "standby"}
        # 其它非节点消息（开局 GroupContext、群通知等）：静默回执，不进模型
        return {"kind": "ignore"}
    # 节点类型：投票预备确认（要原样念开场白）
    if "【投票预备确认】" in text:
        return {"kind": "ack"}

    instruction = text.split("[Instruction]", 1)[-1] if "[Instruction]" in text else text
    pk = "PK" in instruction

    if "投票" in instruction:
        kind = "vote"
    else:
        kind = "speak"

    m = re.search(r"你是\s*(\d+)\s*号", instruction)
    seat = int(m.group(1)) if m else None
    m = re.search(r"你的词是【(.+?)】|词是【(.+?)】", instruction)
    word = (m.group(1) or m.group(2)) if m else ""
    m = re.search(r"第\s*(\d+)\s*轮", instruction)
    rnd = int(m.group(1)) if m else 1

    # [Input] 的 run_input JSON（含公开发言史）
    rounds = []
    m = re.search(r"\[Input\]\s*\n", text)
    if m:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[m.end():])
            for entry in obj.get("history", []):
                rounds.append({
                    "round": entry["round"],
                    "utterances": [(s["seat"], s["text"]) for s in entry.get("speeches", [])],
                })
        except json.JSONDecodeError:
            pass

    # [Upstream Outputs]：本轮排在我前面的人的原话（vote 节点的上游是预备确认，忽略）
    m = re.search(r"\[Upstream Outputs\](.*?)(?:\n\[Instruction\]|\Z)", text, re.S)
    upstream = []
    if m:
        for sm in re.finditer(r"\[(speak_(\d+))\]\s*\n(.*?)(?=\n\[|\Z)", m.group(1), re.S):
            upstream.append((int(sm.group(2)), sm.group(3).strip()))
    if kind == "speak" and upstream:
        rounds.append({"round": rnd, "utterances": upstream})

    # 投票候选（PK 禁弃权时兜底用）
    candidates = []
    m = re.search(r"可以投：(.+?)；", instruction)
    if m:
        candidates = [int(x) for x in re.findall(r"(\d+)号", m.group(1))]

    return {"kind": kind, "seat": seat, "word": word, "round": rnd,
            "rounds": rounds, "pk": pk, "candidates": candidates}


def build_prompt(task, role, word):
    """组训练同款提示词（历史窗口化，与 data/prompting.py 同款渲染）。"""
    history = render_history(task["rounds"])
    if task["kind"] == "speak":
        return (
            "你在玩「谁是卧底」游戏。\n"
            f"你的身份：{role}\n"
            f"你的词语：{word}\n"
            f"{RULES_SPEAK}\n"
            f"历史发言：\n{history}\n"
            "现在轮到你发言。\n"
            "请只输出你的发言内容（不超过25个字）："
        )
    return (
        "你在玩「谁是卧底」游戏。\n"
        f"你的身份：{role}\n"
        f"你的词语：{word}\n"
        f"{RULES_VOTE}\n"
        f"历史发言：\n{history}\n"
        "现在开始投票。\n"
        "请只输出「我投N号」或「我弃权」："
    )


class GamePlayer:
    """on_task 回调的实现：解析 → 提示词 → 模型 → 清洗。"""

    def __init__(self, infer_fn, undercover_words=()):
        """infer_fn: (prompt) -> str  模型推理（OpenAI 兼容或本地 mlx_lm）
        undercover_words: 词库中属于卧底词的集合，用于推断身份（裁判不下发身份）。
        """
        self.infer_fn = infer_fn
        self.undercover_words = set(undercover_words)

    async def on_task(self, session_key, text, params=None):
        task = parse_task(text)
        if task["kind"] == "ignore":
            return ""  # 静默回执：空 final 不产生群消息
        if task["kind"] == "ack":
            return PRESTART_LINE
        if task["kind"] == "standby":
            return "收到，我准备好了。"
        if task["kind"] == "eulogy":
            # 遗言：出局者视角的短发言。词已不可知（任务正文刻意不含词），
            # 泄词由主持人 mask 兜底；这里只管 35 字内、有内容。
            prompt = (
                "你在玩「谁是卧底」游戏，你刚被投票出局，可以留一句遗言。\n"
                "挑一个你最怀疑的座位号，说清是哪句话让你在意。\n"
                f"全场说过的话：\n{clamp_history_text(task.get('history') or '') or '（暂无）'}\n"
                "只输出这一句遗言，不超过35个字，不要解释、不要前缀："
            )
            raw = await asyncio.to_thread(self.infer_fn, prompt)
            line = re.sub(r"\s+", "", raw)[:35]
            return line or "我没什么好说的。"

        role = "卧底" if task["word"] in self.undercover_words else "平民"
        prompt = build_prompt(task, role, task["word"])
        # 推理放到线程池：OpenAI/MLX 同步调用不阻塞事件循环（心跳、abort 要及时处理）
        raw = await asyncio.to_thread(self.infer_fn, prompt)

        if task["kind"] == "vote":
            vote = parse_vote(raw)
            if vote == ABSTAIN and task["pk"] and task["candidates"]:
                # PK 禁止弃权：兜底选一个非自己的候选（等价随机弃权替代）
                seat = task["candidates"][0]
                vote = f"我投{seat}号"
            return vote

        return clamp_clue(raw, banned_chars=set(task["word"]))
