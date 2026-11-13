"""复用“玩家稳健老陈”的人格提示词。

人格文本提炼自
``singlebox/agents/6bots_undercover_game_profile/player-laochen``；
这里不复制 profile 文件，只保留模型生成需要的语气与行为约束。
"""

from __future__ import annotations

from .tasks import GameTask

PERSONA = """你是“玩家稳健老陈”，谁是卧底牌桌上的稳健保守型玩家。
说话短、语气平，通常 8-14 个字；先给宽泛但真实的新线索，不抢话头，不用惊叹号，
不铺张形容词，不点评别人，不表态怀疑谁。投票只交票号，不写理由。
你只知道自己的词，不知道自己是平民还是卧底；不要声称身份，不要提规则、轮次或票数。"""

SYSTEM_RULES = """硬约束：
1. 只输出任务要求的那一句话本身，不解释、不反问、不加引号、不加前缀、不换行。
2. 任何情况下都不能说出自己的词，也不能说出词里连续的两个字，或把词里的字拆开在同一句里说全。
3. 不用拼音、英文、谐音、拆字或暗示去指自己的词。
4. 不编造别人说过的话；引用时用原话。
5. 收到看不懂的任务时，按最接近的格式给一句安全输出，不要空回复。"""


def speech_messages(task: GameTask) -> list[dict[str, str]]:
    bluntness = {1: 4, 2: 3}.get(task.round or 1, 2)
    instruction = (
        f"本轮第 {task.round} 轮发言。你的座位是 {task.seat} 号，你的词是【{task.word}】。\n"
        f"写一句不超过 {task.max_chars} 字的中文描述，描述这个词但不要说出它。\n"
        f"钝度要求：这句话要能同时套在至少 {bluntness} 样别的东西上；"
        "不下定义、不用类目名词、不说用途和对象在同一句。\n"
    )
    if task.is_pk:
        instruction += "这是 PK 发言，可以基于公开信息补充或辩解，但不能暴露私有信息。\n"
    else:
        instruction += "不要点评其他玩家，不要提身份、轮次、规则或票数。\n"
    upstream = task.upstream.strip()
    if upstream:
        instruction += f"\n本轮排在你前面的公开发言：\n{upstream}\n"
    return [
        {"role": "system", "content": f"{PERSONA}\n\n{SYSTEM_RULES}"},
        {"role": "user", "content": instruction + "\n只输出这一句话。"},
    ]


def vote_messages(task: GameTask) -> list[dict[str, str]]:
    targets = "、".join(f"{seat}号" for seat in task.legal_targets) or "（无）"
    option = "我投N号" if task.allow_abstain else "我投N号（本阶段禁止弃权）"
    instruction = (
        f"第 {task.round} 轮{'PK ' if task.is_pk else ''}投票。你是 {task.seat} 号，你的词是【{task.word}】。\n"
        f"可以投的座位：{targets}；不能投自己。\n"
        f"输出格式只能是“{option}”，N 用阿拉伯数字，不超过 {task.max_chars} 字，不写理由。\n"
        "只根据公开发言判断：先看前后矛盾，再看与你词的特征不符，再看与多数人共同特征不相容；"
        "证据弱也选一位，并列时选一位，不要固定选最小号。\n"
        "话少、风格和钝度不算嫌疑；不要编造发言，不假定自己一定是平民。"
    )
    context = task.input_text.strip() or task.upstream.strip()
    if context:
        instruction += f"\n\n公开发言记录：\n{context[:4000]}"
    return [
        {"role": "system", "content": f"{PERSONA}\n\n{SYSTEM_RULES}"},
        {"role": "user", "content": instruction + "\n只输出票面。"},
    ]


def eulogy_messages(task: GameTask) -> list[dict[str, str]]:
    seats = "、".join(f"{seat}号" for seat in task.candidate_seats)
    instruction = (
        f"你在第 {task.round} 轮被投出局了。请留一句遗言，不超过 {task.max_chars} 个字，"
        "语气平静，但必须有内容：点名一个你最怀疑的座位号，并点出对方哪句话让你在意。\n"
        f"场上出现过的座位：{seats}。\n"
        "不许说出自己的词，不许声称自己是卧底或平民，不许拿自己的词和别人比较，"
        "只能说别人的话与别人的话之间对不上。只输出这一句遗言。"
    )
    history = task.raw_text.strip()
    if history:
        instruction += f"\n\n本局公开记录：\n{history[:4000]}"
    return [
        {"role": "system", "content": f"{PERSONA}\n\n{SYSTEM_RULES}"},
        {"role": "user", "content": instruction},
    ]
