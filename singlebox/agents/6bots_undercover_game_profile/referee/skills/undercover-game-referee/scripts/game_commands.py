"""Referee command handlers; dependencies supplied by the CLI entrypoint."""
from __future__ import annotations

import argparse
import json
import random
import shlex
import time
from typing import Any

__all__ = ['cmd_init', 'cmd_status', 'prepare_speak_run', 'cmd_render_speak_run', 'cmd_speeches_set', 'prepare_vote_run', 'cmd_render_vote_run', 'cmd_votes_set', 'cmd_render_ping', 'cmd_my_word', 'cmd_mask', 'cmd_begin', 'cmd_open_round', 'cmd_open_vote', 'cmd_publish_result', 'cmd_finish', 'cmd_reveal', 'cmd_parse_vote']

def cmd_init(api, args: argparse.Namespace) -> None:
    referee_uuid = args.referee_uuid
    with api.locked(args.session):
        if api.state_path(args.session).exists() and not args.force:
            api.die("ALREADY_STARTED", "这一局已经开过了。想重开请加 --force。")

        bots = []
        for item in args.bot:
            if "=" not in item:
                api.die("BAD_BOT", f"--bot 要写成 名称=UUID，收到的是：{item}")
            name, uuid = item.split("=", 1)
            bots.append((name.strip(), uuid.strip()))
        if not bots:
            api.die("NO_BOTS", "至少要有一个 Bot 玩家。")

        human, roster = api.session_roster(args.session, referee_uuid)
        if human is None or not human["present"] or human["actor_id"] != args.human:
            api.die("ROSTER_MISMATCH", "发牌人类必须是当前会话中已 Present 的人类成员。停止，不发牌。")
        if len(bots) != len(roster) or {uuid for _, uuid in bots} != {uuid for _, uuid in roster}:
            api.die("ROSTER_MISMATCH", "玩家名单必须与当前 session 的 worker ID 完全一致，且不包含主持人或重复 ID。停止，重新核对 begin。")
        bots = roster
        api.require_run_slot(args.session)

        cfg = dict(api.DEFAULT_CONFIG)
        cfg["difficulty"] = args.difficulty
        cfg["undercover"] = args.undercover
        cfg["max_rounds"] = args.max_rounds
        cfg["players"] = len(bots) + 1
        if cfg["undercover"] >= cfg["players"]:
            api.die("BAD_CONFIG", "卧底人数必须小于总人数。")

        rng = random.Random(args.seed) if args.seed is not None else random.SystemRandom()

        civ, und = rng.choice(api.load_word_bank(args.difficulty))
        if rng.randint(0, 1):
            civ, und = und, civ

        order = [(name, uuid) for name, uuid in bots] + [("你", args.human)]
        rng.shuffle(order)

        seats = []
        for idx, (who, actor_id) in enumerate(order, start=1):
            if actor_id == args.human:
                seats.append(
                    {
                        "seat": idx,
                        "kind": "human",
                        "display": "你",
                        "bot_name": None,
                        "bot_uuid": None,
                        "binding": None,
                        "word": "",
                        "role": "civilian",
                        "alive": True,
                        "eliminated_round": None,
                    }
                )
            else:
                seats.append(
                    {
                        "seat": idx,
                        "kind": "bot",
                        "display": who[2:] if who.startswith("玩家") else who,
                        "bot_name": who,
                        "bot_uuid": actor_id,
                        "binding": f"seat{idx}",
                        "word": "",
                        "role": "civilian",
                        "alive": True,
                        "eliminated_round": None,
                    }
                )

        spy_seats = rng.sample([s["seat"] for s in seats], cfg["undercover"])
        for s in seats:
            spy = s["seat"] in spy_seats
            s["role"] = "undercover" if spy else "civilian"
            s["word"] = und if spy else civ

        state = {
            "version": api.STATE_VERSION,
            "session_id": args.session,
            "group_id": args.group,
            "human_actor_id": args.human,
            "referee_uuid": referee_uuid,
            "phase": "AWAIT_START",
            "config": cfg,
            "words": {"civilian": civ, "undercover": und},
            "seats": seats,
            "round": 0,
            "rounds": [],
            "pending_ping": None,
            "result": None,
        }
        api.save_state(state)

    api.emit(
        {
            "phase": "AWAIT_START",
            "players": cfg["players"],
            "undercover_count": cfg["undercover"],
            "difficulty": cfg["difficulty"],
            "max_rounds": cfg["max_rounds"],
            "seating": [
                {"seat": s["seat"], "player": s["display"], "kind": s["kind"]} for s in seats
            ],
            "human_seat": next(s["seat"] for s in seats if s["kind"] == "human"),
            "human_word": next(s["word"] for s in seats if s["kind"] == "human"),
            "referee_uuid": referee_uuid,
            "next_action": api.NEXT_ACTION["AWAIT_START"],
            "note": "human_word 是**人类玩家自己**那个词，也是全场唯一一个可以说出口的词——"
            "群聊只有他看得到。发牌时在群里把它念给他（「你是 N 号，你的词是【X】」），"
            "他忘了随时可以用 my-word 再问一次。别的座位的词一个字都不在这里。",
        }
    )


def cmd_status(api, args: argparse.Namespace) -> None:
    # 「被唤醒第一件事是 status」这条纪律，在 session 刚启动时本来是必然报错的：
    # 那时状态文件还不存在，load_state 直接退 2。第一条指令被规定成一条必然失败
    # 的命令，模型就会转去自己摸索——开局那两次参数写错正是这么来的。
    if not api.state_path(args.session).exists():
        api.emit(
            {
                "phase": "NO_GAME",
                "next_action": "这一局还没开牌。先跑 begin 做开局探测（--group 可以省略）。",
                "command": f'uc begin --session {shlex.quote(args.session)} --referee-uuid "<本次 GroupContext.recipient>"',
            },
            compact=True,
        )
        return
    result_action = api.recovery().inspect(args.session, getattr(args, "failed_run", None))
    state = api.load_state(args.session)
    ping = state["pending_ping"]
    brief = {
        "phase": state["phase"],
        "referee_uuid": state["referee_uuid"],
        "recovery": api._recovery.safe_status(state),
        "run_result_action": result_action,
        "round": f"{state['round']}/{state['config']['max_rounds']}",
        "alive": [f"{s['seat']}号{s['display']}" for s in api.alive_seats(state)],
        # 只有「有 Bot 出局、要念遗言」才报出来。平票那种为了叫醒裁判而派的
        # 「预备任务」已经不派了：它是一次完整的 bot 激活加两次裁判激活，换回来
        # 的只是一句「我准备好了」，而每一次 bcs_assign_task 都会让 BCS 往裁判自
        # 己的会话里灌一条 [任务状态]，那条灌入会打断裁判当时正在跑的激活。
        # 平票那一轮改成：开票稿回灌把裁判叫醒，它直接 open-round。
        "pending_ping": (ping or {}).get("bot_name") if (ping or {}).get("kind") == "eulogy" else None,
        "next_action": api.NEXT_ACTION.get(state["phase"], "先跑 status"),
    }
    if result_action:
        brief.update(api._recovery.guidance(args.session, result_action))
    if state["phase"] == "FINISHED":
        if state.get("public_result") and not state.get("revealed_at"):
            brief["next_action"] = (
                "终局结果已生成，但尚未确认发布及揭晓。报告发布失败；维护者确认修复后，"
                "核对当前会话，重试 reveal --session；成功后按返回主持稿，再 finish --session。"
            )
        # 一个新会话永远不该看到 FINISHED——它自己的状态文件还不存在，只会是 NO_GAME。
        brief["note"] = (
            "如果你是刚被一个新会话叫醒的，那是认错局了：--session 传的是上一局的 ID。"
            "同一个协作群里每个会话是独立的一局。"
        )
    if not args.full:
        api.emit(brief, compact=True)
        return
    api.emit(
        {
            **brief,
            "round_no": state["round"],
            "max_rounds": state["config"]["max_rounds"],
            "eliminated": [
                {"seat": s["seat"], "player": s["display"], "round": s["eliminated_round"]}
                for s in state["seats"]
                if not s["alive"]
            ],
            "human_seat": next(s["seat"] for s in state["seats"] if s["kind"] == "human"),
            "pending_ping_detail": ping,
            "renders": (state["rounds"][-1].get("renders", {}) if state["rounds"] else {}),
            "result": state["result"],
        }
    )


def prepare_speak_run(api, session_id: str, retry: bool) -> dict[str, Any]:
    with api.locked(session_id):
        state = api.load_state(session_id)
        pk = state["phase"] in ("AWAIT_PK_SPEAK_START", "PK_SPEAK_RUNNING")
        if retry:
            # 重开当前阶段；open-* 已核实失败 run 并持久化恢复次数。
            # 轮次不推进、本轮记录不重建，只把同一份 YAML 重新渲染一次。
            api.require_phase(state, "SPEAK_RUNNING", "PK_SPEAK_RUNNING")
            if api.current_stage(state)["speeches"]:
                api.die(
                    "ALREADY_SPOKEN",
                    "本轮发言已经收齐落盘了，不能重开发言运行。"
                    "如果卡住的是投票，用 render-vote-run --retry。",
                )
        elif pk:
            api.require_phase(state, "AWAIT_PK_SPEAK_START")
        else:
            api.require_phase(state, "AWAIT_START", "AWAIT_NEXT_ROUND")
            state["round"] += 1
            state["pending_ping"] = None
            state["rounds"].append(
                {
                    "round": state["round"],
                    "order": [
                        s["seat"] for s in sorted(api.alive_seats(state), key=lambda s: s["seat"])
                    ],
                    "speeches": {},
                    "votes": {},
                    "counts": {},
                    "eliminated": None,
                    "tie": False,
                    "renders": {},
                }
            )
        living = sorted(api.alive_seats(state), key=lambda s: s["seat"])
        state["phase"] = "PK_SPEAK_RUNNING" if pk else "SPEAK_RUNNING"
        attempt = api.bump_render(state, "speak")
        yaml_text, bindings = api.render_speak_yaml(state)
        announcement = api.opening_announcement(state, "speak", attempt)
        run_input = {
            "opening": announcement,
            "game": "谁是卧底",
            "round": state["round"],
            "alive": [f"{s['seat']}号 {s['display']}" for s in living],
            "history": api.public_history(state),
            "rule": (
                f"用一句话（不超过{api.SPEECH_MAX_CHARS}字）描述你的词语，不得说出词本身，也不得拆开说；"
                f"本轮的描述要钝到至少还能套在 {api.bluntness_n(state['round'])} 样别的东西上"
            ),
        }
        vote_history = api.public_vote_history(state)
        if vote_history:
            run_input["voteHistory"] = vote_history
        if pk:
            run_input["rule"] += "；本次是 PK，可基于公开发言和已公布票面辩解，不暴露私有信息"
        panel_params = api.public_panel_projection(state, "speak", attempt)
        panel_tab = api.panel_tab_metadata(state, "speak", attempt)
        yaml_path, input_path, panel_params_path = api.write_run_files(
            session_id, api.run_file_kind("pk-speak" if pk else "speak", state["round"], attempt), yaml_text, run_input, panel_params
        )
        api.save_state(state)

    return {
        "phase": state["phase"],
        "round": state["round"],
        "attempt": attempt,
        "announcement": announcement,
        "yaml_path": yaml_path,
        "input_path": input_path,
        "panel_params_path": panel_params_path,
        "panel_tab": panel_tab,
        "bindings": bindings,
        "binding_args": " ".join(f'--binding "{b}"' for b in bindings),
        "run_command": api.run_command(session_id, yaml_path, input_path, panel_params_path, panel_tab, bindings),
    }


def cmd_render_speak_run(api, args: argparse.Namespace) -> None:
    payload = api.prepare_speak_run(args.session, args.retry)
    payload.pop("bindings", None)
    payload["note"] = (
        "YAML 里含词语，不要读它、不要贴它。提交成功后只播报 announcement 并结束。"
    )
    api.emit(payload, compact=True)


def cmd_speeches_set(api, args: argparse.Namespace) -> None:
    payload = json.loads(args.json)
    flags = {}
    for item in args.flag or []:
        seat, _, reason = item.partition("=")
        flags[int(seat)] = reason
    with api.locked(args.session):
        state = api.load_state(args.session)
        api.require_phase(state, "SPEAK_RUNNING", "PK_SPEAK_RUNNING")
        api.require_submission(state, args, "speak")
        rnd = api.current_stage(state)
        expected = set(rnd["order"])
        got = {int(k) for k in payload}
        if got != expected:
            api.die(
                "SPEECH_SET_MISMATCH",
                f"本轮应该有 {sorted(expected)} 号的发言，收到的是 {sorted(got)}。"
                "请把每个座位的原话都取全再提交。",
            )
        results = []
        for seat in rnd["order"]:
            raw = str(payload[str(seat)] if str(seat) in payload else payload[seat]).strip()
            display, reason = api.check_text(state, seat, raw, api.SPEECH_MAX_CHARS)
            if seat in flags and not reason:
                reason = flags[seat]
            rnd["speeches"][str(seat)] = {"raw": raw, "display": display, "violation": reason}
            results.append(
                {
                    "seat": seat,
                    "player": api.seat_of(state, seat)["display"],
                    "label": api.label_of(state, seat),
                    "kind": api.seat_of(state, seat)["kind"],
                    "text": display,
                    "violation": reason,
                }
            )
        state["phase"] = "AWAIT_PK_VOTE_START" if api.is_pk(state) else "AWAIT_VOTE_START"
        api.save_state(state)

    api.emit(
        {
            "phase": state["phase"],
            "round": state["round"],
            "speeches": results,
            # 这条命令只可能在「本轮发言汇总」节点里跑，所以 next_action 不用
            # NEXT_ACTION 的通用文案——那句「直接开投」在这个节点里是有害的。
            "next_action": "念完汇总稿就**结束激活**。你此刻在发言运行的最后一个节点里、"
            "占着协作槽位，在这里跑 open-vote 一定失败，重试会卡死整局。",
            "note": "只使用 text 字段念稿，永远不要使用原始文本。"
            "每位玩家用 label 原样称呼（名字带号数）。"
            "念完不要再等人类说话——这段稿子发出去之后会把你自己叫醒一次，"
            "**开投是那一次激活的事，不是这一次**。",
        }
    )


def prepare_vote_run(api, session_id: str, retry: bool) -> dict[str, Any]:
    with api.locked(session_id):
        state = api.load_state(session_id)
        if retry:
            # 恢复前已核验服务端失败；本地仍保留当前投票阶段。
            # 允许按 SX 重开当前尝试，同时保留常规/PK 阶段边界。
            api.require_phase(state, "VOTE_RUNNING", "PK_VOTE_RUNNING")
        else:
            api.require_phase(state, "AWAIT_VOTE_START", "AWAIT_PK_VOTE_START")
        state["phase"] = "PK_VOTE_RUNNING" if api.is_pk(state) else "VOTE_RUNNING"
        attempt = api.bump_render(state, "vote")
        yaml_text, bindings = api.render_vote_yaml(state)
        living = sorted(api.alive_seats(state), key=lambda s: s["seat"])
        run_input = {
            "game": "谁是卧底",
            "round": state["round"],
            "phase": "投票",
            "opening": api.opening_announcement(state, "vote", attempt),
            "alive": [f"{s['seat']}号 {s['display']}" for s in living],
            "history": api.public_history(state),
            "rule": "只根据所有人历史全部发言，投出你认为词语和大家不一样的人；不能投自己；只交票号，不写理由",
        }
        vote_history = api.public_vote_history(state)
        if vote_history:
            run_input["voteHistory"] = vote_history
        if api.is_pk(state):
            run_input["phase"] = "PK 投票"
            run_input["rule"] += "；仅投本次 PK 玩家，禁止弃权"
        panel_params = api.public_panel_projection(state, "vote", attempt)
        api.current_stage(state)["result_file"] = panel_params["resultFile"]
        panel_tab = api.panel_tab_metadata(state, "vote", attempt)
        yaml_path, input_path, panel_params_path = api.write_run_files(
            session_id, api.run_file_kind("pk-vote" if api.is_pk(state) else "vote", state["round"], attempt), yaml_text, run_input, panel_params
        )
        api.save_state(state)

    return {
        "phase": state["phase"],
        "round": state["round"],
        "attempt": attempt,
        "yaml_path": yaml_path,
        "input_path": input_path,
        "panel_params_path": panel_params_path,
        "panel_tab": panel_tab,
        "bindings": bindings,
        "binding_args": " ".join(f'--binding "{b}"' for b in bindings),
        "run_command": api.run_command(session_id, yaml_path, input_path, panel_params_path, panel_tab, bindings),
    }


def cmd_render_vote_run(api, args: argparse.Namespace) -> None:
    payload = api.prepare_vote_run(args.session, args.retry)
    payload.pop("bindings", None)
    payload["note"] = (
        "YAML 里含词语，不要读它、不要贴它。开投提示由副屏显示，提交完结束激活。"
        + ("" if payload["attempt"] <= 1 else " 这是本轮第 %d 次开投，副屏将显示之前的票作废。" % payload["attempt"])
    )
    api.emit(payload, compact=True)


def cmd_votes_set(api, args: argparse.Namespace) -> None:
    payload = json.loads(args.json)
    with api.locked(args.session):
        state = api.load_state(args.session)
        api.require_phase(state, "VOTE_RUNNING", "PK_VOTE_RUNNING")
        api.require_submission(state, args, "vote")
        pk = api.is_pk(state)
        rnd = api.current_stage(state)
        voters = api.current_round(state)["order"]
        expected = set(voters)
        got = {int(k) for k in payload}
        if got != expected:
            api.die(
                "VOTE_SET_MISMATCH",
                f"本轮应该有 {sorted(expected)} 号的投票，收到的是 {sorted(got)}。"
                "请把每个座位的原话都取全再提交。",
            )

        results = []
        counts: dict[int, int] = {}
        for seat in voters:
            raw = str(payload[str(seat)] if str(seat) in payload else payload[seat]).strip()
            _masked, reason = api.check_text(state, seat, raw, api.VOTE_MAX_CHARS)
            target, note = api.parse_vote(state, seat, raw)
            if reason and reason != f"超过 {api.VOTE_MAX_CHARS} 字":
                target, note = None, "投票内容违规，本票作废"
            # 票面一律规范化成票号，玩家写了什么原话都不往外传。
            #
            # 投票理由是泄露渠道——一条理由就是「拿我的词比对他的话」的结果，念出来
            # 等于广播自己那个词的属性。节点指令里已经要求只交票号，但指令是软的：
            # 只要有一个玩家多写了半句，主持稿就会把它念出去。所以在这里把渠道彻底
            # 关死——主持人拿不到原话，也就没得念、没得猜。
            if target is not None:
                display = f"我投{target}号"
            elif note == "弃权":
                display = "我弃权"
            else:
                display = "无效票"
            rnd["votes"][str(seat)] = {
                "raw": raw,
                "display": display,
                "target": target,
                "violation": reason,
                "note": note,
            }
            if target is not None:
                counts[target] = counts.get(target, 0) + 1
            results.append(
                {
                    "seat": seat,
                    "player": api.seat_of(state, seat)["display"],
                    "label": api.label_of(state, seat),
                    "text": display,
                    "target_seat": target,
                    "target_player": api.seat_of(state, target)["display"] if target else None,
                    "target_label": api.label_of(state, target) if target else None,
                    "violation": reason,
                    "note": note,
                }
            )

        top = max(counts.values()) if counts else 0
        candidates = sorted(s for s, c in counts.items() if c == top) if top else []
        tie = len(candidates) != 1
        eliminated = None if tie else candidates[0]

        if eliminated is not None:
            s = api.seat_of(state, eliminated)
            s["alive"] = False
            s["eliminated_round"] = state["round"]
        rnd["counts"] = {str(k): v for k, v in counts.items()}
        rnd["eliminated"] = eliminated
        rnd["tie"] = tie
        rnd["tallied"] = True
        if pk:
            api.current_round(state)["eliminated"] = eliminated

        spies_alive = [s for s in api.alive_seats(state) if s["role"] == "undercover"]
        survivors = api.alive_seats(state)
        if len(candidates) > 1 and not pk:
            rnd["pk"] = {"order": candidates, "speeches": {}, "votes": {}, "counts": {},
                         "tie": False, "eliminated": None, "renders": {}}
            verdict, winner, reason = "pk", None, "最高票并列，进入 PK"
        elif not spies_alive:
            verdict, winner, reason = "finished", "civilian", "卧底已经全部出局"
        elif len(survivors) <= 2:
            verdict, winner, reason = "finished", "undercover", "场上只剩两个人，卧底还在"
        elif state["round"] >= state["config"]["max_rounds"]:
            verdict, winner, reason = "finished", "undercover", "轮数用完了还没抓到卧底"
        else:
            verdict, winner, reason = "continue", None, ""

        ping = None
        if verdict == "pk":
            state["pending_ping"] = None
            state["phase"] = "AWAIT_PK_SPEAK_START"
        elif verdict == "continue":
            ping = api.choose_ping(state, eliminated)
            state["pending_ping"] = ping
            state["phase"] = "AWAIT_NEXT_ROUND"
        else:
            state["result"] = {"winner": winner, "reason": reason, "round": state["round"]}
            state["phase"] = "FINISHED"
        api.save_state(state)

    api.emit(
        {
            "phase": state["phase"],
            "round": state["round"],
            "votes": results,
            "counts": [
                {
                    "seat": s,
                    "player": api.seat_of(state, s)["display"],
                    "label": api.label_of(state, s),
                    "votes": c,
                }
                for s, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "tie": tie,
            "pk_candidates": candidates if verdict == "pk" else [],
            "eliminated": (
                {
                    "seat": eliminated,
                    "player": api.seat_of(state, eliminated)["display"],
                    "label": api.label_of(state, eliminated),
                }
                if eliminated
                else None
            ),
            "alive": [
                {"seat": s["seat"], "player": s["display"], "label": api.label_of(state, s["seat"])}
                for s in api.alive_seats(state)
            ],
            "verdict": verdict,
            "winner": winner,
            "win_reason": reason,
            "ping": ping,
            "next_action": (
                "本次刚判胜，真相尚未公布。保持在当前计票节点：先 reveal --session '<当前会话ID>'，"
                "公布终局稿，再执行 uc finish --session '<当前会话ID>'。"
                "不要等待 ECHO，不调用 bcs_task_complete 或 bcs_route。"
                if verdict == "finished"
                else "报 PK 名单和开票稿后结束当前节点。回灌后 open-round 开始本轮 PK 发言。"
                if verdict == "pk"
                else (
                    "念开票稿后结束当前节点。回灌后 render-ping 派遗言，回执后开下一轮。"
                    if ping and ping["kind"] == "eulogy"
                    else "念开票稿后结束当前节点。回灌后直接 open-round，不派预备任务。"
                )
            ),
            "note": "只使用 text 字段念稿——它已经规范化成票号，玩家的原话不会给你。"
            "每位玩家用 label / target_label 原样称呼（名字带号数）。"
            "不要替玩家编造或猜测投票理由。出局者身份不要公布，除非 verdict 是 finished。"
            "verdict=pk 时只公布 PK 名单，不淘汰；PK 计票仍平票或零有效票时本轮无人出局，不追加 PK。"
            "仅 verdict=continue 且 ping 为空、或 verdict=continue 且 kind 是 standby 时，**这一轮不派任何任务**："
            "开票稿会把你自己叫醒一次，那一拍直接 open-round。",
        }
    )


def cmd_render_ping(api, args: argparse.Namespace) -> None:
    state = api.load_state(args.session)
    ping = state["pending_ping"]
    if not ping:
        api.die("NO_PING", "当前没有待发的遗言或预备任务。")
    if ping["kind"] == "eulogy":
        # 遗言是整局唯一一条只流向人类玩家的线索通道：Bot 收不到群广播，也看不到
        # 别人的任务回执，所以出局者的推理只会落到人类眼里，不会污染场上任何一个
        # Bot 的判断。以前这条任务只说「可以喊冤也可以放狠话」，配 20 字上限，交
        # 回来的就是「我跟你讲，这票投错人了」——一句纯情绪。现在给素材、规定形状。
        #
        # 泄词这条边界要靠措辞守：**不能把它自己的词写进这条任务正文**（forbid_line
        # 会把词原样拼出来，而这段文字要经过裁判的输出通道流进群里，人类看到出局者
        # 的词就等于知道了他是不是卧底）。所以只能用不点名的写法，再靠 mask 兜底。
        living = api.alive_seats(state)
        example_seat = living[0]["seat"] if living else ping["seat"]
        message = (
            f"你在第 {state['round']} 轮被投出局了。按规矩你可以留一句遗言，主持人会念给全场听。\n\n"
            "到目前为止全场说过的话：\n"
            f"{api.history_block(state)}\n\n"
            f"请说一句遗言，不超过 {api.EULOGY_MAX_CHARS} 个字，语气还是你自己的性格，"
            "但必须有内容：挑一个你最怀疑的座位号，说清是他哪句话让你在意——"
            "把那句话里的关键几个字点出来。\n"
            # 例子里的号数取一个还活着的座位，免得刚好指到收信人自己头上。
            f"例：「我最不放心 {example_seat} 号，『一只手拿着刚好』那句，跟前面几位不是一个路子。」\n\n"
            "三条硬规矩：\n"
            "1. 不许说出你自己那个词，也不许说它里面连续的两个字、或者把它拆开说全；"
            "不许用拼音、英文或谐音去指它。\n"
            "2. 不许说自己是不是卧底，也不许拿自己的词去和别人比——"
            "「跟我理解的不一样」「我的那个不是这样」这类话一个字都不能出现。"
            "你只能说别人的话和别人的话之间对不上。\n"
            "3. 只输出这一句遗言，不要解释、不要前缀。"
        )
    else:
        message = (
            f"第 {state['round']} 轮结束了，本轮没有人出局。\n"
            "下一轮由你第一个发言。收到请只回一句不超过 15 个字的话，表示你准备好了，"
            "符合你自己的性格。不要提词语、身份或任何推理。"
        )
    api.emit(
        {
            "kind": ping["kind"],
            "seat": ping["seat"],
            "target_bot": ping["bot_name"],
            "player": ping["player"],
            "label": api.label_of(state, ping["seat"]),
            "message": message,
            "note": "用 bcs_assign_task 把 message 原样发给 target_bot；它的回执会把你叫醒开下一轮。"
            + (
                "遗言回执拿到之后先过一道遮蔽再念："
                f"mask --seat {ping['seat']} --text '它的原话'，只念返回的 text。"
                "遗言里的怀疑是出局者的个人看法，转述就行，不要附和、不要评价。"
                if ping["kind"] == "eulogy"
                else ""
            ),
        }
    )


def cmd_my_word(api, args: argparse.Namespace) -> None:
    """人类玩家忘了自己的词。

    群聊是裁判和人类的私密双人频道，Bot 一个字都看不到，所以这条随时可以答，
    次数不限。给的永远只有人类自己那一个词。
    """
    state = api.load_state(args.session)
    human = next((s for s in state["seats"] if s["kind"] == "human"), None)
    if human is None:
        api.die("NO_HUMAN", "这一局没有人类玩家。")
    api.emit(
        {
            "phase": state["phase"],
            "human_seat": human["seat"],
            "human_word": human["word"],
            "alive": human["alive"],
            "note": "只说给人类玩家一个人听：「你是 N 号，你的词是【X】」。"
            "别的座位的词不在这里，也不要去猜。",
        },
        compact=True,
    )


def cmd_mask(api, args: argparse.Namespace) -> None:
    """把一句自由文本过一道泄词遮蔽。

    发言和投票都走协作节点，裁判在人类看到之前就能遮蔽；遗言不走节点，是公开的
    任务回执，以前这条路上没有任何机器兜底（文档里那句「唯一拦不住的是遗言」就
    是指它）。这条命令把同一套判定接到遗言上。
    """
    state = api.load_state(args.session)
    text, reason = api.check_text(state, args.seat, args.text, args.max_chars)
    api.emit(
        {
            "seat": args.seat,
            "label": api.label_of(state, args.seat),
            "text": text,
            "violation": reason,
            "note": "只念 text（遮蔽后的版本），永远不要念原话。violation 非空就顺口点一句踩线了。",
        },
        compact=True,
    )


def cmd_begin(api, args: argparse.Namespace) -> None:
    """开局前的全部探测一次做完：人类在不在、有哪些 Bot、我自己是谁、init 怎么写。

    这一步以前要模型自己拼三条 jq、还要去猜自己的 UUID，每一条都要一个来回，每个
    来回都会在群里留下一段状态旁白。现在一条命令给完。
    """
    referee_uuid = args.referee_uuid
    human, bots = api.session_roster(args.session, referee_uuid)

    if human is None:
        api.emit(
            {
                "human_present": False,
                "reason": "这个会话里还没有人类参与者。",
                "next_action": "说「加入提示」那段，结束激活。人类加入后会发消息过来，届时重跑 begin。",
            },
            compact=True,
        )
        return
    if not human["present"]:
        api.emit(
            {
                "human_present": False,
                "human_actor_id": human["actor_id"],
                "reason": "人类在会话里但没有加入（mode 不是 present），含用户输入节点的运行会被拒。",
                "next_action": "说「加入提示」那段，结束激活。他加入后会发消息过来，届时重跑 begin。",
            },
            compact=True,
        )
        return

    init_cmd = " ".join(
        [
            "uc init",
            "--referee-uuid", shlex.quote(referee_uuid),
            "--session", shlex.quote(args.session),
            "--group", shlex.quote(args.group),
            "--human", shlex.quote(human["actor_id"]),
        ]
        + [x for name, uuid in bots for x in ("--bot", shlex.quote(f"{name}={uuid}"))]
        + ["--difficulty", args.difficulty, "--undercover", str(args.undercover),
           "--max-rounds", str(args.max_rounds)]
    )
    api.emit(
        {
            "human_present": True,
            "human_actor_id": human["actor_id"],
            "bots": [name for name, _ in bots],
            "referee_uuid": referee_uuid,
            "init_command": init_cmd,
            "next_action": "说开场白，请人类回一句「开始」。**这一步不 init**——他可能还想先问问规则。",
        },
        compact=True,
    )


def cmd_open_round(api, args: argparse.Namespace) -> None:
    api.recovery().open(args, "speak")


def cmd_open_vote(api, args: argparse.Namespace) -> None:
    api.recovery().open(args, "vote")


def cmd_publish_result(api, args: argparse.Namespace) -> None:
    with api.locked(args.session):
        state = api.load_state(args.session)
        if state["phase"] != "FINISHED" or not state.get("public_result"):
            api.die("NOT_REVEALED", "尚未生成公开终局结果，请先执行 reveal。")
        api.publish_public_result(state)
    api.emit({"ok": True, "published": True, "file_id": state["result_file_id"]})


def cmd_finish(api, args: argparse.Namespace) -> None:
    with api.locked(args.session):
        state = api.load_state(args.session)
        if state["phase"] != "FINISHED":
            api.die("NOT_FINISHED", "本局还没结束，不能关闭会话。")
        if not state.get("revealed_at"):
            api.die("NOT_REVEALED", "本局尚未执行 reveal；先按终局步骤公布答案，再 finish。")
        if state.get("public_result"):
            api.publish_public_result(state)
    # The server accepts repeated completion, including after a lost response.
    # Keep the reveal record intact so closing can be retried independently.
    code, out, err = api.bcs_cli("--json", "session", "complete", args.session)
    if code != 0 or api.last_json(out) is None:
        api.die(
            "FINISH_FAILED",
            f"关闭会话失败（exit={code}）：{(err or out)[:300]}。"
            "本局结果已公布，但尚未确认 BCS 会话关闭。请维护者排障后仅重试 finish --session，勿重复 reveal。",
        )
    api.emit({"ok": True, "session_id": args.session, "completed": True})


def cmd_reveal(api, args: argparse.Namespace) -> None:
    # 真相只许公布一次。
    #
    # 这是「认错局」那条故障链上的最后一道闸：主持人在一个新会话里读到上一局的
    # FINISHED，接着 reveal，把上一局的词和身份念进了一个还没发牌的会话。会话解析
    # 那一层已经堵死了这条路，但公布答案是不可撤销的动作，值得再拦一道。
    with api.locked(args.session):
        state = api.load_state(args.session)
        if state["phase"] != "FINISHED":
            api.die("NOT_FINISHED", "本局还没结束，现在不能公布真相。")
        if state.get("revealed_at"):
            api.die(
                "ALREADY_REVEALED",
                f"这一局的真相在 {state['revealed_at']} 已经公布过一次了，不会再公布第二次。"
                "如果你是刚被一个新会话叫醒的，那说明认错局了——新的一局要先 begin。"
                "如果终局稿已经发出去了，就别再发一遍。",
            )
        if api.current_stage(state).get("result_file"):
            if not state.get("public_result"):
                state["public_result"] = api.build_public_result(state)
                api.save_state(state)
            api.publish_public_result(state)
        state["revealed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        api.save_state(state)
    api.emit(
        {
            "winner": state["result"]["winner"],
            "win_reason": state["result"]["reason"],
            "finale_header": "游戏结束！\n" + ("平民胜利！" if state["result"]["winner"] == "civilian" else "卧底胜利！"),
            "words": state["words"],
            "seats": [
                {
                    "seat": s["seat"],
                    "player": s["display"],
                    "role": s["role"],
                    "word": s["word"],
                    "eliminated_round": s["eliminated_round"],
                }
                for s in sorted(state["seats"], key=lambda x: x["seat"])
            ],
            "public_history": api.public_history(state),
            "vote_history": api.public_vote_history(state),
            "rounds": [
                {
                    "round": r["round"],
                    "speeches": {k: v["display"] for k, v in r["speeches"].items()},
                    "votes": {k: v["target"] for k, v in r["votes"].items()},
                    "eliminated": r["eliminated"],
                    "tie": r["tie"],
                    **({"pk": {"candidates": r["pk"]["order"], "speeches": {k: v["display"] for k, v in r["pk"]["speeches"].items()},
                               "votes": {k: v["target"] for k, v in r["pk"]["votes"].items()}, "tie": r["pk"]["tie"]}} if r.get("pk") else {}),
                }
                for r in state["rounds"]
            ],
        }
    )


def cmd_parse_vote(api, args: argparse.Namespace) -> None:
    state = api.load_state(args.session)
    target, note = api.parse_vote(state, args.voter, args.text)
    api.emit(
        {
            "target_seat": target,
            "target_player": api.seat_of(state, target)["display"] if target else None,
            "note": note,
        }
    )
