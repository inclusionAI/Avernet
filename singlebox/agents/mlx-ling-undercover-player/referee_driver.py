"""「谁是卧底」脚本裁判（确定性阶段机）—— Avernet #2611 混桌对局的主持人侧驱动。

为什么是脚本而不是 LLM 裁判：Ling-3.0-tiny 撑不住 uc 的多步工具协议——
实测出现 NO_REPLY 控制文本塌缩、'[assistant turn failed before producing
content]' 失败占位符被模型回声进历史、以及 6 分钟 reasoning 停滞被 OpenClaw
停滞检测 abort（证据留在 Avernet PR 的对话记录里）。而 #2611 的验收口径本身
就是「主持人脚本」。本文件把 referee SKILL.md 的 S0–S5/SX 阶段机逐条实现成
代码，事实层仍然完全复用同一套 uc 脚本（undercover.py，含全 Bot 对局补丁）。

唤醒分类（params.session_context / 文本）：
- 含 [State Machine Task]  → NODE_TASK：collect 念发言汇总（S2）、tally 开票（S4）
- is_sender / from_bot_id=自己 → ECHO：自己 node 产物经 web_send 回灌（不含发送者
  排除），按 phase 走 S3 开投 / S4b 派遗言 / S4c 直接开下一轮
- 含 [任务状态]/[协同提醒]   → 账本回灌：不推进阶段（遗言回执走消息路径）
- 其余                       → 玩家/人类消息：遗言回执、'卡住了'（SX）、开始

关键协议事实（读 BCS 源码确认，别改错）：
- 节点产物（final_output）经 web_send 发布，route_with_overlay(sender=None)——
  **不排除发送者**，所以主持人会收到自己主持稿的回灌（ECHO 唤醒）。
- 普通 chat 回复经 relay_final_chat_event，sender 被排除——**回复不会回声**，
  不存在回复循环；空内容 final 会被 'bot relay skipped: empty message text'
  丢弃——静默回执统一返回 ""。
- manager_worker 群的 worker 收不到任何广播，玩家上下文只来自节点任务的
  [Input] / [Upstream Outputs]。
- '末节点只念稿，下一步等回灌'：collect/tally 激活里绝不 open-*；回灌唤醒
  物理上必然晚于运行收尾（web_send 在运行完成后发布），时序天然安全。
"""

import argparse
import asyncio
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

from bcn_client import BcnClient

log = logging.getLogger("referee")

RUNNING_PHASES = ("SPEAK_RUNNING", "VOTE_RUNNING", "PK_SPEAK_RUNNING", "PK_VOTE_RUNNING")

VIOLATION_WORDS = ("卧底", "平民", "身份")


def parse_json_doc(text):
    """uc 的 emit 有的是单行（compact=True）有的是多行缩进（默认），都只写一个
    JSON 文档到 stdout。优先整篇解析；失败再从 '{' 起找，优先带 ok 键的那个。"""
    stripped = (text or "").strip()
    if not stripped:
        return None
    try:
        obj = json.loads(stripped)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    payload = None
    for m in re.finditer(r"\{", stripped):
        try:
            obj, _ = json.JSONDecoder().raw_decode(stripped[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "ok" in obj:
            return obj
        if payload is None and isinstance(obj, dict):
            payload = obj
    return payload


class Uc:
    """uc 事实层子进程封装：stdout 一律是单行 JSON（emit/die 同格式）。"""

    def __init__(self, script, bcs_cli, env_extra=None):
        self.script = str(script)
        self.bcs_cli = str(bcs_cli)
        self.env_extra = env_extra or {}

    async def run(self, *args, timeout=120):
        """返回 (payload dict, ok bool)。die() 退出码 2 且 ok=False。"""
        cmd = [sys.executable, self.script, *args]
        env = dict(os.environ)
        env["PATH"] = os.path.dirname(self.bcs_cli) + os.pathsep + env.get("PATH", "")
        env.update(self.env_extra)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return {"ok": False, "error": "TIMEOUT", "message": f"uc {' '.join(args)} 超时"}, False
        text = out.decode()
        payload = parse_json_doc(text)
        if payload is None:
            payload = {"ok": False, "error": "BAD_OUTPUT",
                       "message": (text + "\n" + err.decode())[-500:]}
        return payload, bool(payload.get("ok"))

    async def status(self, session):
        payload, _ = await self.run("status", "--session", session)
        return payload


def extract_scope(text):
    """节点正文里渲染好的 --round/--stage/--attempt（写入命令必须原样带上）。"""
    m = re.search(r"--round (\d+) --stage (\w+) --attempt (\d+)", text)
    if m:
        return ["--round", m.group(1), "--stage", m.group(2), "--attempt", m.group(3)]
    return []


def parse_upstream(text, kind):
    """[Upstream Outputs] 段 → [(seat:int, 原话)]。kind: 'speak' | 'vote'。"""
    m = re.search(r"\[Upstream Outputs\](.*?)(?:\n\[Instruction\]|\Z)", text, re.S)
    if not m:
        return []
    out = []
    for sm in re.finditer(rf"\[({kind}_(\d+))\]\s*\n(.*?)(?=\n\[|\Z)", m.group(1), re.S):
        out.append((int(sm.group(2)), sm.group(3).strip()))
    return sorted(out)


def raw_message_text(text):
    """去掉 BCS v2 群上下文头，只留消息正文（ctx.message 不总在，双保险）。"""
    if "[消息内容]\n" in text:
        text = text.split("[消息内容]\n", 1)[1]
    return re.sub(r"^\[from:[^\]]*\]", "", text).strip()


class GameReferee:
    """一局的阶段机。所有唤醒串行处理（一把锁），动作只调 uc 子进程。"""

    def __init__(self, client, uc, session_id, group_id, config, log_file=None,
                 srv_log=None, srv_quiet=600):
        self.client = client
        self.uc = uc
        self.session_id = session_id
        self.group_id = group_id
        self.config = config  # {"difficulty","undercover","max_rounds"}
        self.srv_log = srv_log        # 模型服务日志（现场评估的判活证人）
        self.srv_quiet = srv_quiet    # 服务静默超过该秒数才认定链子真死
        self.lock = asyncio.Lock()
        self.init_command = None
        self.ping = None           # render-ping 的输出（kind/seat/target_bot/message…）
        self.ping_dispatched = False
        self.eulogy_text = None
        self.eulogy_done = False
        self.finished = asyncio.Event()
        self.game_over = False
        self.sx_round = {}         # round -> 已重开次数（同一轮最多两次）
        self._watchdog = None
        self.log_file = log_file

    # ---------- 日志 ----------

    def _log(self, **kw):
        kw["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        kw["session"] = self.session_id
        log.info("game-log %s", json.dumps(kw, ensure_ascii=False)[:400])
        if self.log_file:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(kw, ensure_ascii=False) + "\n")

    # ---------- 看门狗（无人值守兜底：节点运行死了不会唤醒裁判） ----------

    def _arm_watchdog(self, seconds=3600):
        if self._watchdog:
            self._watchdog.cancel()
        self._watchdog = asyncio.get_running_loop().call_later(
            seconds, lambda: asyncio.create_task(self._watchdog_fire()))

    async def _watchdog_fire(self):
        if self.game_over or self.finished.is_set():
            return
        self._log(wake="WATCHDOG")
        async with self.lock:
            try:
                await self.on_phase_wake("WATCHDOG", "")
            except Exception:
                log.exception("watchdog recovery failed")

    # ---------- 唤醒入口 ----------

    async def on_task(self, session_key, text, params=None):
        params = params or {}
        sid = params.get("bcs_session_id") or session_key
        if sid != self.session_id:  # 上一局/别处的迟到消息
            return ""
        async with self.lock:
            self._arm_watchdog()
            try:
                return await self.dispatch(text, params)
            except Exception as e:
                log.exception("dispatch failed")
                self._log(wake="ERROR", error=str(e)[:300])
                return ""

    async def dispatch(self, text, params):
        ctx = params.get("session_context") or {}
        if "[State Machine Task]" in text:
            m = re.search(r"node_id:\s*(\S+)", text)
            node = m.group(1) if m else ""
            if node == "collect":
                return await self.s2_collect(text)
            if node == "tally":
                return await self.s4_tally(text)
            self._log(wake="NODE_TASK", node=node, action="ignore")
            return ""  # 其它节点不该派给裁判
        if ctx.get("is_sender") or ctx.get("from_bot_id") == self.client.bot_uuid:
            return await self.on_phase_wake("ECHO", text)
        if "[任务状态]" in text or "[协同提醒]" in text:
            return await self.on_ledger(text)
        return await self.on_message(text, ctx)

    async def on_phase_wake(self, kind, text):
        st = await self.uc.status(self.session_id)
        phase = st.get("phase", "?")
        self._log(wake=kind, phase=phase, pending_ping=st.get("pending_ping"))
        if phase == "NO_GAME":
            return await self.s0_begin(start_requested=True)
        if phase == "AWAIT_START":
            return await self.s1_deal_and_open()
        if phase == "AWAIT_VOTE_START":
            return await self.s3_open_vote()
        if phase == "AWAIT_PK_SPEAK_START":
            return await self.s_open_round()
        if phase == "AWAIT_PK_VOTE_START":
            return await self.s3_open_vote()
        if phase == "AWAIT_NEXT_ROUND":
            if st.get("pending_ping"):
                if not self.ping_dispatched:
                    return await self.s4b_dispatch_ping()
                if kind == "WATCHDOG":
                    # 遗言回执迟迟不来（玩家空回复/任务失败/运行死了）：不再等，
                    # 不带遗言直接开下一轮，别让整局吊死在一条回执上
                    return await self.s5_read_eulogy_and_open()
                return ""  # 等遗言回执（消息路径 / 账本兜底）
            return await self.s_open_round()
        if phase == "FINISHED":
            return "本局已结束，新建会话再来一局。"
        # *_RUNNING：绝不推进；'卡住了' 走 SX。看门狗到达这里先做现场评估：
        # 肥上下文串行链（后期提示词 15k-50k token、服务一次一个请求）可以
        # 几十分钟不产生任何唤醒但活着——模型服务日志还在滚动就说明链子在磨，
        # 此时重开会作废健康进度、白烧每轮两次的重开预算。服务也静默（三方
        # 判活的全桌心跳）才是真死，才重开。
        if "卡住了" in text:
            return await self.sx_recover(phase, st)
        if kind == "WATCHDOG":
            if not self._chain_dead():
                self._log(wake="WATCHDOG_DEFERRED", phase=phase,
                          reason="model-server busy; chain alive, not recovering")
                self._arm_watchdog(600)  # 十分钟后再查，不烧重开预算
                return ""
            return await self.sx_recover(phase, st)
        return ""

    def _chain_dead(self):
        """现场评估：模型服务最近一次「真实工作」超过 srv_quiet 秒才认定链子真死。
        只认 ≥1000 token 的预填（游戏提示词都是 10k+），43-token 的外部探针/
        保活不算心跳——2026-11-12 踩过：垃圾流量把假链子焐活了 48 分钟。
        没配 --srv-log 时退回旧行为（看门狗一到就认定死亡）。"""
        if not self.srv_log:
            return True
        try:
            with open(self.srv_log, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 65536))
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            return True
        last = None
        for m in re.finditer(
                r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*progress: \d+/(\d{4,})",
                tail, re.M):
            last = m.group(1)
        if not last:
            return True
        try:
            t = time.mktime(time.strptime(last, "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            return True
        return (time.time() - t) > self.srv_quiet

    async def on_message(self, text, ctx):
        st = await self.uc.status(self.session_id)
        phase = st.get("phase", "?")
        self._log(wake="MSG", phase=phase, sender=str(ctx.get("from"))[:60],
                  text=raw_message_text(text)[:120])
        body = raw_message_text(text)
        if phase == "NO_GAME":
            # 无人值守：任何唤醒都当作「开始」，不靠魔法词防死锁
            return await self.s0_begin(start_requested=True)
        if phase == "AWAIT_START":
            return await self.s1_deal_and_open()
        if phase == "AWAIT_NEXT_ROUND" and self.ping and self.ping_dispatched and not self.eulogy_done:
            # 遗言回执（S5）：唯一会在这个 phase 到达的玩家消息
            self.eulogy_text = body
            return await self.s5_read_eulogy_and_open()
        if "卡住了" in body:
            return await self.sx_recover(phase, st)
        # 其余消息一律静默：节点运行期间玩家的节点产物可能被转成消息送达，
        # 不该逐条回话（空 final 是合法的静默回执）
        return ""

    async def on_ledger(self, text):
        st = await self.uc.status(self.session_id)
        phase = st.get("phase", "?")
        self._log(wake="LEDGER", phase=phase, text=raw_message_text(text)[:120])
        # 遗言回执的账本回灌：若消息路径还没到但文本已缓冲，直接收尾
        if (phase == "AWAIT_NEXT_ROUND" and self.ping and self.ping_dispatched
                and not self.eulogy_done and self.eulogy_text
                and ("已回复" in text or "失败" in text or "超时" in text or "已中断" in text)):
            return await self.s5_read_eulogy_and_open()
        return ""  # 账本从不推进阶段

    # ---------- S0–S5 / SX ----------

    async def s0_begin(self, start_requested):
        out, ok = await self.uc.run(
            "begin", "--session", self.session_id, "--referee-uuid", self.client.bot_uuid,
            "--difficulty", self.config["difficulty"],
            "--undercover", str(self.config["undercover"]),
            "--max-rounds", str(self.config["max_rounds"]))
        self._log(step="S0 begin", ok=ok, out={k: out.get(k) for k in
                   ("human_present", "all_bot_table", "bots", "error", "message")})
        if not ok:
            return f"开局探测失败：{out.get('error')} {out.get('message', '')}"
        self.init_command = out.get("init_command")
        if out.get("all_bot_table"):
            if start_requested:
                return await self.s1_deal_and_open()
            return ("欢迎来到「谁是卧底」全 Bot 对局。卧底 1 名，词语难度中等，最多 6 轮；"
                    "每轮公开发言后自动开投，出局者可留遗言。回一句「开始」即发牌开局。")
        return ("本桌需要人类玩家先加入当前会话。加入后回一句「开始」，我来发牌。")

    async def s1_deal_and_open(self):
        if not self.init_command:
            st = await self.uc.status(self.session_id)
            if st.get("phase") != "AWAIT_START":
                return ""
            return "发牌命令还没就绪，请回一句「开始」重试。"
        # 跑 begin 给的 init_command（把开头的 uc 换成本地脚本）
        argv = shlex.split(self.init_command)
        argv[0] = self.uc.script
        cmd = [sys.executable, *argv]
        env = dict(os.environ)
        env["PATH"] = os.path.dirname(self.uc.bcs_cli) + os.pathsep + env.get("PATH", "")
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env)
        out_b, err_b = await proc.communicate()
        init_out = parse_json_doc(out_b.decode())
        self._log(step="S1 init", rc=proc.returncode,
                  out={k: (init_out or {}).get(k) for k in ("ok", "error", "message", "seating")})
        if proc.returncode != 0 or not (init_out or {}).get("ok"):
            if (init_out or {}).get("error") == "ALREADY_STARTED":
                # 上一次发牌已经落盘（比如播报中断）：别重发牌，直接开第一轮
                open_out = await self.s_open_round()
                return ("牌已发过，直接开第一轮。\n\n" + open_out) if open_out else ""
            msg = (init_out or {}).get("message") or err_b.decode()[-200:]
            return f"发牌失败：{msg}"
        seating = "、".join(f"{s['seat']}号 {s['player']}" for s in init_out.get("seating", []))
        cfg = init_out
        open_out = await self.s_open_round()
        header = (f"游戏开始！本局 {cfg.get('players')} 人，卧底 {cfg.get('undercover_count')} 名，"
                  f"难度 {cfg.get('difficulty')}，最多 {cfg.get('max_rounds')} 轮。\n座位：{seating}。\n"
                  "每轮公开发言后自动开投，出局者可留遗言。玩家发言超时或卡住请回一句「卡住了」。\n\n")
        return header + open_out

    async def s2_collect(self, text):
        scope = extract_scope(text)
        speeches = parse_upstream(text, "speak")
        payload = {str(seat): raw for seat, raw in speeches}
        flags = []
        for seat, raw in speeches:
            if any(w in raw for w in VIOLATION_WORDS):
                flags += ["--flag", f"{seat}=谈身份"]
        args = ["speeches-set", "--session", self.session_id, *scope,
                "--json", json.dumps(payload, ensure_ascii=False), *flags]
        out, ok = await self.uc.run(*args)
        self._log(step="S2 collect", ok=ok, scope=scope, n_speeches=len(speeches),
                  out={k: out.get(k) for k in ("phase", "error", "message")})
        if not ok:
            return f"发言汇总提交失败（{out.get('error')}）：{out.get('message', '')}"
        lines = ["【发言转述】"]
        for sp in out.get("speeches", []):
            line = f"{sp['label']}：「{sp['text']}」"
            if sp.get("violation"):
                line += f"（违规：{sp['violation']}）"
            lines.append(line)
        lines.append("以上为本轮公开发言。接下来自动开投，大家稍等片刻。")
        return "\n".join(lines)

    async def s3_open_vote(self):
        out, ok = await self.uc.run("open-vote", "--session", self.session_id)
        self._log(step="S3 open-vote", ok=ok,
                  out={k: out.get(k) for k in ("phase", "attempt", "error", "message")})
        if not ok:
            if out.get("error") == "IN_COLLECT_NODE":
                return ""  # 一次都不要重试，立刻结束激活
            return f"开投暂未成功：{out.get('message', out.get('error'))}"
        return ""  # 提交即授权开投，按返回提示立即结束、不播报

    async def s4_tally(self, text):
        scope = extract_scope(text)
        votes = parse_upstream(text, "vote")
        payload = {str(seat): raw for seat, raw in votes}
        args = ["votes-set", "--session", self.session_id, *scope,
                "--json", json.dumps(payload, ensure_ascii=False)]
        out, ok = await self.uc.run(*args)
        self._log(step="S4 votes-set", ok=ok, scope=scope, n_votes=len(votes),
                  out={k: out.get(k) for k in ("phase", "verdict", "error", "message")})
        if not ok:
            return f"计票提交失败（{out.get('error')}）：{out.get('message', '')}"
        verdict = out.get("verdict")
        lines = ["【开票结果】"]
        for v in out.get("votes", []):
            if v.get("target_label"):
                lines.append(f"{v['label']} → {v['target_label']}")
            else:
                lines.append(f"{v['label']}：{v['text']}")
        if out.get("counts"):
            lines.append("票数：" + "、".join(f"{c['label']}：{c['votes']}票" for c in out["counts"]))
        if verdict == "pk":
            labels = []
            for seat in out.get("pk_candidates", []):
                for v in out.get("votes", []):
                    if v.get("seat") == seat:
                        labels.append(v["label"])
            lines.append(f"最高票并列，进入 PK！PK 名单：{'、'.join(labels)}。不淘汰，追加发言后重新投票。")
            return "\n".join(lines)
        if verdict == "continue":
            if out.get("eliminated"):
                e = out["eliminated"]
                lines.append(f"本轮出局的是 {e['label']}，身份暂不公布。")
            else:
                lines.append("本轮无人出局。" + ("（平票或零有效票，不再追加 PK）" if out.get("tie") else ""))
            alive = "、".join(a["label"] for a in out.get("alive", []))
            lines.append(f"存活：{alive}。")
            return "\n".join(lines)
        if verdict == "finished":
            return await self.s_finish(out)
        return "\n".join(lines)

    async def s_finish(self, tally_out):
        """S4 finished 分支：唯一允许 reveal + finish 的地方。"""
        rev, ok = await self.uc.run("reveal", "--session", self.session_id)
        self._log(step="S4 reveal", ok=ok, out={k: rev.get(k) for k in ("winner", "win_reason", "error")})
        if not ok:
            return f"揭晓失败：{rev.get('error')} {rev.get('message', '')}"
        words = rev.get("words", {})
        lines = [rev.get("finale_header", "游戏结束！"),
                 f"词对：平民词「{words.get('civilian')}」／卧底词「{words.get('undercover')}」",
                 f"胜负：{rev.get('win_reason', '')}", "全员身份："]
        for s in rev.get("seats", []):
            role = "平民" if s.get("role") == "civilian" else "卧底"
            lines.append(f"{s['seat']}号 {s['player']} — {role}（{s.get('word')}）"
                         + (f"，第 {s['eliminated_round']} 轮出局" if s.get("eliminated_round") else ""))
        # finish 会 session complete 关掉 BCS 会话；终局稿是这次唤醒的回复，
        # 在 on_task 返回后才作为 final 送达，所以 finish 必须等回复落地之后再跑。
        async def _finish_later():
            for _ in range(2):
                await asyncio.sleep(5)
                fin, ok2 = await self.uc.run("finish", "--session", self.session_id)
                self._log(step="S4 finish", ok=ok2,
                          out={k: fin.get(k) for k in ("ok", "error", "message")})
                if ok2:
                    break
            self.game_over = True
            self.finished.set()
        asyncio.create_task(_finish_later())
        return "\n".join(lines)

    async def s4b_dispatch_ping(self):
        out, ok = await self.uc.run("render-ping", "--session", self.session_id)
        self._log(step="S4b render-ping", ok=ok,
                  out={k: out.get(k) for k in ("kind", "seat", "target_bot", "player", "error")})
        if not ok:
            return ""
        self.ping = out
        self.eulogy_text = None
        self.eulogy_done = False  # 新一次遗言任务，收尾标记要复位（一局可多次出局）
        try:
            resp = await self.client.rpc("task.dispatch", {
                "group_id": self.group_id,
                # session_id 必填：不带时服务端消息存储事务报
                # "transaction parameter 3 references missing row 0 from step 1"
                # （会话消息序号行按 session_id 查，查不到就整笔崩），带上即好。
                "session_id": self.session_id,
                "target_bot": out["target_bot"],
                "message": out["message"]})
            self._log(step="S4b task.dispatch", target=out["target_bot"],
                      task_id=str(resp.get("task_id"))[:40])
        except Exception as e:
            self._log(step="S4b task.dispatch", error=str(e)[:300])
            # 兜底：遗言派不出去不该把整局吊死——跳过遗言直接开下一轮
            #（与 WATCHDOG 分支同款策略），遗言只是锦上添花。
            return await self.s5_read_eulogy_and_open()
        self.ping_dispatched = True
        return ""  # 派完结束激活，不念稿

    async def s5_read_eulogy_and_open(self):
        lines = []
        if self.eulogy_text and self.ping:
            masked, ok = await self.uc.run(
                "mask", "--session", self.session_id,
                "--seat", str(self.ping.get("seat")), "--text", self.eulogy_text)
            self._log(step="S5 mask", ok=ok)
            spoken = masked.get("text", self.eulogy_text) if ok else self.eulogy_text
            lines.append(f"上轮出局的 {self.ping.get('label', '')} 留下遗言：「{spoken}」")
        self.eulogy_done = True
        self.ping = None
        self.ping_dispatched = False
        self.eulogy_text = None
        open_out = await self.s_open_round()
        if open_out:
            lines.append(open_out)
        return "\n\n".join(lines)

    async def s_open_round(self):
        out, ok = await self.uc.run("open-round", "--session", self.session_id)
        self._log(step="open-round", ok=ok,
                  out={k: out.get(k) for k in ("phase", "round", "attempt", "run_id", "error", "message")})
        if not ok:
            if out.get("error") == "IN_TALLY_NODE":
                return ""
            return f"开新一轮暂未成功：{out.get('message', out.get('error'))}"
        return out.get("announcement", "")

    async def sx_recover(self, phase, st):
        """卡住诊断：按 phase 尝试重开当前轮（同一轮最多两次）。"""
        rnd = str(st.get("round", ""))
        used = self.sx_round.get(rnd, 0)
        if used >= 2:
            return "同一轮已重开过两次仍未恢复，本局只能作废，请新建会话重开。"
        self.sx_round[rnd] = used + 1
        if phase in ("SPEAK_RUNNING", "PK_SPEAK_RUNNING"):
            out, ok = await self.uc.run("open-round", "--session", self.session_id, "--retry")
        elif phase in ("VOTE_RUNNING", "PK_VOTE_RUNNING"):
            out, ok = await self.uc.run("open-vote", "--session", self.session_id, "--retry")
        elif phase in ("AWAIT_VOTE_START", "AWAIT_PK_VOTE_START"):
            out, ok = await self.uc.run("open-vote", "--session", self.session_id)
        else:
            return await self.on_phase_wake("SX", "")
        self._log(step="SX recover", phase=phase, attempt=used + 1, ok=ok,
                  out={k: out.get(k) for k in ("submitted", "run_id", "error", "message")})
        if not ok:
            return f"卡住诊断：重开失败（{out.get('error')}）：{out.get('message', '')}"
        return f"收到。已重新发起本轮（第 {used + 1} 次重开），旧发言/旧票作废。"


# ---------------------------------------------------------------------------
# 编排：连接 → 建桌 → 逐局开打
# ---------------------------------------------------------------------------

class Driver:
    def __init__(self, args):
        self.args = args
        self.uc = Uc(args.uc, args.bcs_cli)
        self.client = None
        self.group_id = None
        self.connected = asyncio.Event()
        self.players = dict(p.split("=", 1) for p in args.player)  # name -> uuid

    async def on_connected(self, client):
        proc = await asyncio.create_subprocess_exec(
            self.args.bcs_cli, "onboard",
            "--name", "谁是卧底主持人脚本",
            "--summary", "谁是卧底全 Bot 桌的脚本裁判（SKILL.md 阶段机 S0-S5 的确定性实现；uc 事实层主持）。",
            "--skills", "undercover-referee-scripted,phase-machine,format-safe-tally",
            "--domains", "undercover-game,social-deduction,orchestration",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "BCN_BOT_TOKEN": client.token or ""},
        )
        out, _ = await proc.communicate()
        log.info("onboard rc=%s: %s", proc.returncode, out.decode()[:200])
        if client.token:
            with open(os.path.expanduser("~/.mlx-referee.token"), "w") as f:
                f.write(client.token)
        self.connected.set()

    async def bcs(self, *args):
        proc = await asyncio.create_subprocess_exec(
            self.args.bcs_cli, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env={**os.environ, "BCN_BOT_TOKEN": self.client.token or ""})
        out, _ = await proc.communicate()
        text = out.decode()
        return proc.returncode, parse_json_doc(text), text

    async def create_group(self):
        args = [
            "create-group",
            "--manager", self.client.bot_uuid,
            "--participants", ",".join(self.players.values()),
            "--topic", "谁是卧底",
            "--context", ("谁是卧底 · 6人局（全 Bot 对局，无人类玩家）"
                          "· 卧底1名 · 词语难度中等 · 最多6轮"),
        ]
        rc, payload, text = await self.bcs(*args, "--no-session")
        if rc != 0:
            rc, payload, text = await self.bcs(*args)  # 服务端不支持 --no-session 时退回默认
        gid = (payload or {}).get("id") or (payload or {}).get("group_id")
        if not gid:
            m = re.search(r"bcs_grp_[0-9a-f]+", text)
            gid = m.group(0) if m else None
        if not gid:
            raise RuntimeError(f"create-group failed rc={rc}: {text[:300]}")
        self.group_id = gid
        log.info("group created: %s", gid)

    async def create_session(self, title, kickoff="开始"):
        """建会话并顺手把开场输入灌进去：input 会作为 <GroupContext> 消息以
        chat.send 派给 driver（group_context_delivery 默认 send），就是天然的
        自启动唤醒，不依赖任何人再来说话。"""
        rc, payload, text = await self.bcs(
            "session", "create", "--group", self.group_id, "--title", title,
            "--input", json.dumps(kickoff, ensure_ascii=False))
        sid = None
        if isinstance(payload, dict):
            for key in ("id", "session_id", "bcs_session_id"):
                if payload.get(key):
                    sid = payload[key]
                    break
            if not sid:
                def find(o):
                    if isinstance(o, dict):
                        for k, v in o.items():
                            if k in ("id", "session_id") and isinstance(v, str) and ":" in v:
                                return v
                            r = find(v)
                            if r:
                                return r
                    elif isinstance(o, list):
                        for v in o:
                            r = find(v)
                            if r:
                                return r
                    return None
                sid = find(payload)
        if not sid:
            m = re.search(rf"{re.escape(self.group_id)}:[0-9a-f]{{8}}", text)
            sid = m.group(0) if m else None
        if not sid:
            raise RuntimeError(f"session create failed rc={rc}: {text[:300]}")
        log.info("session created: %s", sid)
        return sid

    async def run(self):
        self.current_ref = None
        self.client = BcnClient(f"ws://127.0.0.1:{self.args.bcs_port}/ws/bot",
                                on_task=self._route, on_connected=self.on_connected)
        runner = asyncio.create_task(self.client.run_forever())
        await asyncio.wait_for(self.connected.wait(), 30)
        await self.create_group()

        for game in range(1, self.args.games + 1):
            sid = await self.create_session(f"谁是卧底 第{game}局")
            log_dir = Path(self.args.log_dir)
            log_dir.mkdir(parents=True, exist_ok=True)
            ref = GameReferee(self.client, self.uc, sid, self.group_id,
                              {"difficulty": self.args.difficulty,
                               "undercover": self.args.undercover,
                               "max_rounds": self.args.max_rounds},
                              log_file=str(log_dir / f"game-{game:02d}.jsonl"),
                              srv_log=self.args.srv_log,
                              srv_quiet=self.args.srv_quiet)
            self.current_ref = ref  # 开场唤醒可能在 create_session 返回前就到了
            ref._arm_watchdog()
            try:
                await asyncio.wait_for(ref.finished.wait(), timeout=self.args.game_timeout)
                log.info("game %d finished", game)
            except asyncio.TimeoutError:
                # 一局超时只作废本局，不连坐后面的局（否则一个慢局杀掉整个 run）
                log.error("game %d timed out after %ds — 作废本局，继续下一局",
                          game, self.args.game_timeout)
                ref._log(wake="GAME_TIMEOUT",
                         error=f"game_timeout {self.args.game_timeout}s exceeded")
            self.current_ref = None
        runner.cancel()

    async def _route(self, session_key, text, params=None):
        """把唤醒路由到当前局；开场唤醒可能抢在 current_ref 就绪之前，等一小会儿。"""
        for _ in range(100):
            ref = self.current_ref
            if ref is not None:
                return await ref.on_task(session_key, text, params)
            await asyncio.sleep(0.1)
        log.warning("wake dropped (no current game): %.80s", text)
        return ""


def main():
    ap = argparse.ArgumentParser(description="谁是卧底 · 脚本裁判（阶段机驱动）")
    ap.add_argument("--bcs-port", default=os.environ.get("BCS_PORT", "21000"))
    ap.add_argument("--bcs-cli", default="/Users/lipeilin/代码库/python/Avernet/apps/bcs/target/debug/bcs-cli")
    ap.add_argument("--uc", default=("/Users/lipeilin/代码库/python/Avernet/singlebox/agents/"
                                     "6bots_undercover_game_profile/referee/skills/"
                                     "undercover-game-referee/scripts/undercover.py"))
    ap.add_argument("--player", action="append", required=True,
                    help="玩家 名称=UUID，可多次（不含裁判自己）")
    ap.add_argument("--games", type=int, default=1)
    ap.add_argument("--difficulty", default="medium")
    ap.add_argument("--undercover", type=int, default=1)
    ap.add_argument("--max-rounds", type=int, default=6)
    ap.add_argument("--log-dir", default=str(Path(__file__).parent / "validation-logs"))
    ap.add_argument("--game-timeout", type=int, default=4 * 3600)
    ap.add_argument("--srv-log", default=os.environ.get("REF_SRV_LOG"),
                    help="模型服务日志路径；给了之后看门狗重开前先看它是否在滚动"
                         "（现场评估：在磨=链子活着，不重开）")
    ap.add_argument("--srv-quiet", type=int, default=600,
                    help="模型服务日志静默超过该秒数才认定链子真死（默认 600）")
    ap.add_argument("--preflight-url", action="append", default=[],
                    help="开局前模型后端探针（preflight.py）的目标 base_url，可多次；"
                         "任一硬故障直接不开局")
    ap.add_argument("--preflight-adapter", default=None,
                    help="探针的 adapter 目录（与玩家微调档一致时做底座/LoRA 判别）")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    if args.preflight_url:
        import subprocess
        import sys
        pf = Path(__file__).parent / "preflight.py"
        for url in args.preflight_url:
            cmd = [sys.executable, str(pf), "--base-url", url]
            if args.preflight_adapter:
                cmd += ["--adapter-path", args.preflight_adapter]
            log.info("preflight: %s", " ".join(cmd))
            rc = subprocess.call(cmd)
            if rc != 0:
                log.error("preflight failed for %s — 不开局（先修模型服务再跑）", url)
                sys.exit(1)

    try:
        asyncio.run(Driver(args).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
