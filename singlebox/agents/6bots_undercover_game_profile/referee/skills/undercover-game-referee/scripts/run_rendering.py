"""StateMachine YAML and panel rendering."""
from __future__ import annotations

import json
import re
from typing import Any

__all__ = ['yq', 'block', 'forbid_line', 'append_ui_context', 'actor_id_for', 'public_panel_projection', 'panel_tab_metadata', 'write_run_files', 'opening_announcement', 'render_speak_yaml', 'render_vote_yaml']

def yq(api, value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def block(api, text: str, indent: int) -> str:
    pad = " " * indent
    lines = text.rstrip("\n").split("\n")
    return "\n".join(pad + line if line else "" for line in lines)


def forbid_line(api, word: str) -> str:
    """字面泄词的红线。写成一行清单，让它照着看一眼就行——这是查表，不是让它把
    自己写的句子逐字扫一遍。真正的兜底是 check_text：服务端逐字判定 + mask 遮蔽，
    所以这里不需要把每种变体都展开成一段说明。"""
    parts = [f"不得出现「{word}」"]
    grams = api.bigrams(word)
    if len(grams) > 1:
        parts.append("不得出现" + "、".join(f"「{g}」" for g in grams))
    if len(word) > 1:
        parts.append("也不得把它的字拆到一句里说全")
    parts.append("不许拼音、英文、谐音")
    return "；".join(parts) + "。"


def append_ui_context(api, instruction: str, context: dict[str, Any]) -> str:
    """Append viewer-private machine context to a HumanInput instruction."""
    payload = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    return f"{instruction.rstrip()}\n\n{api.UI_CONTEXT_OPEN}\n{payload}\n{api.UI_CONTEXT_CLOSE}"


def actor_id_for(api, seat: dict[str, Any], state: dict[str, Any]) -> str:
    return state["human_actor_id"] if seat["kind"] == "human" else seat["bot_uuid"]


def public_panel_projection(api, state: dict[str, Any], kind: str, attempt: int) -> dict[str, Any]:
    """Build the complete whitelist-only public projection for one phase."""
    all_seats = sorted(state["seats"], key=lambda seat: seat["seat"])
    living = [seat for seat in all_seats if seat["alive"]]
    phase = ("pk_" if api.is_pk(state) else "") + ("speaking" if kind == "speak" else "voting")
    acting = api.speech_seats(state) if kind == "speak" else living
    node_prefix = "speak" if kind == "speak" else "vote"
    seat_order = [api.actor_id_for(seat, state) for seat in all_seats]
    turn_order = [api.actor_id_for(seat, state) for seat in acting]
    players = [{"actorId": api.actor_id_for(seat, state), "displayName": seat["display"], "seatNumber": int(seat["seat"]), "isHuman": seat["kind"] == "human", "alive": bool(seat["alive"]), "eliminated": not bool(seat["alive"])} for seat in all_seats]
    node_actor_map = {f"{node_prefix}_{seat['seat']}": api.actor_id_for(seat, state) for seat in acting}
    for node_id in (("collect",) if kind == "speak" else ("tally",)):
        node_actor_map[node_id] = state["referee_uuid"]
    human = next((seat for seat in acting if seat["kind"] == "human"), None)
    current_action = None if human is None else {"actorId": api.actor_id_for(human, state), "type": "speech" if kind == "speak" else "vote", "nodeId": f"{node_prefix}_{human['seat']}"}
    history = [{"round": entry["round"], **({"stage": "pk"} if entry.get("stage") == "pk" else {}), "speeches": [{"actorId": api.actor_id_for(api.seat_of(state, item["seat"]), state), "seatNumber": item["seat"], "displayName": item["player"], "text": item["text"]} for item in entry["speeches"]]} for entry in api.public_history(state)]
    params: dict[str, Any] = {
        "runId": api.RUN_ID_TEMPLATE, "groupId": state["group_id"] or "{{bcs.group_id}}", "sessionId": state["session_id"], "gameSessionId": state["session_id"],
        "phase": phase, "round": state["round"], "attempt": attempt,
        "host": {"actorId": state["referee_uuid"], "displayName": "主持人"}, "seatOrder": seat_order, "turnOrder": turn_order, "players": players, "nodeActorMap": node_actor_map,
        "publicHistory": history, "rules": {"speechMaxChars": api.SPEECH_MAX_CHARS, "voteMaxChars": api.VOTE_MAX_CHARS, "forbidOwnWord": kind == "speak", "bluntness": api.bluntness_n(state["round"])},
        "currentViewerActorId": state["human_actor_id"], "currentAction": current_action,
        "display": {"showVoteResults": False, "showHostOutput": True, "showTimer": True},
    }
    if api.is_pk(state):
        params["pkCandidates"] = [api.actor_id_for(api.seat_of(state, seat), state) for seat in api.current_stage(state)["order"]]
    vote_history = api.public_vote_history(state)
    if vote_history:
        params["voteHistory"] = vote_history
    if kind == "vote" and human is not None:
        viewer_id = api.actor_id_for(human, state)
        params["voteCandidates"] = [{"actorId": api.actor_id_for(seat, state), "displayName": seat["display"], "seatNumber": int(seat["seat"]), "eligible": True} for seat in living if api.actor_id_for(seat, state) != viewer_id and (not api.is_pk(state) or seat["seat"] in api.current_stage(state)["order"])]
    if kind == "vote":
        params["resultFile"] = f"undercover-result-v1-r{state['round']}{'-pk' if api.is_pk(state) else ''}-a{attempt}.json"
        params["openingAnnouncement"] = api.opening_announcement(state, kind, attempt)
    return params


def panel_tab_metadata(api, state: dict[str, Any], kind: str, attempt: int) -> dict[str, str]:
    phase = ("PK " if api.is_pk(state) else "") + ("发言" if kind == "speak" else "投票")
    safe_session = re.sub(r"[^A-Za-z0-9_.-]", "-", state["session_id"])
    return {"id": f"undercover-game-{safe_session}", "title": f"谁是卧底 · 第 {state['round']} 轮{phase}"}


def write_run_files(api,
    session_id: str,
    kind: str,
    yaml_text: str,
    run_input: dict[str, Any],
    panel_params: dict[str, Any],
) -> tuple[str, str, str]:
    d = api.work_dir(session_id)
    yaml_path = d / f"{kind}.yaml"
    input_path = d / f"{kind}-input.json"
    panel_params_path = d / f"{kind}-panel-params.json"
    yaml_path.write_text(yaml_text, encoding="utf-8")
    input_path.write_text(json.dumps(run_input, ensure_ascii=False, indent=2), encoding="utf-8")
    panel_params_path.write_text(json.dumps(panel_params, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(yaml_path), str(input_path), str(panel_params_path)


def opening_announcement(api, state: dict[str, Any], kind: str, attempt: int) -> str:
    """Only public phase information; speech is spoken, vote is shown by the panel."""
    if api.is_pk(state):
        action = "发言" if kind == "speak" else "投票"
        retry = f"本次 PK 之前的{action}作废，请重新{action}。" if attempt > 1 else ""
        roster = "、".join(api.label_of(state, seat) for seat in api.current_stage(state)["order"])
        rule = "上述玩家依次补充描述或辩解。" if kind == "speak" else "所有存活玩家仅投 PK 玩家，禁止自投和弃权，全部提交后统一计票。"
        return retry + f"第 {state['round']} 轮 PK {action}开始，PK 玩家：{roster}。" + rule
    retry = ("本轮之前的发言作废，请重新发言。" if kind == "speak" else
             "本轮之前的票作废，请重新投票。") if attempt > 1 else ""
    if kind == "vote":
        return retry + f"第 {state['round']} 轮投票开始，请在副屏投票，全部提交后统一计票。"
    living = sorted(api.alive_seats(state), key=lambda seat: seat["seat"])
    roster = "、".join(f"{seat['seat']}号 {seat['display']}" for seat in living)
    lead = living[0]
    return (retry + f"第 {state['round']} 轮发言开始，场上还有：{roster}。"
            f"从 {lead['seat']}号 {lead['display']} 开始依次发言；轮到你时请在副屏填写。")


def render_speak_yaml(api, state: dict[str, Any]) -> tuple[str, list[str]]:
    rnd = state["round"]
    pk = api.is_pk(state)
    phase_label = "PK 发言" if pk else "发言"
    living = api.speech_seats(state)
    node_ids = [f"speak_{s['seat']}" for s in living]

    participants: list[str] = []
    for s in living:
        if s["kind"] == "bot":
            participants.append(
                f"  {s['binding']}:\n"
                f"    display_name: {api.yq(str(s['seat']) + '号 ' + s['display'])}\n"
                f"    required: true"
            )
    participants.append('  referee:\n    display_name: "主持人"\n    required: true')

    # 首位玩家直接作为入口，避免向仍在提交运行的主持人自派任务。
    nodes: list[str] = []
    for idx, s in enumerate(living):
        nid = node_ids[idx]
        targets = node_ids[idx + 1 :] + ["collect"]
        targets_yaml = "[" + ", ".join(targets) + "]"
        first = idx == 0
        if s["kind"] == "human":
            # 人类节点的指令要短。Bot 节点那套（枚举二字组合、钝度自检问句、类目
            # 白名单）是写给模型看的硬约束，人类读到一半就跳过了——实测他两轮都
            # 直接给了定义句。他需要的只是：我是几号、我的词、一句话多长、别说出
            # 那个词、说钝一点。字面泄词有 check_text 兜底，不必在这里枚举。
            instruction = api.append_ui_context((
                f"【你的词语】{s['word']}\n\n"
                f"第 {rnd} 轮 · 你是 {s['seat']} 号 · 轮到你{phase_label}了。\n"
                "上面能看到本轮在你之前的人说了什么。\n\n"
                f"写一句话（不超过 {api.SPEECH_MAX_CHARS} 个字）描述你的词，"
                "别把这个词说出来，也别拆开来说。\n"
                f"说钝一点：这句话得能同时套在至少 {api.bluntness_n(rnd)} 样别的东西上。\n"
                + ("本次 PK，可基于公开发言和已公布票面补充描述或辩解，不暴露私有信息。" if pk else
                 "别提身份、别点评别人，只说你的词。")
            ), {"action": "speech", "round": rnd, "seatNumber": s["seat"], "word": s["word"], "maxChars": api.SPEECH_MAX_CHARS, "forbidOwnWord": True, "bluntness": api.bluntness_n(rnd)})
            nodes.append(
                f"      {nid}:\n"
                f"        kind: human_input\n"
                f"        display_name: {api.yq('👤 你的发言（' + str(s['seat']) + '号 · 人类玩家）')}\n"
                f"        node_timeout_ms: {api.HUMAN_NODE_TIMEOUT_MS}\n"
                f"        instruction: |\n{api.block(instruction, 10)}\n"
                f"        transitions:\n"
                f"          complete:\n"
                f"            targets: {targets_yaml}"
            )
        else:
            instruction = (
                f"你是 {s['seat']} 号，你的词是【{s['word']}】。第 {rnd} 轮{phase_label}。\n"
                "[Input] 含公开规则和开场；[Upstream Outputs] 只含本轮排在你前面的人的原话。"
                "[Input]：历史轮次的发言。\n"
                f"输出一句话，不超过 {api.SPEECH_MAX_CHARS} 字，描述你的词。\n"
                f"{api.forbid_line(s['word'])}\n\n"
                f"{api.bluntness_block(rnd, first)}\n\n"
                + ("本次 PK，可基于公开发言和已公布票面补充描述或辩解，不暴露私有信息。\n" if pk else
                 "不提身份、轮次、规则、票数，不点评别人。\n")
                + "只输出这句话：没有编号、引号、JSON、前缀、解释。"
            )
            nodes.append(
                f"      {nid}:\n"
                f"        kind: bot_task\n"
                f"        display_name: {api.yq(str(s['seat']) + '号发言')}\n"
                f"        assignee:\n"
                f"          type: bot_binding\n"
                f"          binding: {s['binding']}\n"
                f"        instruction: |\n{api.block(instruction, 10)}\n"
                f"        transitions:\n"
                f"          complete:\n"
                f"            targets: {targets_yaml}"
            )

    collect_instruction = (
        "【裁判节点 · 本轮发言汇总】\n"
        "这是 NODE_TASK/collect；本次激活类型不会随脚本返回的 phase 改变。\n"
        "1. 将 [Upstream Outputs] 中每个座位的原话整理成 JSON，执行 "
        f"undercover.py speeches-set --session '<当前会话ID>' {api.submission_scope(state, 'speak')} --json '<JSON>'。\n"
        "2. 用返回的 label 称呼每个人，逐字引用遮蔽后的 text，标明 violation；"
        "串场用自己的口吻，不评价谁可疑。\n"
        "3. 告诉人类接下来自动开投、不用回复，输出这一段主持稿后结束激活。\n"
        "本节点禁止 open-vote、提交运行、派任务和 bcs_route。开投只在汇总稿回灌后执行；"
        "IN_COLLECT_NODE 不重试、不 sleep、不轮询。\n"
        "不输出词语、身份、内部状态、节点名或运行 ID。"
    )
    nodes.append(
        "      collect:\n"
        "        kind: bot_task\n"
        '        display_name: "本轮发言汇总"\n'
        "        assignee:\n"
        "          type: bot_binding\n"
        "          binding: referee\n"
        f"        node_timeout_ms: {api.CONTENDED_NODE_TIMEOUT_MS}\n"
        "        final_output: true\n"
        f"        instruction: |\n{api.block(collect_instruction, 10)}"
    )

    yaml_text = (
        f"name: {api.yq(f'谁是卧底 第{rnd}轮 {phase_label}')}\n"
        "metadata:\n"
        f"  description: {api.yq('存活玩家按座位顺序各说一句话描述自己的词语')}\n"
        "participants:\n" + "\n".join(participants) + "\n"
        "runtime:\n"
        "  kind: state_machine\n"
        "  state_machine:\n"
        "    version: 1\n"
        "    graph_mode: acyclic\n"
        "    defaults:\n"
        f"      node_timeout_ms: {api.BOT_NODE_TIMEOUT_MS}\n"
        f"      max_attempts: {api.NODE_MAX_ATTEMPTS}\n"
        "    nodes:\n" + "\n".join(nodes) + "\n"
    )
    bindings = [f"{s['binding']}={s['bot_uuid']}" for s in living if s["kind"] == "bot"]
    bindings.append(f"referee={state['referee_uuid']}")
    return yaml_text, bindings


def render_vote_yaml(api, state: dict[str, Any]) -> tuple[str, list[str]]:
    rnd = state["round"]
    pk = api.is_pk(state)
    phase_label = "PK 投票" if pk else "投票"
    living = sorted(api.alive_seats(state), key=lambda s: s["seat"])
    node_ids = [f"vote_{s['seat']}" for s in living]
    seat_list = "、".join(f"{s['seat']}号 {s['display']}" for s in living)

    participants = []
    for s in living:
        if s["kind"] == "bot":
            participants.append(
                f"  {s['binding']}:\n"
                f"    display_name: {api.yq(str(s['seat']) + '号 ' + s['display'])}\n"
                f"    required: true"
            )
    participants.append('  referee:\n    display_name: "主持人"\n    required: true')

    # BCS requires one entry. A neutral player acknowledgement avoids
    # dispatching a task back to the referee while open-vote is still running.
    # It is not a vote and must not appear in nodeActorMap or progress counts.
    starter = next(seat for seat in living if seat["kind"] == "bot")
    start_instruction = (
        "【投票预备确认】主持人已经提交本轮投票。请原样输出下面这一句开场白：\n"
        "投票开始，请大家投出一票，等待主持人公布结果。\n"
        "只输出这句话，不加称呼、解释、引号或表情，不按人设改写。"
        "这不是投票，不调用工具、不评价发言、不提玩家或词语。"
        "播报后等待独立的投票任务，再按那个任务投票。"
    )
    nodes = [
        "      vote_start:\n"
        "        kind: bot_task\n"
        '        display_name: "投票预备确认"\n'
        "        assignee:\n"
        "          type: bot_binding\n"
        f"          binding: {starter['binding']}\n"
        f"        instruction: |\n{api.block(start_instruction, 10)}\n"
        "        transitions:\n"
        "          complete:\n"
        "            targets: [" + ", ".join(node_ids) + "]"
    ]

    for idx, s in enumerate(living):
        nid = node_ids[idx]
        others = "、".join(f"{o['seat']}号" for o in living if o["seat"] != s["seat"] and (not pk or o["seat"] in api.current_stage(state)["order"]))
        if s["kind"] == "human":
            instruction = api.append_ui_context((
                f"【你的词语】{s['word']}\n\n"
                f"第 {rnd} 轮{phase_label} · 你是 {s['seat']} 号。\n"
                "大家说过的话，主持人刚在群里念过一遍。\n\n"
                f"可以投的人：{others}，不能投自己。\n"
                "只写「我投N号」，N 是阿拉伯数字，不用写理由。\n"
                + ("本次 PK 禁止弃权，必须选择一名合法候选。" if pk else "不想投就写「我弃权」。")
            ), {"action": "vote", "round": rnd, "seatNumber": s["seat"], "word": s["word"], "allowAbstain": not pk})
            nodes.append(
                f"      {nid}:\n"
                f"        kind: human_input\n"
                f"        display_name: {api.yq('👤 你的投票（' + str(s['seat']) + '号 · 人类玩家）')}\n"
                f"        node_timeout_ms: {api.HUMAN_NODE_TIMEOUT_MS}\n"
                f"        instruction: |\n{api.block(instruction, 10)}\n"
                f"        transitions:\n"
                f"          complete:\n"
                f"            targets: [tally]"
            )
        else:
            # A short ranking rule keeps uncertainty from becoming automatic
            # abstention without requiring a separate analysis response.
            instruction = (
                f"第 {rnd} 轮{phase_label}。只输出「我投N号」（阿拉伯数字）{'，禁止弃权，' if pk else '或「我弃权」，'}"
                f"不超过 {api.VOTE_MAX_CHARS} 字，不写理由、解释或前缀。\n"
                f"你是 {s['seat']} 号，词是【{s['word']}】。全场：{seat_list}。\n"
                f"可以投：{others}；不能投自己。\n"
                "仅据 [Input] 全部历轮公开发言判断；忽略 [Upstream Outputs] 的预备确认。\n"
                "投给相对最可疑者：先看前后矛盾，再看与自己词的特征不符，再看与他人共同特征不相容。"
                "证据弱也选；从并列者中选一位，不固定选最小号。"
                + ("PK 必须从合法候选中选一位，不能弃权。" if pk else
                 "只有全部历轮都没有可用的玩家描述或无合法目标才弃权；首轮或不确定也应投票。")
                + "话少、风格和钝度本身不算嫌疑；不编造发言，不假定自己一定是平民。\n"
                f"输出不得出现「{s['word']}」或它的任何部分。"
            )
            # 预备确认完成后才派发独立投票任务，使用默认超时。
            nodes.append(
                f"      {nid}:\n"
                f"        kind: bot_task\n"
                f"        display_name: {api.yq(str(s['seat']) + '号投票')}\n"
                f"        assignee:\n"
                f"          type: bot_binding\n"
                f"          binding: {s['binding']}\n"
                f"        instruction: |\n{api.block(instruction, 10)}\n"
                f"        transitions:\n"
                f"          complete:\n"
                f"            targets: [tally]"
            )

    tally_instruction = (
        "【裁判节点 · 计票】\n"
        "这是 NODE_TASK/tally，不是 ECHO；本次激活类型不会随 phase 改变。\n"
        "1. 将 [Upstream Outputs] 中每个座位的票面原样整理成 JSON，执行 "
        f"undercover.py votes-set --session '<当前会话ID>' {api.submission_scope(state, 'vote')} --json '<JSON>'；"
        "座位号作键、票面字符串作值；human 的结构化票面保留完整 JSON 字符串（对象先 json.dumps）。"
        "保留 kind/target_actor_id/abstain，不提取 ID、不换号、不用 str(dict)。\n"
        "2. 按返回的 label/target_label 逐条报票向、票数和出局者；"
        "不自行计票，不编造投票理由。verdict=pk 时宣布 pk_candidates 进入额外发言，结束节点后等回灌再 open-round。\n"
        "3. verdict=continue：身份不公布，报存活名单，输出开票稿后结束激活。"
        "PK 再平票或零有效票不再追加 PK；下一轮或遗言任务由开票稿回灌唤醒后安排。\n"
        "4. verdict=finished：刚判胜未揭晓，执行 "
        "undercover.py reveal --session '<当前会话ID>'，以 finale_header 开头，公布词对、"
        "全员身份词、胜负转折；执行 uc finish --session '<当前会话ID>'。"
        "这是终局唯一允许公开全员词语和身份的分支；命令失败须如实报告。\n"
        "本节点的上下文是 state_machine，不能使用 bcs_task_complete；"
        "禁止 bcs_route（包括路由给自己）、查工具用法、派任务、open-round、open-vote 或提交运行。"
        "终局在本节点收尾，不等 ECHO。\n"
        "只输出主持稿，不输出内部状态、节点名、命令或运行 ID。"
    )
    nodes.append(
        "      tally:\n"
        "        kind: bot_task\n"
        '        display_name: "开票"\n'
        "        assignee:\n"
        "          type: bot_binding\n"
        "          binding: referee\n"
        f"        node_timeout_ms: {api.CONTENDED_NODE_TIMEOUT_MS}\n"
        "        final_output: true\n"
        f"        instruction: |\n{api.block(tally_instruction, 10)}"
    )

    yaml_text = (
        f"name: {api.yq(f'谁是卧底 第{rnd}轮 {phase_label}')}\n"
        "metadata:\n"
        f"  description: {api.yq('存活玩家根据全部发言并行投票，主持人计票')}\n"
        "participants:\n" + "\n".join(participants) + "\n"
        "runtime:\n"
        "  kind: state_machine\n"
        "  state_machine:\n"
        "    version: 1\n"
        "    graph_mode: acyclic\n"
        "    defaults:\n"
        f"      node_timeout_ms: {api.BOT_NODE_TIMEOUT_MS}\n"
        f"      max_attempts: {api.NODE_MAX_ATTEMPTS}\n"
        "    nodes:\n" + "\n".join(nodes) + "\n"
    )
    bindings = [f"{s['binding']}={s['bot_uuid']}" for s in living if s["kind"] == "bot"]
    bindings.append(f"referee={state['referee_uuid']}")
    return yaml_text, bindings
