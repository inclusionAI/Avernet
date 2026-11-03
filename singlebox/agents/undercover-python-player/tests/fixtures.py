"""测试辅助：构造与 undercover.py 生成的节点 prompt 一致的真实 chat.send 样例。"""

from __future__ import annotations

import json


def build_speak_prompt(seat: int, word: str, rnd: int, previous=None,
                       display_name: str = "3号发言") -> str:
    """"发言节点 prompt —— 结构与 runtime/build_node_prompt 一致。

    [State Machine Task]
    node_id: speak_<seat>
    display_name: <seat>号发言
    [Input]        <- run.input, 公开历史（无词）
    [Upstream Outputs] <- 直系父节点产物（本轮之前的话）
    [Instruction]  <- 含词、座位、轮次、钝度、限制

    previous: list[(seat, phrase), ...] 按顺序即为车轮中排在本玩家前面的原话。
    """
    prev = "\n".join(f"[speak_{s}]\n{s}号：{t}\n" for s, t in (previous or []))
    blunt = {1: 4, 2: 3}.get(rnd, 2)
    inst = (
        f"你是 {seat} 号，你的词是【{word}】。第 {rnd} 轮发言。\n"
        "[Input] 含公开规则和开场；[Upstream Outputs] 只含本轮排在你前面的人的原话。\n"
        f"输出一句话，不超过 25 字，描述你的词。\n"
        f"不得出现「{word}」；不得出现{word[:2] if len(word)>1 else ''}；也不得把它的字拆到一句里说全。"
        f"不许拼音、英文、谐音。\n\n"
        f"本轮钝度 {blunt}：这句话要能同时套在至少 {blunt} 样别的东西上。\n\n"
        "不提身份、轮次、规则、票数，不点评别人。\n"
        "只输出这句话：没有编号、引号、JSON、前缀、解释。"
    )
    return (
        f"[State Machine Task]\nnode_id: speak_{seat}\ndisplay_name: {display_name}\n\n"
        f"[Input]\n{{\n  \"rules\": ...\n}}\n\n"
        f"[Upstream Outputs]\n{prev or '(none)'}\n\n"
        f"[Instruction]\n{inst}"
    )


def build_vote_prompt(seat: int, word: str, rnd: int, others: list[int],
                      previous=None, is_pk: bool = False,
                      display_name: str = "3号投票") -> str:
    """"投票节点 prompt。"""
    prev = "\n".join(f"[speak_{s}]\n{t}\n" for s, t in (previous or []))
    others_s = "、".join(f"{o}号" for o in others)
    pk_rule = "，禁止弃权，" if is_pk else "或「我弃权」，"
    idx = " ".join(str(o) for o in others)
    inst = (
        f"第 {rnd} 轮投票。只输出「我投N号」（阿拉伯数字）{pk_rule}"
        f"不超过 10 字，不写理由、解释或前缀。\n"
        f"你是 {seat} 号，词是【{word}】。全场：{ '3号 阿和、' if False else ''}{others_s}。\n"
        f"可以投：{others_s}；不能投自己。\n"
        "仅据 [Input] 全部历轮公开发言判断；忽略 [Upstream Outputs] 的预备确认。\n"
        "投给相对最可疑者：先看前后矛盾，再看与自己词的特征不符，再看与他人共同特征不相容。\n"
        f"{'PK 必须从合法候选中选一位，不能弃权。' if is_pk else '只有全部历轮都没有可用的玩家描述或无合法目标才弃权。'}\n"
        f"输出不得出现「{word}」或它的任何部分。"
    )
    return (
        f"[State Machine Task]\nnode_id: vote_{seat}\ndisplay_name: {display_name}\n\n"
        f"[Input]\n{{\n  \"rules\": ...\n}}\n\n"
        f"[Upstream Outputs]\n{prev or '(none)'}\n\n"
        f"[Instruction]\n{inst}"
    )


def build_vote_prompt_from_history(seat: int, word: str, rnd: int,
                                   alive: list[int], others: list[int],
                                   history: list[dict], is_pk: bool = False) -> str:
    """投票节点 prompt，`[Input]` 用真实 run.input JSON。

    对齐 `undercover.py::prepare_vote_run()` 的 run_input["history"]
    （public_history 的 seat/player/text 对象）以及 BCS
    `runtime.rs::build_node_prompt` 用 `serde_json::to_string_pretty` 写
    `[Input]` 的方式。文本级 fixture 无法覆盖这个结构。
    """
    run_input = {
        "game": "谁是卧底",
        "round": rnd,
        "phase": "PK 投票" if is_pk else "投票",
        "opening": f"第 {rnd} 轮投票开始。",
        "alive": [f"{s}号 玩家{s}" for s in sorted(alive)],
        "history": history,
        "rule": "只根据所有人历史全部发言投票；不能投自己；只交票号" +
                ("；仅投本次 PK 玩家，禁止弃权" if is_pk else ""),
    }
    input_json = json.dumps(run_input, ensure_ascii=False, indent=2)
    others_s = "、".join(f"{o}号" for o in others)
    inst = (
        f"第 {rnd} 轮{'PK 投票' if is_pk else '投票'}。只输出「我投N号」"
        f"（阿拉伯数字）{'，禁止弃权，' if is_pk else '或「我弃权」，'}"
        "不超过 10 字，不写理由、解释或前缀。\n"
        f"你是 {seat} 号，词是【{word}】。\n"
        f"可以投：{others_s}；不能投自己。\n"
        "仅据 [Input] 全部历轮公开发言判断；忽略 [Upstream Outputs] 的预备确认。\n"
        + ("PK 必须从合法候选中选一位，不能弃权。" if is_pk
           else "只有全部历轮都没有可用的玩家描述或无合法目标才弃权。")
    )
    return (
        f"[State Machine Task]\nnode_id: vote_{seat}\n"
        f"display_name: {seat}号投票\n\n"
        f"[Input]\n{input_json}\n\n"
        "[Upstream Outputs]\n[vote_start]\n投票开始，请大家投出一票，"
        "等待主持人公布结果。\n\n"
        f"[Instruction]\n{inst}"
    )


def chat_send_frame(content: str, session_key: str = "grp-00000001:abcd1234",
                    rid: str = "smnode-1-speak_3-0") -> dict:
    """构造 BCS 下发的 chat.send ReqFrame。"""
    return {
        "type": "req",
        "id": rid,
        "method": "chat.send",
        "params": {
            "session_key": session_key,
            "bcs_session_id": session_key,
            "message": {"role": "user",
                        "content": [{"type": "text", "text": content}],
                        "timestamp": 1710960000000},
            "deliver": True,
        },
    }


def chat_abort_frame(run_id: str, session_key: str = "grp-00000001:abcd1234",
                     rid: str = "abort-1") -> dict:
    return {"type": "req", "id": rid, "method": "chat.abort",
            "params": {"session_key": session_key, "run_id": run_id}}