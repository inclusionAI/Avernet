"""测试用游戏提示词，形状与主持人脚本渲染结果一致。"""

from __future__ import annotations

import json

SESSION_KEY = "grp-test:abcdef12"
SESSION_ID = "grp-test:abcdef12"
GROUP_ID = "grp-test"


def speech_instruction(word: str = "苹果", seat: int = 3, round_number: int = 1, pk: bool = False) -> str:
    phase = "PK 发言" if pk else "发言"
    return (
        f"你是 {seat} 号，你的词是【{word}】。第 {round_number} 轮{phase}。\n"
        "[Input] 含公开规则和开场；[Upstream Outputs] 只含本轮排在你前面的人的原话。\n"
        "[Input]：历史轮次的发言。\n"
        f"输出一句话，不超过 25 字，描述你的词。\n"
        f"不得出现「{word}」；也不得把它的字拆到一句里说全；不许拼音、英文、谐音。\n\n"
        "本轮钝度 4：描述至少适用于 4 样东西，不下定义、不用类目名词。\n"
        "从「什么时候会想到它 / 多久遇到一次 / 用完是什么感觉 / 一般放在哪儿 / 大概是什么样」选一个角度。\n"
        "可带一个属性（形状/材质/大小/场合四选一）；用途和对象不能进同一句。\n"
        "每轮补充一个符合自己词的真实线索，优先选自己和前序玩家尚未说过的角度；"
        "避免只说泛泛感受或换词复述。\n"
        "你是首发，给出一个可比较的宽泛特征。\n\n"
        + (
            "本次 PK，可基于公开发言和已公布票面补充描述或辩解，不暴露私有信息。\n"
            if pk
            else "不提身份、轮次、规则、票数，不点评别人。\n"
        )
        + "只输出这句话：没有编号、引号、JSON、前缀、解释。"
    )


def vote_instruction(
    word: str = "苹果",
    seat: int = 3,
    round_number: int = 1,
    pk: bool = False,
    targets: tuple[int, ...] = (1, 2, 4),
) -> str:
    target_text = "、".join(f"{value}号" for value in targets)
    phase = "PK 投票" if pk else "投票"
    rule = "禁止弃权，" if pk else "或「我弃权」，"
    return (
        f"第 {round_number} 轮{phase}。只输出「我投N号」（阿拉伯数字）{rule}"
        "不超过 10 字，不写理由、解释或前缀。\n"
        f"你是 {seat} 号，词是【{word}】。全场：1号 老陈、2号 小满、3号 阿和、4号 林工。\n"
        f"可以投：{target_text}；不能投自己。\n"
        "仅据 [Input] 全部历轮公开发言判断；忽略 [Upstream Outputs] 的预备确认。\n"
        "投给相对最可疑者：先看前后矛盾，再看与自己词的特征不符，再看与他人共同特征不相容。"
        "证据弱也选；从并列者中选一位，不固定选最小号。\n"
        + ("PK 必须从合法候选中选一位，不能弃权。\n" if pk else "只有全部历轮都没有可用的玩家描述或无合法目标才弃权；首轮或不确定也应投票。\n")
        + "话少、风格和钝度本身不算嫌疑；不编造发言，不假定自己一定是平民。\n"
        f"输出不得出现「{word}」或它的任何部分。"
    )


VOTE_START_INSTRUCTION = (
    "【投票预备确认】主持人已经提交本轮投票。请原样输出下面这一句开场白：\n"
    "投票开始，请大家投出一票，等待主持人公布结果。\n"
    "只输出这句话，不加称呼、解释、引号或表情，不按人设改写。"
    "这不是投票，不调用工具、不评价发言、不提玩家或词语。"
    "播报后等待独立的投票任务，再按那个任务投票。"
)

EULOGY_TEXT = (
    "[from:主持人] 你在第 1 轮被投出局了。按规矩你可以留一句遗言，主持人会念给全场听。\n\n"
    "到目前为止全场说过的话：\n"
    "第 1 轮：\n"
    "1号 老陈：「平时不太动它，占地方。」\n"
    "2号 小满：「红彤彤的，很甜。」\n"
    "4号 林工：「用起来得两只手，急不得。」\n\n"
    "请说一句遗言，不超过 35 个字，语气还是你自己的性格，但必须有内容："
    "挑一个你最怀疑的座位号，说清是他哪句话让你在意——把那句话里的关键几个字点出来。\n"
    "三条硬规矩：\n"
    "1. 不许说出你自己那个词，也不许说它里面连续的两个字、或者把它拆开说全；"
    "不许用拼音、英文或谐音去指它。\n"
    "2. 不许说自己是不是卧底，也不许拿自己的词去和别人比。\n"
    "3. 只输出这一句遗言，不要解释、不要前缀。"
)


def state_machine_text(node_id: str, instruction: str, *, upstream: str = "", input_text: str = "") -> str:
    return (
        "[State Machine Task]\n"
        f"node_id: {node_id}\n"
        "display_name: 3号发言\n\n"
        "[Input]\n"
        f"{input_text or json.dumps({'game': '谁是卧底', 'round': 1}, ensure_ascii=False)}\n\n"
        "[Upstream Outputs]\n"
        f"{upstream or '(none)'}\n\n"
        "[Instruction]\n"
        f"{instruction}"
    )


def chat_send_frame(
    text: str,
    *,
    run_id: str = "smnode-run-1-speak_3-0",
    session_key: str = SESSION_KEY,
    session_id: str = SESSION_ID,
    timeout_ms: int | None = 420_000,
) -> dict[str, object]:
    params: dict[str, object] = {
        "session_key": session_key,
        "bcs_group_id": GROUP_ID,
        "bcs_session_id": session_id,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
        "session_context": {"session_id": GROUP_ID, "participants": []},
    }
    if timeout_ms is not None:
        params["timeout_ms"] = timeout_ms
    return {"type": "req", "id": run_id, "method": "chat.send", "params": params}
