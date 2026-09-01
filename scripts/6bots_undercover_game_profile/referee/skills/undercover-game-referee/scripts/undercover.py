#!/usr/bin/env python3
"""谁是卧底 · 裁判事实层。

裁判 Bot 的所有游戏状态读写、随机、渲染和判定都通过本脚本完成。
模型只负责把渲染好的内容交给工具，以及用主持人的口吻说话。

两条不变量：
  1. 除 render-* / reveal / my-word 和 init 的 human_word 外，任何子命令的 stdout
     都不包含词语和身份。这样即使把命令输出原样贴进群聊也不会泄密。
     human_word 是唯一的例外，给的只有**人类玩家自己那个词**：群聊是裁判和人类的
     私密双人频道，Bot 一个字都看不到，所以把它念给人类是安全的。
  2. render-speak-run / render-vote-run 把含词的 YAML 写进文件、只打印路径，
     词一次都不经过模型的输出通道。

begin / open-round / open-vote 会自己调 bcs-cli（认证仍由 CLI 负责，本脚本不碰
token）。把探测、渲染、提交合成一条命令不是为了省事：裁判的每一次工具调用和它的
输出都会被转发成群里的事件，来回越多、人类看到的无关文字越多；而且入口节点是裁判
自己的，提交之后每多一个来回，开场就晚一个来回。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shlex
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

STATE_VERSION = 5

PHASES = (
    "AWAIT_START",
    "SPEAK_RUNNING",
    "AWAIT_VOTE_START",
    "VOTE_RUNNING",
    "AWAIT_NEXT_ROUND",
    "FINISHED",
)

NEXT_ACTION = {
    "AWAIT_START": "开第一轮：先把人类自己的词念给他（init 返回的 human_word，或 my-word），再 open-round。",
    "SPEAK_RUNNING": "等发言协作把汇总节点派给你；拿到全部发言后调 speeches-set。",
    "AWAIT_VOTE_START": "**如果这次激活是「本轮发言汇总」节点：念完汇总稿就结束激活，不要在那里开投。**你正占着协作槽位，在那个节点里跑 open-vote 一定失败，重试会卡死整局。其余情况（汇总稿的回灌、人类说话）：直接开投，不用等人类说话，跑 open-vote。",
    "VOTE_RUNNING": "等投票协作把计票节点派给你；计票节点上调 votes-set。运行超时未回就走卡住诊断。",
    "AWAIT_NEXT_ROUND": "pending_ping 非空（有 Bot 出局，要念遗言）：派出遗言任务，"
    "等它的回执把你叫醒，再 open-round。"
    "pending_ping 为空（平票，或出局的是人类）：**不要派任何任务**，直接 open-round——"
    "开票稿的回灌就是这一步的唤醒源，bcs_assign_task 会打断你自己这次激活。",
    "FINISHED": "本局已经结束、真相也公布过了。只说一句「本局已结束，新建会话再来一局」，"
    "**不要再 reveal、不要 bcs_task_complete、不要调任何脚本**。"
    "如果你是刚被一个新会话叫醒的，那说明 --session 传错了：新会话应该看到 NO_GAME。",
}

SPEECH_MAX_CHARS = 25
# 投票只交票号：「我投4号」5 字、「我弃权」3 字。理由是泄露渠道——一条理由本质上
# 是「拿我的词去比对他的话」的结果，念出来就等于广播自己那个词的一个属性。
VOTE_MAX_CHARS = 10
MASK = "○"

# SELF_INJECT_NOTE：`bcs_assign_task` 不是免费的工具调用。
#
# 每派出一条任务，BCS 会立刻往裁判**自己**的会话里回灌一条 `[任务状态]`（裁判是
# 这个主从群的 driver，账本状态只发给它）。那条回灌是直接改会话文件的，会打断裁判
# 当时正在跑的那次激活。任务的**回执**到达时还会再回灌一条。
#
# 2026-08-31 那一局里，5 次激活被打断，4 次是裁判自己派单打断了自己（两次遗言、
# 两次看门狗），第 5 次是看门狗回执打断了 `vote_open` 入口节点——那次整局就死在
# 那里。协作节点的派发不会回灌，只有 `bcs_assign_task` 会。
#
# 由此有两条硬纪律，写进了 phase-machine.md 和 RULES.md：
#   1. `bcs_assign_task` 必须是本次激活的最后一个工具调用；
#   2. **派任务的那一刻，身后不能有节点在排队等裁判让路。** 提交过运行的那次激活
#      里一律不派任务——回执随时会到，撞上入口节点就是整个运行失败。
# 第 2 条把看门狗整个否掉了：它只能在开投提交之后派，而那时 `vote_open` 正排在
# 后面。投票运行失败的兜底因此统一交给人类。

# 节点超时与重试。
#
# Bot 的会话通道是串行的：同一时刻只跑一个激活，其余排队。通道偶尔会在一次激活
# 结束后没有及时释放，节点任务就会一直排在队里不动，直到 Bot 侧的自愈把通道抢
# 回来——那个自愈窗口是分钟级的。所以节点超时必须明显大于它，否则 BCS 先判超时、
# 自愈后到，节点产物变成没人认领的孤儿。
#
# max_attempts 固定为 1：重试对「通道堵住」这类故障毫无作用，只会让同一个节点排
# 两份，排空时还可能逆序执行，在群里留下重复播报和空回复。真正的瞬时失败由 Bot
# 侧自己重试。
BOT_NODE_TIMEOUT_MS = 420_000
# 裁判的通道最挤：人类的自由聊天、节点任务、以及它自己 final_output 的回灌都走
# 这一条，所以派给裁判的节点额外放宽。
CONTENDED_NODE_TIMEOUT_MS = 600_000
# 入口节点是裁判自己的（整局的流程推进只能由主持人做），而运行是在裁判的一次激活
# 里同步提交的：入口节点在提交那一刻就派出去，排在提交它的那次激活后面等自己让路。
# 实测正常交接 3–5 秒；最坏的一次是 Bot 通道在激活结束后泄漏了 6 分钟。15 分钟把
# 两种情况都盖住，也留出裁判自己那条通道被人类插话占用的余量。
ENTRY_NODE_TIMEOUT_MS = 900_000
HUMAN_NODE_TIMEOUT_MS = 900_000
NODE_MAX_ATTEMPTS = 1

# 发言轮收尾和「自动开投」之间只隔几秒：汇总稿回灌唤醒裁判的实测延迟是 3–7 秒，
# 而那时发言运行可能刚好还没释放协作槽位。与其让裁判自己「等几秒再试一次」——每
# 试一次就是一个来回、一段群里的无关文字——不如在脚本里退避重试。
RUN_SLOT_RETRIES = 3
RUN_SLOT_WAIT_S = 4

# 遗言的字数上限。20 字只够喊一句冤，装不下「我怀疑几号 + 他哪句话不对」，
# 而遗言是整局唯一一条只流向人类玩家的线索通道，值得给足位置。
EULOGY_MAX_CHARS = 35

DEFAULT_CONFIG = {
    "players": 6,
    "undercover": 1,
    "difficulty": "medium",
    "max_rounds": 6,
    "reveal_role": False,
}


# --------------------------------------------------------------------------
# 路径与原子读写
# --------------------------------------------------------------------------

def game_dir() -> Path:
    base = os.environ.get("BOT_DATA_DIR") or os.environ.get("OPENCLAW_DATA_DIR")
    if not base:
        base = str(Path.home() / ".openclaw")
    d = Path(base) / "undercover-game"
    d.mkdir(parents=True, exist_ok=True)
    return d


def skill_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def state_path(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
    return game_dir() / f"{safe}.json"


def work_dir(session_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
    d = game_dir() / "work" / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


# 一局的入口和出口。这几条命令**永远不许**去猜会话：
#
#   一个协作群可以连着开好几个会话，每个会话是独立的一局，而状态文件是按 session
#   落盘的。新会话刚建起来时它还没有自己的状态文件，这时磁盘上「唯一那一局」恰恰
#   是上一局——2026-08-28 那次就是这么炸的：主持人在新会话里跑了一条不带 --session
#   的 status，认到上一局的状态、读到 FINISHED，接着 reveal 把上一局的词和身份念进
#   了新会话，最后还调 bcs_task_complete 把这个刚建的会话关掉了。
#
# 认错局的代价在这几条上也最大：status 让主持人以为本局已结束，reveal / my-word 直接
# 把答案念出来，begin / init 会往错的地方写。所以它们必须显式给 --session。
SESSION_REQUIRED_COMMANDS = ("status", "begin", "init", "reveal", "my-word")


def known_states() -> list[tuple[str, str, Path]]:
    """(session_id, phase, 状态文件)，最近改动的排在前面。

    文件名是把 session_id 里的 ':' 换掉之后的安全名，反推不回来，所以真正的
    session_id 从文件内容里读。
    """
    out: list[tuple[str, str, Path]] = []
    for f in game_dir().glob("*.json"):
        if f.name.startswith("."):
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sid = str(data.get("session_id") or "")
        if sid:
            out.append((sid, str(data.get("phase") or ""), f))
    out.sort(key=lambda item: item[2].stat().st_mtime, reverse=True)
    return out


def session_from_env() -> str | None:
    """运行时如果把会话 ID 放进了环境变量，那是权威来源。"""
    for key in ("BCS_SESSION_ID", "BCS_GROUP_SESSION_ID", "OPENCLAW_SESSION_ID", "BCN_SESSION_ID"):
        v = (os.environ.get(key) or "").strip()
        # 会话 ID 形如 bcs_grp_xxxx:yyyy；不带冒号的多半是群号或别的东西，宁可不要。
        if v and ":" in v:
            return v
    return None


def resolve_session(explicit: str | None, command: str) -> str:
    """把 --session 解析出来。

    分两档，因为「省掉参数」的价值和「认错局」的代价在不同命令上完全不一样：

    - 入口/出口命令（见 SESSION_REQUIRED_COMMANDS）：必须显式给。它们是新会话里
      第一条会被调用的命令，而新会话恰恰是最容易认错局的时刻。
    - 中途的命令（open-*、*-set、render-*、mask…）：可以省。它们不可能是一个新会话
      的第一条命令，而且只会落到**本机唯一一局还没结束的游戏**上；有两局同时在跑
      就报错让调用方说清楚。这一档保住的是每轮两三个来回——提交类命令必须是激活的
      最后一个动作，在它前面插一次参数报错，本轮开场就晚一个来回。
    """
    if explicit and explicit.strip():
        return explicit.strip()
    env = session_from_env()
    if env:
        return env
    if command in SESSION_REQUIRED_COMMANDS:
        die(
            "NO_SESSION",
            f"`{command}` 必须显式给 --session：它是一局的入口/出口，认错局的代价最大。"
            "会话 ID 在把你叫醒的 GroupContext 里，形如 bcs_grp_xxxx:yyyy——从那里抄，"
            "不要从别处猜，也不要沿用上一局的。",
            command=f'uc {command} --session "<GroupContext 里的会话 ID>"',
        )
    live = [(sid, phase) for sid, phase, _ in known_states() if phase != "FINISHED"]
    if len(live) == 1:
        return live[0][0]
    if len(live) > 1:
        die(
            "AMBIGUOUS_SESSION",
            "这台机器上不止一局还在进行中，认不出你说的是哪一局。"
            "把 GroupContext 里的会话 ID 用 --session 传进来。",
            sessions=[sid for sid, _ in live],
        )
    die(
        "NO_SESSION",
        "没给 --session，而且本机没有正在进行中的局。"
        "把 GroupContext 里那个形如 bcs_grp_xxxx:yyyy 的会话 ID 用 --session 传进来。",
    )
    raise AssertionError("unreachable")


def resolve_group(explicit: str | None, session_id: str) -> str:
    """--group 也变成可选的：会话 ID 的冒号前半段就是群号。"""
    if explicit and explicit.strip():
        return explicit.strip()
    head = session_id.split(":", 1)[0].strip()
    if head:
        return head
    die("NO_GROUP", f"从会话 ID「{session_id}」里推不出群号，请用 --group 显式给一个。")
    raise AssertionError("unreachable")


class JsonArgumentParser(argparse.ArgumentParser):
    """argparse 默认把 usage 打到 stderr 再退 2。

    裁判的每一次命令输出都会被转发成群里的事件，一坨 usage 转储既占屏又泄露流程
    细节；而且模型在那个当口需要的不是 usage，是一条能直接重跑的命令。所以这里把
    参数错误也变成脚本自己的 JSON，并把已经认出来的会话 ID 一起递回去。
    """

    def error(self, message: str) -> None:  # type: ignore[override]
        die(
            "BAD_ARGS",
            f"参数不对：{message}。`--session` 从 GroupContext 里抄（形如 bcs_grp_xxxx:yyyy）；"
            "`--group` 可以省，脚本会从会话 ID 冒号前半段推出来。",
            command=(self.prog or "undercover.py").split()[-1],
            usage=self.format_usage().strip(),
        )


@contextmanager
def locked(session_id: str):
    """整个读-改-写序列持锁，避免并发激活互相覆盖。"""
    lock_file = state_path(session_id).with_suffix(".lock")
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass
        yield
    finally:
        os.close(fd)


def load_state(session_id: str) -> dict[str, Any]:
    p = state_path(session_id)
    if not p.exists():
        die("NO_STATE", f"没有找到这一局的状态文件：{p}。如果是新的一局，先跑 init。")
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        die("BAD_STATE", f"状态文件无法解析：{exc}。不要凭记忆继续，向人类说明并停下。")
        raise AssertionError("unreachable")
    # 文件名是清洗过的安全名（':' 也会被换掉），所以两个长得像的会话理论上能撞进
    # 同一个文件。这条自检几乎不花钱，却能把「我操作的是不是这一局」变成硬事实。
    got = str(state.get("session_id") or "")
    if got and got != session_id:
        die(
            "STATE_SESSION_MISMATCH",
            f"这个状态文件属于另一局（{got}），不是你要的 {session_id}。"
            "不要继续，先确认 --session 传对了。",
        )
    return state


def save_state(state: dict[str, Any]) -> None:
    p = state_path(state["session_id"])
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# 本次进程操作的是哪一局。emit / die 都会把它带出去：认错局这件事必须在群里看得见，
# 而不是等到主持人念出上一局的答案才被发现。
CURRENT_SESSION: str | None = None


def die(code: str, message: str, **extra: Any) -> None:
    payload: dict[str, Any] = {"ok": False, "error": code}
    if CURRENT_SESSION:
        payload["session"] = CURRENT_SESSION
    payload.update({"message": message, **extra})
    json.dump(payload, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.exit(2)


def emit(payload: dict[str, Any], compact: bool = False) -> None:
    """命令输出也是公开的。

    Bot 的每一次工具调用和它的输出都会被转发成群里的事件，所以一条 40 行的状态
    JSON 和一段废话一样占屏。默认输出因此尽量短，详细版只在明确要的时候给。
    """
    head: dict[str, Any] = {"ok": True}
    if CURRENT_SESSION:
        head["session"] = CURRENT_SESSION
    payload = {**head, **payload}
    json.dump(payload, sys.stdout, ensure_ascii=False, indent=None if compact else 2)
    sys.stdout.write("\n")


def require_phase(state: dict[str, Any], *allowed: str) -> None:
    if state["phase"] not in allowed:
        die(
            "WRONG_PHASE",
            f"当前阶段是 {state['phase']}，这个命令只能在 {'/'.join(allowed)} 阶段用。"
            f" 下一步应该是：{NEXT_ACTION.get(state['phase'], '先跑 status')}",
        )


# --------------------------------------------------------------------------
# 座位辅助
# --------------------------------------------------------------------------

def seat_of(state: dict[str, Any], seat: int) -> dict[str, Any]:
    for s in state["seats"]:
        if s["seat"] == seat:
            return s
    die("NO_SEAT", f"没有 {seat} 号座位。")
    raise AssertionError("unreachable")


def alive_seats(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in state["seats"] if s["alive"]]


def label_of(state: dict[str, Any], seat: int) -> str:
    """念稿用的称呼：名字后面永远跟着号数。

    人类玩家在副屏里投的是号码，在群里听到的却是名字——中间那次映射一直是他自己
    在做。所以事实层直接把成品给出来，主持稿里每个人第一次出现就用这个。
    """
    s = seat_of(state, seat)
    return f"{s['display']}（{s['seat']}号）"


def public_name(state: dict[str, Any], seat: int) -> str:
    """写给 Bot 看时的称呼：人类座位不能叫「你」。"""
    s = seat_of(state, seat)
    return "人类玩家" if s["kind"] == "human" else s["display"]


def current_round(state: dict[str, Any]) -> dict[str, Any]:
    return state["rounds"][-1]


def public_history(state: dict[str, Any]) -> list[dict[str, Any]]:
    """给玩家看的公开发言史，只含可展示文本，不含词与身份。"""
    out = []
    for rnd in state["rounds"]:
        if not rnd["speeches"]:
            continue
        out.append(
            {
                "round": rnd["round"],
                "speeches": [
                    {
                        "seat": int(seat),
                        "player": seat_of(state, int(seat))["display"],
                        "text": rec["display"],
                    }
                    for seat, rec in sorted(rnd["speeches"].items(), key=lambda kv: int(kv[0]))
                ],
            }
        )
    return out


# --------------------------------------------------------------------------
# 违规检查与遮蔽
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# 发言钝度
# --------------------------------------------------------------------------

# 泄词有两条路：字面上说出词（forbid_line 拦得住），和语义上把词描述到只对应一件
# 东西（脚本拦不住）。第二条才是实战里致命的那条——第一轮就下定义，卧底当场暴露，
# 一轮结束。
#
# 这里给的是一把玩家能自己算的尺子：把这句话念给局外人听，他能不能列出至少 N 样
# 符合的东西。N 按轮次收敛，信息就按轮次释放。
#
# 为什么必须收敛：投票只看历史发言，发言要是一直很钝，投票就退化成随机；6 人 1 个
# 卧底、每轮出局 1 个，第 4 轮结束只剩 2 人卧底就赢了，所以平民只有 4 轮时间，第 3
# 轮起必须放到能真正推理的程度。
#
# 起点从 5 下调到 4：实测第一轮六句全是纯感受（「一天不碰它我就浑身难受」「合上
# 之后会愣一会儿」），票型完全是噪声，第一轮白烧了一轮还冤杀了一个平民。真正卡死
# 第一轮的其实不是这个数字，是下面那份类目白名单——它把属性整类禁掉了，所以一起
# 松开：第一轮允许带一个属性，只是不许把用途和对象塞进同一句。
BLUNTNESS_LADDER = {1: 4, 2: 3}
BLUNTNESS_FLOOR = 2


def bluntness_n(rnd: int) -> int:
    return BLUNTNESS_LADDER.get(rnd, BLUNTNESS_FLOOR)


ANGLE_MENU = "什么时候会想到它 / 多久遇到一次 / 用完是什么感觉 / 一般放在哪儿 / 大概是什么样"


def bluntness_block(rnd: int, first: bool) -> str:
    """本轮的钝度要求，逐字写进每个发言节点的 instruction。

    这一段刻意写成**闭合式**：给一张固定的角度表让它挑一个，而不是让它自己去枚举
    「这句话还能罩住哪些东西」。开放式自检（"念给局外人，他能列出至少 N 样吗"）要
    求模型在开口之前先在心里造一批候选物再逐个比对——2026-08-31 那一局里，每个发
    言节点从拿到 200 响应到吐出第一个字平均静默 35 秒（最长 76 秒），产出只有 20
    来个字，那段静默就是这一步。闭合式给的是同一份钝度保证：一个角度 + 不出现类目
    名词 + 不下定义，这句话必然同时罩得住一片东西，而模型不需要先把那一片列出来。
    """
    n = bluntness_n(rnd)
    lines = [f"【本轮钝度 = {n}】这句话得能同时套在至少 {n} 样别的东西上。"]
    if rnd == 1:
        lines.append(f"照这个写：从「{ANGLE_MENU}」里挑**一个**角度，只说那一个。")
        lines.append(
            "可以带一个属性（形状 / 材质 / 大小 / 场合，四选一），但一句里只放一个，"
            "而且「用途」和「对象」不能进同一句——那等于给这个词下定义。"
        )
    elif rnd == 2:
        lines.append(f"照这个写：从「{ANGLE_MENU}」里挑一个角度，可以再加一个使用场合。")
        lines.append("整句仍然不等于这个词的定义。")
    else:
        lines.append("这一轮可以说用途了，但整句仍然不能等价于这个词的定义。")
    lines.append("不出现这个词所属类目的名词（不写「这种工具」「这类饮料」）。")
    if first:
        lines.append("你是本轮第一个发言的人，本轮的钝度由你定——宁可更钝。")
    else:
        lines.append("前面的人到了什么钝度，对齐就行：不要更锐利，也不要去补他们没说到的角度。")
    lines.append("按上面这条直接写出来，不要先在心里列一批候选再挑。")
    return "\n".join(lines)


def bigrams(word: str) -> list[str]:
    """词里全部连续两字片段；两字词返回它自己，一字词返回空。"""
    return [word[i : i + 2] for i in range(len(word) - 1)]


def check_text(state: dict[str, Any], seat: int, text: str, max_chars: int) -> tuple[str, str | None]:
    """返回 (可展示文本, 违规原因或 None)。

    泄词判定有三种，都精确、可复现，也和告诉玩家的规则一字不差：
      - 完整词语出现在文本里；
      - 词里任意连续两个字出现在文本里（说了大半个词）；
      - 词的每一个字都在文本里出现过（拆散了说全）。
    单个常用字命中不算违规，否则「其他人」这种话会被误判。
    对家的词只要完整出现也遮蔽，防止串词直接摊牌给人类。
    """
    own = seat_of(state, seat)["word"]
    other = state["words"]["undercover"] if own == state["words"]["civilian"] else state["words"]["civilian"]
    display = text
    reason = None

    if own and own in display:
        display = display.replace(own, MASK * len(own))
        reason = "说出了自己的词"
    elif own and any(g in display for g in bigrams(own)):
        for g in bigrams(own):
            display = display.replace(g, MASK * len(g))
        reason = "说出了自己词里连续的两个字"
    elif own and all(ch in display for ch in own):
        for ch in set(own):
            display = display.replace(ch, MASK)
        reason = "把自己的词拆开说了"

    if other and other in display:
        display = display.replace(other, MASK * len(other))
        reason = reason or "说出了对家的词"

    if reason is None and len(text) > max_chars:
        reason = f"超过 {max_chars} 字"

    return display, reason


# --------------------------------------------------------------------------
# 投票解析
# --------------------------------------------------------------------------

CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def parse_vote(state: dict[str, Any], voter_seat: int | None, text: str) -> tuple[int | None, str | None]:
    """从一句人话里解析出被投座位。返回 (座位号或 None, 说明)。"""
    if not text or not text.strip():
        return None, "没有内容"

    if re.search(r"弃权", text):
        return None, "弃权"

    living = {s["seat"] for s in alive_seats(state)}

    def acceptable(n: int) -> bool:
        return n in living and n != voter_seat

    # 1) 「我投N号」这类锚点，取「投」之后最近的数字
    for m in re.finditer(r"投[^0-9零一二两三四五六七八九]{0,4}([0-9]+|[零一二两三四五六七八九])", text):
        tok = m.group(1)
        n = int(tok) if tok.isdigit() else CN_DIGITS[tok]
        if acceptable(n):
            return n, None

    # 2) 名字匹配
    for s in state["seats"]:
        if s["display"] and s["display"] in text and acceptable(s["seat"]):
            return s["seat"], None

    # 3) 全文里恰好只出现一个合法座位号
    nums = set()
    for tok in re.findall(r"[0-9]+|[零一二两三四五六七八九]", text):
        n = int(tok) if tok.isdigit() else CN_DIGITS[tok]
        if acceptable(n):
            nums.add(n)
    if len(nums) == 1:
        return nums.pop(), None

    if voter_seat is not None and re.search(rf"投[^0-9]{{0,4}}{voter_seat}\b", text):
        return None, "投了自己"
    return None, "读不出投给谁"


# --------------------------------------------------------------------------
# YAML 渲染
# --------------------------------------------------------------------------

def yq(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def block(text: str, indent: int) -> str:
    pad = " " * indent
    lines = text.rstrip("\n").split("\n")
    return "\n".join(pad + line if line else "" for line in lines)


def forbid_line(word: str) -> str:
    """字面泄词的红线。写成一行清单，让它照着看一眼就行——这是查表，不是让它把
    自己写的句子逐字扫一遍。真正的兜底是 check_text：服务端逐字判定 + mask 遮蔽，
    所以这里不需要把每种变体都展开成一段说明。"""
    parts = [f"不得出现「{word}」"]
    grams = bigrams(word)
    if len(grams) > 1:
        parts.append("不得出现" + "、".join(f"「{g}」" for g in grams))
    if len(word) > 1:
        parts.append("也不得把它的字拆到一句里说全")
    parts.append("不许拼音、英文、谐音")
    return "；".join(parts) + "。"


def write_run_files(session_id: str, kind: str, yaml_text: str, run_input: dict[str, Any]) -> tuple[str, str]:
    d = work_dir(session_id)
    yaml_path = d / f"{kind}.yaml"
    input_path = d / f"{kind}-input.json"
    yaml_path.write_text(yaml_text, encoding="utf-8")
    input_path.write_text(json.dumps(run_input, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(yaml_path), str(input_path)


def render_speak_yaml(state: dict[str, Any]) -> tuple[str, list[str]]:
    rnd = state["round"]
    living = sorted(alive_seats(state), key=lambda s: s["seat"])
    node_ids = [f"speak_{s['seat']}" for s in living]

    participants: list[str] = []
    for s in living:
        if s["kind"] == "bot":
            participants.append(
                f"  {s['binding']}:\n"
                f"    display_name: {yq(str(s['seat']) + '号 ' + s['display'])}\n"
                f"    required: true"
            )
    participants.append('  referee:\n    display_name: "主持人"\n    required: true')

    seat_roster = "、".join(f"{s['seat']}号 {s['display']}" for s in living)
    lead = living[0]

    # 入口节点归主持人。整局的流程推进只由主持人做——玩家 Bot 不当任何一个运行的
    # 门房。它的产物就是本轮的开场稿：提交完运行裁判不必再说话，人类在群里看到的
    # 第一句是「第 N 轮开始」，而不是裁判把活干完之后的收尾。
    #
    # 这段话会作为 [Upstream Outputs] 流给每一个发言节点，所以只准写全场都已经知道
    # 的东西：第几轮、还剩哪几号、谁先说。
    open_instruction = (
        "【主持人节点 · 回合开场】\n"
        f"第 {rnd} 轮开始。这是本轮发言运行的入口，你在这里只说一句开场，"
        "不要调用任何脚本、不要做任何判断。\n"
        f"场上还剩：{seat_roster}。第一个发言的是 {lead['seat']}号 {lead['display']}。\n"
        "说清三件事：第几轮 / 还剩谁 / 谁先说。不超过 3 句话。\n"
        + (
            "第一轮可以再带一句副屏提示（发言框在右边副屏、词写在框最上面），之后不再提。\n"
            if rnd == 1
            else ""
        )
        + "这段话会原样转给每一位玩家，所以只能包含上面这些公开信息。\n"
        "不要提任何人的词、身份、发言倾向或谁可疑，也不要出现阶段编号、节点名或运行 ID。"
    )
    nodes: list[str] = [
        "      speak_open:\n"
        "        kind: bot_task\n"
        '        display_name: "回合开场"\n'
        "        assignee:\n"
        "          type: bot_binding\n"
        "          binding: referee\n"
        f"        node_timeout_ms: {ENTRY_NODE_TIMEOUT_MS}\n"
        f"        instruction: |\n{block(open_instruction, 10)}\n"
        "        transitions:\n"
        "          complete:\n"
        "            targets: [" + ", ".join(node_ids) + "]"
    ]
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
            instruction = (
                f"【你的词语】{s['word']}\n\n"
                f"第 {rnd} 轮 · 你是 {s['seat']} 号 · 轮到你发言了。\n"
                "上面能看到本轮在你之前的人说了什么。\n\n"
                f"写一句话（不超过 {SPEECH_MAX_CHARS} 个字）描述你的词，"
                "别把这个词说出来，也别拆开来说。\n"
                f"说钝一点：这句话得能同时套在至少 {bluntness_n(rnd)} 样别的东西上。\n"
                "别提身份、别点评别人，只说你的词。"
            )
            nodes.append(
                f"      {nid}:\n"
                f"        kind: human_input\n"
                f"        display_name: {yq('👤 你的发言（' + str(s['seat']) + '号 · 人类玩家）')}\n"
                f"        node_timeout_ms: {HUMAN_NODE_TIMEOUT_MS}\n"
                f"        instruction: |\n{block(instruction, 10)}\n"
                f"        transitions:\n"
                f"          complete:\n"
                f"            targets: {targets_yaml}"
            )
        else:
            instruction = (
                f"你是 {s['seat']} 号，你的词是【{s['word']}】。第 {rnd} 轮发言。\n"
                "[Upstream Outputs]：第一条是主持人开场，其余是本轮排在你前面的人的原话。"
                "[Input]：历史轮次的发言。\n"
                f"输出一句话，不超过 {SPEECH_MAX_CHARS} 字，描述你的词。\n"
                f"{forbid_line(s['word'])}\n\n"
                f"{bluntness_block(rnd, first)}\n\n"
                "不提身份、轮次、规则、票数，不点评别人。\n"
                "只输出这句话：没有编号、引号、JSON、前缀、解释。"
            )
            nodes.append(
                f"      {nid}:\n"
                f"        kind: bot_task\n"
                f"        display_name: {yq(str(s['seat']) + '号发言')}\n"
                f"        assignee:\n"
                f"          type: bot_binding\n"
                f"          binding: {s['binding']}\n"
                f"        instruction: |\n{block(instruction, 10)}\n"
                f"        transitions:\n"
                f"          complete:\n"
                f"            targets: {targets_yaml}"
            )

    collect_instruction = (
        "【裁判节点 · 本轮发言汇总】\n"
        "[Upstream Outputs] 里是本轮全部玩家的发言。\n"
        "1. 先把每个座位的原话整理成 JSON，调 undercover.py speeches-set 交给事实层。\n"
        "2. 用它返回的可展示文本写主持稿：串场用你自己的话，发言逐字引用，不要改写、不要概括。\n"
        "   **每位玩家都要用返回里的 label 原样称呼（名字后面带号数），"
        "例如「阿和（1号）开头：「…」」。**人类在副屏里投的是号码，"
        "只报名字他就得自己去对照。\n"
        "3. 结尾告诉人类：给他几秒看完，接下来他不用做任何事——**不要再让他说一声**。\n"
        "**这个节点里只做上面这三件事。不要开投、不要调 open-vote、不要提交任何运行。**"
        "你此刻就在这个发言运行的最后一个节点里：运行要等你这次激活结束才算完成，"
        "协作槽位也才释放。在这里开投一定会被拒（IN_COLLECT_NODE），而重试、sleep、轮询都只会让"
        "这次激活一直不结束——运行永远完不成，槽位永远不释放，整局就死在这里。"
        "开投等这段稿子发出去、你被自己这条消息叫醒之后再做。\n"
        "只输出给玩家看的主持稿，不要输出任何词语、身份、内部状态、节点名或运行 ID。"
    )
    nodes.append(
        "      collect:\n"
        "        kind: bot_task\n"
        '        display_name: "本轮发言汇总"\n'
        "        assignee:\n"
        "          type: bot_binding\n"
        "          binding: referee\n"
        f"        node_timeout_ms: {CONTENDED_NODE_TIMEOUT_MS}\n"
        "        final_output: true\n"
        f"        instruction: |\n{block(collect_instruction, 10)}"
    )

    yaml_text = (
        f"name: {yq(f'谁是卧底 第{rnd}轮 发言')}\n"
        "metadata:\n"
        f"  description: {yq('存活玩家按座位顺序各说一句话描述自己的词语')}\n"
        "participants:\n" + "\n".join(participants) + "\n"
        "runtime:\n"
        "  kind: state_machine\n"
        "  state_machine:\n"
        "    version: 1\n"
        "    graph_mode: acyclic\n"
        "    defaults:\n"
        f"      node_timeout_ms: {BOT_NODE_TIMEOUT_MS}\n"
        f"      max_attempts: {NODE_MAX_ATTEMPTS}\n"
        "    nodes:\n" + "\n".join(nodes) + "\n"
    )
    bindings = [f"{s['binding']}={s['bot_uuid']}" for s in living if s["kind"] == "bot"]
    bindings.append(f"referee={state['referee_uuid']}")
    return yaml_text, bindings


def render_vote_yaml(state: dict[str, Any]) -> tuple[str, list[str]]:
    rnd = state["round"]
    living = sorted(alive_seats(state), key=lambda s: s["seat"])
    node_ids = [f"vote_{s['seat']}" for s in living]
    seat_list = "、".join(f"{s['seat']}号 {s['display']}" for s in living)

    participants = []
    for s in living:
        if s["kind"] == "bot":
            participants.append(
                f"  {s['binding']}:\n"
                f"    display_name: {yq(str(s['seat']) + '号 ' + s['display'])}\n"
                f"    required: true"
            )
    participants.append('  referee:\n    display_name: "主持人"\n    required: true')

    # 入口节点归主持人。流程推进只由主持人做，玩家 Bot 不当门房。
    #
    # 但它的产物会作为 [Upstream Outputs] 流给每一个投票节点——投票是全场信息最
    # 敏感的一刻，主持人在这里多说一个字都可能给某个人加权。所以这段稿子是全局
    # 唯一一段被写死内容边界的：只许说“开投了、票箱在副屏、只交票号、投完一起念”，
    # 不许出现任何玩家、任何发言、任何倾向。
    open_instruction = (
        "【主持人节点 · 开投】\n"
        f"第 {rnd} 轮投票现在开始。这是本轮投票运行的入口，你在这里只说一句开场，"
        "不要调用任何脚本、不要做任何判断。\n"
        "只说这四件事：开投了 / 所有人同时投、票箱在右边副屏 / 只交票号不写理由 / "
        "投完我一起念。不超过 2 句话，可以用 🗳️。\n"
        "这段话会原样转给每一位正在投票的玩家。**不要提到任何玩家的号码或名字、"
        "不要复述或评价任何一句发言、不要流露任何倾向**——多说一个字都可能左右选票。\n"
        "也不要出现阶段编号、节点名或运行 ID。"
    )
    nodes = [
        "      vote_open:\n"
        "        kind: bot_task\n"
        '        display_name: "开投"\n'
        "        assignee:\n"
        "          type: bot_binding\n"
        "          binding: referee\n"
        f"        node_timeout_ms: {ENTRY_NODE_TIMEOUT_MS}\n"
        f"        instruction: |\n{block(open_instruction, 10)}\n"
        "        transitions:\n"
        "          complete:\n"
        "            targets: [" + ", ".join(node_ids) + "]"
    ]

    for idx, s in enumerate(living):
        nid = node_ids[idx]
        others = "、".join(f"{o['seat']}号" for o in living if o["seat"] != s["seat"])
        if s["kind"] == "human":
            instruction = (
                f"【你的词语】{s['word']}\n\n"
                f"第 {rnd} 轮投票 · 你是 {s['seat']} 号。\n"
                "大家说过的话，主持人刚在群里念过一遍。\n\n"
                f"可以投的人：{others}，不能投自己。\n"
                "只写「我投N号」，N 是阿拉伯数字，不用写理由。\n"
                "不想投就写「我弃权」。"
            )
            nodes.append(
                f"      {nid}:\n"
                f"        kind: human_input\n"
                f"        display_name: {yq('👤 你的投票（' + str(s['seat']) + '号 · 人类玩家）')}\n"
                f"        node_timeout_ms: {HUMAN_NODE_TIMEOUT_MS}\n"
                f"        instruction: |\n{block(instruction, 10)}\n"
                f"        transitions:\n"
                f"          complete:\n"
                f"            targets: [tally]"
            )
        else:
            # 「为什么不写理由」那段论证不在这里写：玩家 profile 的 SKILL.md 里已经
            # 有一份完整的，节点里再来一遍等于让模型每投一票都把它重读重认一次。
            # 这里只留一条**闭合的**挑人规则——开放式的「读完所有人所有轮次再自
            # 己想」是投票节点吐 4 个字要花 20-70 秒的原因。
            instruction = (
                f"你是 {s['seat']} 号，你的词是【{s['word']}】。第 {rnd} 轮投票。"
                f"全场：{seat_list}。\n"
                f"[Input] 是所有人历轮发言的原话，这是唯一依据。"
                "[Upstream Outputs] 只有主持人开场，不含信息。\n"
                f"可以投：{others}。不能投自己。\n\n"
                "按这两条挑一个人，命中哪条投哪个：\n"
                "① 哪句话和你的词最对不上；② 谁前后两轮自相矛盾。\n"
                "话少、风格不同、这轮说得钝，都不算——钝是规则要求的。\n\n"
                "只输出票号：「我投N号」，N 是阿拉伯数字，整条不超过 "
                f"{VOTE_MAX_CHARS} 字。不写理由、不解释、不加前缀。\n"
                "两条都命中不了就只输出「我弃权」。\n"
                f"输出里不得出现「{s['word']}」或它的任何一部分。"
            )
            # 入口节点归了裁判，玩家的投票节点不再和任何东西抢通道，走默认超时。
            nodes.append(
                f"      {nid}:\n"
                f"        kind: bot_task\n"
                f"        display_name: {yq(str(s['seat']) + '号投票')}\n"
                f"        assignee:\n"
                f"          type: bot_binding\n"
                f"          binding: {s['binding']}\n"
                f"        instruction: |\n{block(instruction, 10)}\n"
                f"        transitions:\n"
                f"          complete:\n"
                f"            targets: [tally]"
            )

    tally_instruction = (
        "【裁判节点 · 计票】\n"
        "[Upstream Outputs] 里是本轮全部玩家的投票，每条只有票号，没有理由。\n"
        "1. 把每个座位的原话整理成 JSON，调 undercover.py votes-set 交给事实层计票。\n"
        "2. 用它返回的结果写开票主持稿：逐条报谁投了谁、报票数、宣布出局者、"
        "说明身份暂不公布、报剩下几个人。判定一律以事实层返回为准，不要自己数票。\n"
        "   **每位玩家都要用返回里的 label / target_label 原样称呼（名字后面带号数），"
        "例如「阿和（1号）投了阿浪（3号）」。**只报名字的话，人类下一轮就不知道"
        "副屏里那些号码是谁。\n"
        "   **玩家没有给理由，你也不许替他们编、不许猜他们为什么这么投。**"
        "这一段的戏在票型上——谁压谁、谁是孤票、谁被围了。\n"
        "   返回里 tie 为真就是平票：本轮没有人出局，直接进下一轮，没有重投这回事。\n"
        "3. 如果事实层说本局结束，就在这里公布完整真相（先调 reveal）。\n"
        "**这个节点里只做这三件事。不要派任何任务、不要调 bcs_assign_task、"
        "不要去查 bcs 的用法，也不要开下一轮、不要提交任何运行。**"
        "你此刻就在这个投票运行的最后一个节点里：运行要等你这次激活结束才算完成，"
        "协作槽位也才释放，在这里提交下一个运行只会把整局锁死。"
        "下一轮的唤醒源等这段稿子发出去之后再安排——"
        "这条消息会把你自己叫醒一次，那次才是安排它的地方。\n"
        "只输出给玩家看的主持稿，不要输出任何未出局玩家的词语或身份、内部状态、节点名或运行 ID。"
    )
    nodes.append(
        "      tally:\n"
        "        kind: bot_task\n"
        '        display_name: "开票"\n'
        "        assignee:\n"
        "          type: bot_binding\n"
        "          binding: referee\n"
        f"        node_timeout_ms: {CONTENDED_NODE_TIMEOUT_MS}\n"
        "        final_output: true\n"
        f"        instruction: |\n{block(tally_instruction, 10)}"
    )

    yaml_text = (
        f"name: {yq(f'谁是卧底 第{rnd}轮 投票')}\n"
        "metadata:\n"
        f"  description: {yq('存活玩家根据全部发言并行投票，主持人计票')}\n"
        "participants:\n" + "\n".join(participants) + "\n"
        "runtime:\n"
        "  kind: state_machine\n"
        "  state_machine:\n"
        "    version: 1\n"
        "    graph_mode: acyclic\n"
        "    defaults:\n"
        f"      node_timeout_ms: {BOT_NODE_TIMEOUT_MS}\n"
        f"      max_attempts: {NODE_MAX_ATTEMPTS}\n"
        "    nodes:\n" + "\n".join(nodes) + "\n"
    )
    bindings = [f"{s['binding']}={s['bot_uuid']}" for s in living if s["kind"] == "bot"]
    bindings.append(f"referee={state['referee_uuid']}")
    return yaml_text, bindings


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------

def bcs_url() -> str:
    return os.environ.get("BCS_API_BASE_URL") or "http://127.0.0.1:21000"


def run_command(session_id: str, yaml_path: str, input_path: str, bindings: list[str]) -> str:
    """给人看的、可以原样粘进 shell 的提交命令（用技能里定义的 bcs 函数）。"""
    parts = ["bcs collaborate run", shlex.quote(yaml_path), "--session", shlex.quote(session_id)]
    for b in bindings:
        parts += ["--binding", shlex.quote(b)]
    parts += ["--input", "@" + shlex.quote(input_path)]
    return " ".join(parts)


def bcs_cli(*args: str, timeout: int = 90) -> tuple[int, str, str]:
    cmd = ["bcs-cli", "--url", bcs_url(), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        die(
            "NO_BCS_CLI",
            "PATH 里找不到 bcs-cli，复合命令用不了。退回手工两步：先 render-speak-run / render-vote-run 拿到 run_command，再自己跑一遍 bcs collaborate permission 和那条命令。",
        )
    except subprocess.TimeoutExpired:
        die("BCS_CLI_TIMEOUT", f"bcs-cli {args[0] if args else ''} 超过 {timeout} 秒没返回。")
    return r.returncode, r.stdout, r.stderr


def last_json(text: str) -> dict[str, Any] | None:
    """bcs-cli 有时在 JSON 前面打日志行、有时又是缩进过的多行 JSON，两种都要认。"""
    t = (text or "").strip()
    if not t:
        return None
    try:
        whole = json.loads(t)
        if isinstance(whole, dict):
            return whole
    except ValueError:
        pass
    dec = json.JSONDecoder()
    found = None
    idx = t.find("{")
    while idx != -1:
        try:
            obj, end = dec.raw_decode(t[idx:])
        except ValueError:
            idx = t.find("{", idx + 1)
            continue
        if isinstance(obj, dict):
            found = obj
            idx = t.find("{", idx + end)
        else:
            idx = t.find("{", idx + 1)
    return found


def require_run_slot(session_id: str, busy: tuple[str, str] | None = None) -> None:
    """一个 session 同时只能有一个自定义协作运行，只认服务端返回的 allowed。

    退避重试放在脚本里，不放在裁判身上：自动开投是被汇总稿的回灌唤醒的，那时发言
    运行可能刚收尾、槽位还没释放。让模型「等几秒再试一次」等于多烧一个来回，还会
    在群里多留一段无关文字。

    退避扛的只是「上一个运行刚收尾」那几秒的竞态。扛不过去时故障就换了一种性质，
    所以 `busy` 让调用方替换退避用尽之后的错误码和文案——见 SELF_LOCK。
    """
    reason: Any = None
    for attempt in range(RUN_SLOT_RETRIES):
        code, out, err = bcs_cli("collaborate", "permission", "--session", session_id)
        data = last_json(out)
        if data is None:
            die("PERMISSION_UNREADABLE", f"读不懂 collaborate permission 的返回：{(out or err)[:300]}")
        if data.get("allowed"):
            return
        reason = data.get("reason") or data.get("message") or code
        if attempt < RUN_SLOT_RETRIES - 1:
            time.sleep(RUN_SLOT_WAIT_S)
    err_code, tail = busy or (
        "RUN_SLOT_BUSY",
        "上一个运行还活着，本次什么都没改。回一句还在等谁，结束激活。",
    )
    die(
        err_code,
        f"等了 {RUN_SLOT_RETRIES} 次服务端仍不放行，reason={reason}。" + tail,
        waited_seconds=RUN_SLOT_WAIT_S * (RUN_SLOT_RETRIES - 1),
    )


# 两个运行的末节点（collect / tally）都是裁判自己的，而运行要等那次激活结束才算完成、
# 协作槽位才释放。裁判如果在末节点里就去提交下一个运行，就成了「它等槽位、槽位等它」：
# 重试、sleep、轮询只会让那次激活一直不结束，运行永远完不成，整局死在那一步。
# 2026-08-30 第 2 轮就是这样——collect 节点里跑了 open-vote，重试、sleep 10、sleep 20、
# 轮询，一路到节点超时。退避扛的是「上一个运行刚收尾」那几秒的竞态；扛不过去时故障已经
# 换了一种性质，所以换一个错误码，并把「不要重试」说死。
SELF_LOCK = {
    # phase: (错误码, 还没收尾的那个运行, 末节点名, 那个节点的稿子)
    "AWAIT_VOTE_START": ("IN_COLLECT_NODE", "发言", "本轮发言汇总", "汇总稿"),
    "AWAIT_NEXT_ROUND": ("IN_TALLY_NODE", "投票", "开票", "开票稿"),
}


def self_lock_hint(session_id: str, phase: str) -> tuple[str, str] | None:
    """槽位被占且 phase 正停在末节点推出来的那个值时，替换 require_run_slot 的报错。"""
    entry = SELF_LOCK.get(phase)
    if entry is None or load_state(session_id)["phase"] != phase:
        return None
    err_code, run_name, node_name, board = entry
    return (
        err_code,
        f"{run_name}运行还没收尾。**最可能的原因是你此刻就在它的最后一个节点"
        f"（{node_name}）里**：那个运行要等你这次激活结束才算完成，槽位才会释放。"
        "所以在那个节点里重试多少次、sleep 多久都不会成功，每试一次只是让这次激活"
        f"更长、整局更卡。**立刻结束激活**，把{board}发出去——那条消息会把你叫醒一次，"
        "下一步是那次激活的事。"
        "如果你确定这次激活不是那个节点（比如是人类说「卡住了」把你叫醒的），"
        f"那就是那个{run_name}运行已经死了、槽位还挂着：如实告诉人类还在等它判死，"
        "结束激活，不要重试。",
    )


def submit_run(session_id: str, yaml_path: str, input_path: str, bindings: list[str]) -> dict[str, Any]:
    args = ["collaborate", "run", yaml_path, "--session", session_id]
    for b in bindings:
        args += ["--binding", b]
    args += ["--input", "@" + input_path]
    code, out, err = bcs_cli(*args)
    data = last_json(out)
    if code != 0 or data is None:
        die(
            "SUBMIT_FAILED",
            f"提交运行失败（exit={code}）：{(err or out)[:300]}。"
            "不要重复提交，先告诉人类，再走卡住诊断。",
        )
    return data


def resolve_referee_uuid(explicit: str | None) -> str:
    """裁判自己的 bot_uuid。

    这一步以前是让模型去读环境变量 BCN_BOT_UUID 的，但那个变量在本平台是空的，
    而 bot_uuid 实际上就等于 Bot 名称。模型为此花了三个来回翻 session.json，
    每个来回都在群里留下一段状态旁白——那正是人类看到一堆无关文字的来源之一。
    所以这件事挪到脚本里做，模型不需要知道自己叫什么。
    """
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.environ.get("BCN_BOT_UUID")
    if env and env.strip():
        return env.strip()
    base = os.environ.get("BOT_DATA_DIR") or os.environ.get("OPENCLAW_DATA_DIR")
    if base:
        try:
            data = json.loads((Path(base) / ".bcs" / "session.json").read_text(encoding="utf-8"))
            uuid = str(data.get("bot_uuid") or "").strip()
            if uuid:
                return uuid
        except (OSError, ValueError, TypeError):
            pass
    die(
        "NO_REFEREE_UUID",
        "认不出裁判自己的 bot_uuid：BCN_BOT_UUID 是空的，$BOT_DATA_DIR/.bcs/session.json 也读不到。"
        "用 --referee-uuid 显式给一个（本平台就是 Bot 名称）。",
    )
    raise AssertionError("unreachable")


def load_word_bank(difficulty: str) -> list[tuple[str, str]]:
    path = skill_dir() / "references" / "word-bank" / f"{difficulty}.tsv"
    if not path.exists():
        die("NO_WORD_BANK", f"词库不存在：{path}")
    pairs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) == 2 and parts[0] and parts[1]:
            pairs.append((parts[0], parts[1]))
    if not pairs:
        die("EMPTY_WORD_BANK", f"词库是空的：{path}")
    return pairs


def cmd_init(args: argparse.Namespace) -> None:
    referee_uuid = resolve_referee_uuid(args.referee_uuid)
    with locked(args.session):
        if state_path(args.session).exists() and not args.force:
            die("ALREADY_STARTED", "这一局已经开过了。想重开请加 --force。")

        bots = []
        for item in args.bot:
            if "=" not in item:
                die("BAD_BOT", f"--bot 要写成 名称=UUID，收到的是：{item}")
            name, uuid = item.split("=", 1)
            bots.append((name.strip(), uuid.strip()))
        if not bots:
            die("NO_BOTS", "至少要有一个 Bot 玩家。")

        cfg = dict(DEFAULT_CONFIG)
        cfg["difficulty"] = args.difficulty
        cfg["undercover"] = args.undercover
        cfg["max_rounds"] = args.max_rounds
        cfg["players"] = len(bots) + 1
        if cfg["undercover"] >= cfg["players"]:
            die("BAD_CONFIG", "卧底人数必须小于总人数。")

        rng = random.Random(args.seed) if args.seed is not None else random.SystemRandom()

        civ, und = rng.choice(load_word_bank(args.difficulty))
        if rng.randint(0, 1):
            civ, und = und, civ

        order = [name for name, _ in bots] + ["__HUMAN__"]
        rng.shuffle(order)
        uuid_of = dict(bots)

        seats = []
        for idx, who in enumerate(order, start=1):
            if who == "__HUMAN__":
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
                        "bot_uuid": uuid_of[who],
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
            "version": STATE_VERSION,
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
        save_state(state)

    emit(
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
            "next_action": NEXT_ACTION["AWAIT_START"],
            "note": "human_word 是**人类玩家自己**那个词，也是全场唯一一个可以说出口的词——"
            "群聊只有他看得到。发牌时在群里把它念给他（「你是 N 号，你的词是【X】」），"
            "他忘了随时可以用 my-word 再问一次。别的座位的词一个字都不在这里。",
        }
    )


def cmd_status(args: argparse.Namespace) -> None:
    # 「被唤醒第一件事是 status」这条纪律，在 session 刚启动时本来是必然报错的：
    # 那时状态文件还不存在，load_state 直接退 2。第一条指令被规定成一条必然失败
    # 的命令，模型就会转去自己摸索——开局那两次参数写错正是这么来的。
    if not state_path(args.session).exists():
        emit(
            {
                "phase": "NO_GAME",
                "next_action": "这一局还没开牌。先跑 begin 做开局探测（--group 可以省略）。",
                "command": f"uc begin --session {args.session}",
            },
            compact=True,
        )
        return
    state = load_state(args.session)
    ping = state["pending_ping"]
    brief = {
        "phase": state["phase"],
        "round": f"{state['round']}/{state['config']['max_rounds']}",
        "alive": [f"{s['seat']}号{s['display']}" for s in alive_seats(state)],
        # 只有「有 Bot 出局、要念遗言」才报出来。平票那种为了叫醒裁判而派的
        # 「预备任务」已经不派了：它是一次完整的 bot 激活加两次裁判激活，换回来
        # 的只是一句「我准备好了」，而每一次 bcs_assign_task 都会让 BCS 往裁判自
        # 己的会话里灌一条 [任务状态]，那条灌入会打断裁判当时正在跑的激活。
        # 平票那一轮改成：开票稿回灌把裁判叫醒，它直接 open-round。
        "pending_ping": (ping or {}).get("bot_name") if (ping or {}).get("kind") == "eulogy" else None,
        "next_action": NEXT_ACTION.get(state["phase"], "先跑 status"),
    }
    if state["phase"] == "FINISHED":
        # 一个新会话永远不该看到 FINISHED——它自己的状态文件还不存在，只会是 NO_GAME。
        brief["note"] = (
            "如果你是刚被一个新会话叫醒的，那是认错局了：--session 传的是上一局的 ID。"
            "同一个协作群里每个会话是独立的一局。"
        )
    if not args.full:
        emit(brief, compact=True)
        return
    emit(
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


def bump_render(state: dict[str, Any], kind: str) -> int:
    """记下这一轮的某个运行渲染到第几次，并返回次数。第一次是 1。"""
    renders = current_round(state).setdefault("renders", {})
    renders[kind] = renders.get(kind, 0) + 1
    return renders[kind]


def run_file_kind(kind: str, rnd: int, attempt: int) -> str:
    return f"{kind}-r{rnd}" if attempt <= 1 else f"{kind}-r{rnd}-retry{attempt - 1}"


def prepare_speak_run(session_id: str, retry: bool) -> dict[str, Any]:
    with locked(session_id):
        state = load_state(session_id)
        if retry:
            # 重开当前这一轮：上一次提交的运行失败了，而运行失败不会唤醒裁判。
            # 轮次不推进、本轮记录不重建，只把同一份 YAML 重新渲染一次。
            require_phase(state, "SPEAK_RUNNING")
            if current_round(state)["speeches"]:
                die(
                    "ALREADY_SPOKEN",
                    "本轮发言已经收齐落盘了，不能重开发言运行。"
                    "如果卡住的是投票，用 render-vote-run --retry。",
                )
        else:
            require_phase(state, "AWAIT_START", "AWAIT_NEXT_ROUND")
            state["round"] += 1
            state["pending_ping"] = None
            state["rounds"].append(
                {
                    "round": state["round"],
                    "order": [
                        s["seat"] for s in sorted(alive_seats(state), key=lambda s: s["seat"])
                    ],
                    "speeches": {},
                    "votes": {},
                    "counts": {},
                    "eliminated": None,
                    "tie": False,
                    "renders": {},
                }
            )
        living = sorted(alive_seats(state), key=lambda s: s["seat"])
        state["phase"] = "SPEAK_RUNNING"
        attempt = bump_render(state, "speak")
        yaml_text, bindings = render_speak_yaml(state)
        run_input = {
            "game": "谁是卧底",
            "round": state["round"],
            "alive": [f"{s['seat']}号 {s['display']}" for s in living],
            "history": public_history(state),
            "rule": (
                f"用一句话（不超过{SPEECH_MAX_CHARS}字）描述你的词语，不得说出词本身，也不得拆开说；"
                f"本轮的描述要钝到至少还能套在 {bluntness_n(state['round'])} 样别的东西上"
            ),
        }
        yaml_path, input_path = write_run_files(
            session_id, run_file_kind("speak", state["round"], attempt), yaml_text, run_input
        )
        save_state(state)

    return {
        "phase": "SPEAK_RUNNING",
        "round": state["round"],
        "attempt": attempt,
        "yaml_path": yaml_path,
        "input_path": input_path,
        "bindings": bindings,
        "binding_args": " ".join(f'--binding "{b}"' for b in bindings),
        "run_command": run_command(session_id, yaml_path, input_path, bindings),
    }


def cmd_render_speak_run(args: argparse.Namespace) -> None:
    payload = prepare_speak_run(args.session, args.retry)
    payload.pop("bindings", None)
    payload["note"] = (
        "YAML 里含词语，不要读它、不要贴它。开场稿由运行的入口节点产出，提交完不要再说话。"
    )
    emit(payload, compact=True)


def cmd_speeches_set(args: argparse.Namespace) -> None:
    payload = json.loads(args.json)
    flags = {}
    for item in args.flag or []:
        seat, _, reason = item.partition("=")
        flags[int(seat)] = reason
    with locked(args.session):
        state = load_state(args.session)
        require_phase(state, "SPEAK_RUNNING")
        rnd = current_round(state)
        expected = set(rnd["order"])
        got = {int(k) for k in payload}
        if got != expected:
            die(
                "SPEECH_SET_MISMATCH",
                f"本轮应该有 {sorted(expected)} 号的发言，收到的是 {sorted(got)}。"
                "请把每个座位的原话都取全再提交。",
            )
        results = []
        for seat in rnd["order"]:
            raw = str(payload[str(seat)] if str(seat) in payload else payload[seat]).strip()
            display, reason = check_text(state, seat, raw, SPEECH_MAX_CHARS)
            if seat in flags and not reason:
                reason = flags[seat]
            rnd["speeches"][str(seat)] = {"raw": raw, "display": display, "violation": reason}
            results.append(
                {
                    "seat": seat,
                    "player": seat_of(state, seat)["display"],
                    "label": label_of(state, seat),
                    "kind": seat_of(state, seat)["kind"],
                    "text": display,
                    "violation": reason,
                }
            )
        state["phase"] = "AWAIT_VOTE_START"
        save_state(state)

    emit(
        {
            "phase": "AWAIT_VOTE_START",
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


def prepare_vote_run(session_id: str, retry: bool) -> dict[str, Any]:
    with locked(session_id):
        state = load_state(session_id)
        if retry:
            # 投票运行失败不会唤醒裁判，phase 却已经推到 VOTE_RUNNING 了。
            # 没有这条路，卡住诊断走到重开那一步就会被阶段卫兵拦死。
            require_phase(state, "VOTE_RUNNING")
        else:
            require_phase(state, "AWAIT_VOTE_START")
        state["phase"] = "VOTE_RUNNING"
        attempt = bump_render(state, "vote")
        yaml_text, bindings = render_vote_yaml(state)
        living = sorted(alive_seats(state), key=lambda s: s["seat"])
        run_input = {
            "game": "谁是卧底",
            "round": state["round"],
            "phase": "投票",
            "alive": [f"{s['seat']}号 {s['display']}" for s in living],
            "history": public_history(state),
            "rule": "只根据所有人历史全部发言，投出你认为词语和大家不一样的人；不能投自己；只交票号，不写理由",
        }
        yaml_path, input_path = write_run_files(
            session_id, run_file_kind("vote", state["round"], attempt), yaml_text, run_input
        )
        save_state(state)

    return {
        "phase": "VOTE_RUNNING",
        "round": state["round"],
        "attempt": attempt,
        "yaml_path": yaml_path,
        "input_path": input_path,
        "bindings": bindings,
        "binding_args": " ".join(f'--binding "{b}"' for b in bindings),
        "run_command": run_command(session_id, yaml_path, input_path, bindings),
    }


def cmd_render_vote_run(args: argparse.Namespace) -> None:
    payload = prepare_vote_run(args.session, args.retry)
    payload.pop("bindings", None)
    payload["note"] = (
        "YAML 里含词语，不要读它、不要贴它。开投稿由运行的入口节点产出，提交完不要再说话。"
        + ("" if payload["attempt"] <= 1 else " 这是本轮第 %d 次开投，要向人类说明之前的票作废。" % payload["attempt"])
    )
    emit(payload, compact=True)


def choose_ping(state: dict[str, Any], eliminated_seat: int | None) -> dict[str, Any] | None:
    """选一个能把裁判叫醒去开下一轮的人。出局者优先（遗言），否则找下一轮首发。

    只有 kind == "eulogy" 会真的被派出去。standby（平票那种「预备任务」）仍然渲染，
    但 status 不再把它报给裁判——那一轮的唤醒源改用开票稿的回灌，裁判在那一拍直接
    open-round。理由见 cmd_status 里 pending_ping 那段注释。
    """
    if eliminated_seat is not None:
        s = seat_of(state, eliminated_seat)
        if s["kind"] == "bot":
            return {"kind": "eulogy", "seat": s["seat"], "bot_name": s["bot_name"], "player": s["display"]}
    for s in sorted(alive_seats(state), key=lambda x: x["seat"]):
        if s["kind"] == "bot":
            return {"kind": "standby", "seat": s["seat"], "bot_name": s["bot_name"], "player": s["display"]}
    return None


def cmd_votes_set(args: argparse.Namespace) -> None:
    payload = json.loads(args.json)
    with locked(args.session):
        state = load_state(args.session)
        require_phase(state, "VOTE_RUNNING")
        rnd = current_round(state)
        expected = set(rnd["order"])
        got = {int(k) for k in payload}
        if got != expected:
            die(
                "VOTE_SET_MISMATCH",
                f"本轮应该有 {sorted(expected)} 号的投票，收到的是 {sorted(got)}。"
                "请把每个座位的原话都取全再提交。",
            )

        results = []
        counts: dict[int, int] = {}
        for seat in rnd["order"]:
            raw = str(payload[str(seat)] if str(seat) in payload else payload[seat]).strip()
            _masked, reason = check_text(state, seat, raw, VOTE_MAX_CHARS)
            target, note = parse_vote(state, seat, raw)
            if reason and reason != f"超过 {VOTE_MAX_CHARS} 字":
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
                    "player": seat_of(state, seat)["display"],
                    "label": label_of(state, seat),
                    "text": display,
                    "target_seat": target,
                    "target_player": seat_of(state, target)["display"] if target else None,
                    "target_label": label_of(state, target) if target else None,
                    "violation": reason,
                    "note": note,
                }
            )

        top = max(counts.values()) if counts else 0
        candidates = sorted(s for s, c in counts.items() if c == top) if top else []
        tie = len(candidates) != 1
        # 平票 = 本轮无人出局，直接进下一轮。没有重投，也不在平票者里随机挑人。
        #
        # 代价是明摆着的：平票不减员，但照样烧掉一轮，而轮数用完判卧底赢，所以平
        # 票是纯粹的平民损失。这是有意的——票是暗投、只有票号、Bot 之间没法串供，
        # 连着平票很难人为制造；真要调平衡，杠杆是 max_rounds，不是把重投加回来。
        eliminated = None if tie else candidates[0]

        if eliminated is not None:
            s = seat_of(state, eliminated)
            s["alive"] = False
            s["eliminated_round"] = state["round"]
        rnd["counts"] = {str(k): v for k, v in counts.items()}
        rnd["eliminated"] = eliminated
        rnd["tie"] = tie

        spies_alive = [s for s in alive_seats(state) if s["role"] == "undercover"]
        survivors = alive_seats(state)
        if not spies_alive:
            verdict, winner, reason = "finished", "civilian", "卧底已经全部出局"
        elif len(survivors) <= 2:
            verdict, winner, reason = "finished", "undercover", "场上只剩两个人，卧底还在"
        elif state["round"] >= state["config"]["max_rounds"]:
            verdict, winner, reason = "finished", "undercover", "轮数用完了还没抓到卧底"
        else:
            verdict, winner, reason = "continue", None, ""

        ping = None
        if verdict == "continue":
            ping = choose_ping(state, eliminated)
            state["pending_ping"] = ping
            state["phase"] = "AWAIT_NEXT_ROUND"
        else:
            state["result"] = {"winner": winner, "reason": reason, "round": state["round"]}
            state["phase"] = "FINISHED"
        save_state(state)

    emit(
        {
            "phase": state["phase"],
            "round": state["round"],
            "votes": results,
            "counts": [
                {
                    "seat": s,
                    "player": seat_of(state, s)["display"],
                    "label": label_of(state, s),
                    "votes": c,
                }
                for s, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ],
            "tie": tie,
            "eliminated": (
                {
                    "seat": eliminated,
                    "player": seat_of(state, eliminated)["display"],
                    "label": label_of(state, eliminated),
                }
                if eliminated
                else None
            ),
            "alive": [
                {"seat": s["seat"], "player": s["display"], "label": label_of(state, s["seat"])}
                for s in alive_seats(state)
            ],
            "verdict": verdict,
            "winner": winner,
            "win_reason": reason,
            "ping": ping,
            "next_action": NEXT_ACTION[state["phase"]],
            "note": "只使用 text 字段念稿——它已经规范化成票号，玩家的原话不会给你。"
            "每位玩家用 label / target_label 原样称呼（名字带号数）。"
            "不要替玩家编造或猜测投票理由。出局者身份不要公布，除非 verdict 是 finished。"
            "tie 为真就是本轮无人出局、直接进下一轮，没有重投这回事。"
            "ping 为空、或它的 kind 是 standby 时，**这一轮不派任何任务**："
            "开票稿会把你自己叫醒一次，那一拍直接 open-round。",
        }
    )


def history_block(state: dict[str, Any]) -> str:
    """全场公开发言，逐条带轮次和座位号。写给已出局的玩家看，所以人类那一席叫「人类玩家」。"""
    lines = []
    for rnd in public_history(state):
        for sp in rnd["speeches"]:
            lines.append(
                f"第{rnd['round']}轮 {sp['seat']}号 {public_name(state, sp['seat'])}：「{sp['text']}」"
            )
    return "\n".join(lines)


def cmd_render_ping(args: argparse.Namespace) -> None:
    state = load_state(args.session)
    ping = state["pending_ping"]
    if not ping:
        die("NO_PING", "当前没有待发的遗言或预备任务。")
    if ping["kind"] == "eulogy":
        # 遗言是整局唯一一条只流向人类玩家的线索通道：Bot 收不到群广播，也看不到
        # 别人的任务回执，所以出局者的推理只会落到人类眼里，不会污染场上任何一个
        # Bot 的判断。以前这条任务只说「可以喊冤也可以放狠话」，配 20 字上限，交
        # 回来的就是「我跟你讲，这票投错人了」——一句纯情绪。现在给素材、规定形状。
        #
        # 泄词这条边界要靠措辞守：**不能把它自己的词写进这条任务正文**（forbid_line
        # 会把词原样拼出来，而这段文字要经过裁判的输出通道流进群里，人类看到出局者
        # 的词就等于知道了他是不是卧底）。所以只能用不点名的写法，再靠 mask 兜底。
        living = alive_seats(state)
        example_seat = living[0]["seat"] if living else ping["seat"]
        message = (
            f"你在第 {state['round']} 轮被投出局了。按规矩你可以留一句遗言，主持人会念给全场听。\n\n"
            "到目前为止全场说过的话：\n"
            f"{history_block(state)}\n\n"
            f"请说一句遗言，不超过 {EULOGY_MAX_CHARS} 个字，语气还是你自己的性格，"
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
    emit(
        {
            "kind": ping["kind"],
            "seat": ping["seat"],
            "target_bot": ping["bot_name"],
            "player": ping["player"],
            "label": label_of(state, ping["seat"]),
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


def cmd_my_word(args: argparse.Namespace) -> None:
    """人类玩家忘了自己的词。

    群聊是裁判和人类的私密双人频道，Bot 一个字都看不到，所以这条随时可以答，
    次数不限。给的永远只有人类自己那一个词。
    """
    state = load_state(args.session)
    human = next((s for s in state["seats"] if s["kind"] == "human"), None)
    if human is None:
        die("NO_HUMAN", "这一局没有人类玩家。")
    emit(
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


def cmd_mask(args: argparse.Namespace) -> None:
    """把一句自由文本过一道泄词遮蔽。

    发言和投票都走协作节点，裁判在人类看到之前就能遮蔽；遗言不走节点，是公开的
    任务回执，以前这条路上没有任何机器兜底（文档里那句「唯一拦不住的是遗言」就
    是指它）。这条命令把同一套判定接到遗言上。
    """
    state = load_state(args.session)
    text, reason = check_text(state, args.seat, args.text, args.max_chars)
    emit(
        {
            "seat": args.seat,
            "label": label_of(state, args.seat),
            "text": text,
            "violation": reason,
            "note": "只念 text（遮蔽后的版本），永远不要念原话。violation 非空就顺口点一句踩线了。",
        },
        compact=True,
    )


def cmd_begin(args: argparse.Namespace) -> None:
    """开局前的全部探测一次做完：人类在不在、有哪些 Bot、我自己是谁、init 怎么写。

    这一步以前要模型自己拼三条 jq、还要去猜自己的 UUID，每一条都要一个来回，每个
    来回都会在群里留下一段状态旁白。现在一条命令给完。
    """
    referee_uuid = resolve_referee_uuid(args.referee_uuid)
    code, out, err = bcs_cli("session", "get", args.session)
    data = last_json(out)
    if code != 0 or data is None:
        die("SESSION_GET_FAILED", f"读不到会话信息（exit={code}）：{(err or out)[:300]}")

    human = None
    bots: list[tuple[str, str]] = []
    for p in data.get("participants") or []:
        kind = p.get("actor_kind") or "bot"
        uuid = str(p.get("bot_uuid") or "")
        name = str(p.get("bot_name") or uuid)
        if kind == "human":
            # 人类参与者的 mode 缺省是 absent，不是 present；字段缺失按 absent 处理。
            human = {"actor_id": uuid, "name": name, "present": (p.get("mode") or "absent") == "present"}
        elif uuid and uuid != referee_uuid and name != referee_uuid:
            bots.append((name, uuid))

    if human is None:
        emit(
            {
                "human_present": False,
                "reason": "这个会话里还没有人类参与者。",
                "next_action": "说「加入提示」那段，结束激活。人类加入后会发消息过来，届时重跑 begin。",
            },
            compact=True,
        )
        return
    if not human["present"]:
        emit(
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
            "--session", shlex.quote(args.session),
            "--group", shlex.quote(args.group),
            "--human", shlex.quote(human["actor_id"]),
        ]
        + [x for name, uuid in bots for x in ("--bot", shlex.quote(f"{name}={uuid}"))]
        + ["--difficulty", args.difficulty, "--undercover", str(args.undercover),
           "--max-rounds", str(args.max_rounds)]
    )
    emit(
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


def cmd_open_round(args: argparse.Namespace) -> None:
    """开一轮发言：查槽位 → 渲染 → 提交，一条命令做完。

    合成这一条不是为了省事，是为了把「提交」和「激活结束」之间的距离压到零。
    入口节点是裁判自己的，它在提交那一刻就排进裁判的通道；裁判在提交之后每多花
    一个来回，入口节点就多等一个来回。所以提交必须是本次激活的最后一个动作，
    而最省事的保证办法就是让它和前面几步待在同一条命令里。
    """
    # 开下一轮的正常入口是遗言回执那次激活；如果裁判在开票节点里就开下一轮，
    # 和 collect 节点里开投是同一条死锁，见 SELF_LOCK。
    busy = None if args.retry else self_lock_hint(args.session, "AWAIT_NEXT_ROUND")
    require_run_slot(args.session, busy)
    payload = prepare_speak_run(args.session, args.retry)
    result = submit_run(args.session, payload["yaml_path"], payload["input_path"], payload["bindings"])
    emit(
        {
            "phase": payload["phase"],
            "round": payload["round"],
            "attempt": payload["attempt"],
            "submitted": True,
            "run_id": result.get("run_id") or (result.get("nodes") or [{}])[0].get("run_id"),
            "next_action": "本轮开场稿由运行的入口节点产出，你现在什么都不用说。"
            "收尾只留一个字都行，立刻结束激活——你多占一秒通道，开场就晚一秒。",
        },
        compact=True,
    )


def cmd_open_vote(args: argparse.Namespace) -> None:
    """开投：查槽位 → 渲染 → 提交，到此为止。

    这条命令以前还会返回一个「看门狗」任务，让裁判在提交之后再 bcs_assign_task
    派给一个已出局的 Bot 当闹钟。那是 2026-08-31 第 3 轮投票死掉的直接原因，
    见 SELF_INJECT_NOTE：派任务和它的回执都会往裁判自己的会话里回灌一条
    `[任务状态]`，而回灌会打断裁判当时正在跑的激活。开投这一步身后正排着
    `vote_open` 入口节点，回执来得又不受控（实测 5 秒），撞上去就是整个运行失败。

    所以提交之后一件事都不做。投票运行失败的兜底统一交给人类——开场白里说清
    「超过 5 分钟没动静回我一句『卡住了』」，SX 能从 VOTE_RUNNING 重开。
    """
    # --retry 是卡住诊断在 VOTE_RUNNING 上重开，槽位被占就是「运行还活着」的正常
    # 诊断结论；非 retry 的这条路上，槽位被占只可能是发言运行没收尾，见 SELF_LOCK。
    busy = None if args.retry else self_lock_hint(args.session, "AWAIT_VOTE_START")
    require_run_slot(args.session, busy)
    payload = prepare_vote_run(args.session, args.retry)
    result = submit_run(args.session, payload["yaml_path"], payload["input_path"], payload["bindings"])
    emit(
        {
            "phase": payload["phase"],
            "round": payload["round"],
            "attempt": payload["attempt"],
            "submitted": True,
            "run_id": result.get("run_id") or (result.get("nodes") or [{}])[0].get("run_id"),
            "next_action": "立刻结束激活。开投稿由运行的入口节点产出，你不用说；"
            "**这次激活里一个工具调用都不要再加，尤其不要 bcs_assign_task**——"
            "入口节点正排在你后面，任何派单的回灌都会打断它。"
            + ("" if payload["attempt"] <= 1 else " 这是本轮第 %d 次开投，要向人类说明之前的票作废。" % payload["attempt"]),
        },
        compact=True,
    )


def cmd_reveal(args: argparse.Namespace) -> None:
    # 真相只许公布一次。
    #
    # 这是「认错局」那条故障链上的最后一道闸：主持人在一个新会话里读到上一局的
    # FINISHED，接着 reveal，把上一局的词和身份念进了一个还没发牌的会话。会话解析
    # 那一层已经堵死了这条路，但公布答案是不可撤销的动作，值得再拦一道。
    with locked(args.session):
        state = load_state(args.session)
        if state["phase"] != "FINISHED":
            die("NOT_FINISHED", "本局还没结束，现在不能公布真相。")
        if state.get("revealed_at"):
            die(
                "ALREADY_REVEALED",
                f"这一局的真相在 {state['revealed_at']} 已经公布过一次了，不会再公布第二次。"
                "如果你是刚被一个新会话叫醒的，那说明认错局了——新的一局要先 begin。"
                "如果终局稿已经发出去了，就别再发一遍。",
            )
        state["revealed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)
    emit(
        {
            "winner": state["result"]["winner"],
            "win_reason": state["result"]["reason"],
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
            "rounds": [
                {
                    "round": r["round"],
                    "speeches": {k: v["display"] for k, v in r["speeches"].items()},
                    "votes": {k: v["target"] for k, v in r["votes"].items()},
                    "eliminated": r["eliminated"],
                    "tie": r["tie"],
                }
                for r in state["rounds"]
            ],
        }
    )


def cmd_parse_vote(args: argparse.Namespace) -> None:
    state = load_state(args.session)
    target, note = parse_vote(state, args.voter, args.text)
    emit(
        {
            "target_seat": target,
            "target_player": seat_of(state, target)["display"] if target else None,
            "note": note,
        }
    )


def main() -> None:
    parser = JsonArgumentParser(description="谁是卧底 · 裁判事实层")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_session(p: argparse.ArgumentParser) -> argparse.ArgumentParser:
        # --session / --group 都不是 required：漏了就自己认（见 resolve_session）。
        p.add_argument("--session", default=None)
        return p

    p = with_session(sub.add_parser("init", help="开一局：抽词、排座位、抽卧底"))
    p.add_argument("--group", default=None)
    p.add_argument("--human", required=True)
    p.add_argument(
        "--referee-uuid",
        default=None,
        help="裁判自己的 bot_uuid。不给就由脚本解析（BCN_BOT_UUID → .bcs/session.json）。",
    )
    p.add_argument("--bot", action="append", default=[], metavar="名称=UUID")
    p.add_argument("--difficulty", default="medium", choices=["easy", "medium", "hard"])
    p.add_argument("--undercover", type=int, default=1)
    p.add_argument("--max-rounds", type=int, default=6)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = with_session(sub.add_parser("status", help="当前阶段和下一步"))
    p.add_argument("--full", action="store_true", help="连出局名单、渲染次数、终局结果一起给")
    p.set_defaults(func=cmd_status)

    p = with_session(sub.add_parser("render-speak-run", help="渲染本轮发言协作"))
    p.add_argument(
        "--retry",
        action="store_true",
        help="重开当前这一轮的发言运行（上一次提交的运行失败了）。轮次不推进。",
    )
    p.set_defaults(func=cmd_render_speak_run)

    p = with_session(sub.add_parser("render-vote-run", help="渲染本轮投票协作"))
    p.add_argument(
        "--retry",
        action="store_true",
        help="重开当前这一轮的投票运行（上一次提交的运行失败了）。之前的票作废。",
    )
    p.set_defaults(func=cmd_render_vote_run)

    p = with_session(sub.add_parser("begin", help="开局探测：人类在不在、有哪些 Bot、init 怎么写"))
    p.add_argument("--group", default=None)
    p.add_argument("--referee-uuid", default=None)
    p.add_argument("--difficulty", default="medium", choices=["easy", "medium", "hard"])
    p.add_argument("--undercover", type=int, default=1)
    p.add_argument("--max-rounds", type=int, default=6)
    p.set_defaults(func=cmd_begin)

    p = with_session(sub.add_parser("open-round", help="开一轮发言：查槽位、渲染、提交"))
    p.add_argument("--retry", action="store_true", help="重开当前这一轮的发言运行。轮次不推进。")
    p.set_defaults(func=cmd_open_round)

    p = with_session(sub.add_parser("open-vote", help="开投：查槽位、渲染、提交"))
    p.add_argument("--retry", action="store_true", help="重开当前这一轮的投票运行。之前的票作废。")
    p.set_defaults(func=cmd_open_vote)

    with_session(sub.add_parser("render-ping", help="渲染遗言或预备任务")).set_defaults(
        func=cmd_render_ping
    )
    with_session(sub.add_parser("reveal", help="终局公布真相")).set_defaults(func=cmd_reveal)

    p = with_session(sub.add_parser("speeches-set", help="提交本轮发言：检查、遮蔽、落盘"))
    p.add_argument("--json", required=True, metavar='{"1":"...","3":"..."}')
    p.add_argument("--flag", action="append", default=[], metavar="座位=原因")
    p.set_defaults(func=cmd_speeches_set)

    p = with_session(sub.add_parser("votes-set", help="提交本轮投票：解析、计票、判定"))
    p.add_argument("--json", required=True, metavar='{"1":"...","3":"..."}')
    p.set_defaults(func=cmd_votes_set)

    p = with_session(sub.add_parser("parse-vote", help="单独解析一句投票（调试用）"))
    p.add_argument("--text", required=True)
    p.add_argument("--voter", type=int, default=None)
    p.set_defaults(func=cmd_parse_vote)

    with_session(
        sub.add_parser("my-word", help="人类玩家自己的词（只说给他一个人听）")
    ).set_defaults(func=cmd_my_word)

    p = with_session(sub.add_parser("mask", help="给一句自由文本（遗言）做泄词遮蔽"))
    p.add_argument("--seat", type=int, required=True)
    p.add_argument("--text", required=True)
    p.add_argument("--max-chars", type=int, default=EULOGY_MAX_CHARS)
    p.set_defaults(func=cmd_mask)

    args = parser.parse_args()
    global CURRENT_SESSION
    args.session = resolve_session(getattr(args, "session", None), args.command)
    CURRENT_SESSION = args.session
    if hasattr(args, "group"):
        args.group = resolve_group(args.group, args.session)
    args.func(args)


if __name__ == "__main__":
    main()
