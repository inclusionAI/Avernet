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
输出都会被转发成群里的事件，来回越多、人类看到的无关文字越多；运行直接从玩家节点开始，主持人只播报返回的公开开场。
"""

from __future__ import annotations

import argparse
import importlib.util
from types import SimpleNamespace
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

# Load sibling support when this file is also imported directly by contract tests.
_recovery_spec = importlib.util.spec_from_file_location("undercover_recovery", Path(__file__).with_name("recovery.py"))
_recovery = importlib.util.module_from_spec(_recovery_spec)
_recovery_spec.loader.exec_module(_recovery)


def recovery():
    return _recovery.Recovery(SimpleNamespace(**globals()))


STATE_VERSION = 7

# 发言提交后没有主持人入口任务；只播报公开开场并释放当前激活。
SUBMITTED_NEXT_ACTION = "原样播报 announcement 后结束激活；不追加工具调用，不宣称任何玩家已经完成。"

PHASES = (
    "AWAIT_START",
    "SPEAK_RUNNING",
    "AWAIT_VOTE_START",
    "VOTE_RUNNING",
    "AWAIT_PK_SPEAK_START",
    "PK_SPEAK_RUNNING",
    "AWAIT_PK_VOTE_START",
    "PK_VOTE_RUNNING",
    "AWAIT_NEXT_ROUND",
    "FINISHED",
)

NEXT_ACTION = {
    "AWAIT_PK_SPEAK_START": "常规投票平票。计票节点只报 PK 名单后结束；开票稿回灌后 open-round 开始本轮 PK 发言，不派遗言。",
    "PK_SPEAK_RUNNING": "等 PK 发言汇总节点；只收 PK 玩家发言，调 speeches-set。",
    "AWAIT_PK_VOTE_START": "PK 发言汇总节点念稿后结束；回灌后 open-vote 开始 PK 投票。",
    "PK_VOTE_RUNNING": "等全体存活玩家 PK 投票收齐，在计票节点调 votes-set。",
    "AWAIT_START": "开第一轮：先用工具调用前的消息告诉人类座位和 human_word，然后最后调用 open-round；不要把发牌告知留到提交后的最终回复。",
    "SPEAK_RUNNING": "等发言协作把汇总节点派给你；拿到全部发言后调 speeches-set。",
    "AWAIT_VOTE_START": "**如果这次激活是「本轮发言汇总」节点：念完汇总稿就结束激活，不要在那里开投。**你正占着协作槽位，在那个节点里跑 open-vote 一定失败，重试会卡死整局。其余情况（汇总稿的回灌、人类说话）：直接开投，不用等人类说话，跑 open-vote。",
    "VOTE_RUNNING": "等投票协作把计票节点派给你；计票节点上调 votes-set。运行超时未回就走卡住诊断。",
    "AWAIT_NEXT_ROUND": "pending_ping 非空（有 Bot 出局，要念遗言）：派出遗言任务，"
    "等它的回执把你叫醒，再 open-round。"
    "pending_ping 为空（平票，或出局的是人类）：**不要派任何任务**，直接 open-round——"
    "开票稿的回灌就是这一步的唤醒源，bcs_assign_task 会打断你自己这次激活。",
    "FINISHED": "本局已经结束、真相也公布过了。只说一句「本局已结束，新建会话再来一局」，"
    "**不要再 reveal、不要 bcs_task_complete**。仅维护者确认故障已修复并明确要求重试关闭当前会话时，调用 finish --session；其余情况不调脚本。"
    "如果你是刚被一个新会话叫醒的，那说明 --session 传错了：新会话应该看到 NO_GAME。",
}

SPEECH_MAX_CHARS = 25
# 投票只交票号：「我投4号」5 字、「我弃权」3 字。理由是泄露渠道——一条理由本质上
# 是「拿我的词去比对他的话」的结果，念出来就等于广播自己那个词的一个属性。
VOTE_MAX_CHARS = 10
MASK = "○"

# SELF_INJECT_NOTE：派遗言的状态与回执可能回灌到主持人当前会话。
# 只在上一运行结束后派遗言，并作为最后一个工具调用；提交后不派看门狗。
# 旧实现曾由主持人执行 speak_open/vote_open，现已移除该自派入口路径。

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
SESSION_REQUIRED_COMMANDS = ("status", "begin", "init", "reveal", "publish-result", "finish", "my-word")


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
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
        directory = os.open(str(p.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
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


ANGLE_MENU = "什么时候会想到它 / 多久遇到一次 / 用完是什么感觉 / 一般放在哪儿 / 大概是什么样"


# --------------------------------------------------------------------------
# 投票解析
# --------------------------------------------------------------------------

CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


# --------------------------------------------------------------------------
# YAML 渲染
# --------------------------------------------------------------------------


PANEL_COMPONENT = "bcsPanel.UndercoverGamePanel"
RUN_ID_TEMPLATE = "{{bcs.run_id}}"
UI_CONTEXT_OPEN = "[UNDERCOVER_UI_CONTEXT_V1]"
UI_CONTEXT_CLOSE = "[/UNDERCOVER_UI_CONTEXT_V1]"


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------

def run_command(
    session_id: str,
    yaml_path: str,
    input_path: str,
    panel_params_path: str,
    panel_tab: dict[str, str],
    bindings: list[str],
) -> str:
    """Give the exact same custom-panel command used by ``submit_run``."""
    parts = ["bcs-cli collaborate run", shlex.quote(yaml_path), "--session", shlex.quote(session_id)]
    for b in bindings:
        parts += ["--binding", shlex.quote(b)]
    parts += [
        "--input", "@" + shlex.quote(input_path),
        "--panel-component", PANEL_COMPONENT,
        "--panel-params", "@" + shlex.quote(panel_params_path),
        "--panel-tab-id", shlex.quote(panel_tab["id"]),
        "--panel-tab-title", shlex.quote(panel_tab["title"]),
        "--panel-tab-closable", "false",
    ]
    return " ".join(parts)


def _bcs_env() -> dict[str, str]:
    """Build the environment for bcs-cli subprocesses.

    bcs-cli reads BOT_DATA_DIR to locate .bcs/session.json. Normalize
    OPENCLAW_DATA_DIR and the home fallback just like game_dir() does,
    while leaving credential discovery and precedence to the CLI.
    """
    env = os.environ.copy()
    if not env.get("BOT_DATA_DIR"):
        env["BOT_DATA_DIR"] = env.get("OPENCLAW_DATA_DIR") or str(Path.home() / ".openclaw")
    return env


def bcs_cli(*args: str, timeout: int = 90) -> tuple[int, str, str]:
    cmd = ["bcs-cli", *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=_bcs_env())
    except FileNotFoundError:
        die(
            "NO_BCS_CLI",
            "运行环境未提供 bcs-cli。停止本次激活，报告运行环境未就绪，由维护者修复。",
        )
    except subprocess.TimeoutExpired:
        die("BCS_CLI_TIMEOUT", f"bcs-cli {args[0] if args else ''} 超过 {timeout} 秒没返回。")
    if r.returncode != 0:
        diagnostic = r.stdout + "\n" + r.stderr
        if re.search(r"\b401\b|unauthorized|valid bot token is required", diagnostic, re.IGNORECASE):
            die("BCS_AUTH_FAILED", "BCS 未能认证当前 Bot。停止本次激活，请维护者检查原 Bot 的运行时认证与容器身份；不重新 connect、不查找或传递 token。")
        if re.search(r"\b403\b|forbidden", diagnostic, re.IGNORECASE):
            die("BCS_FORBIDDEN", "当前 Bot 无权执行此 BCS 操作。停止本次激活，请维护者核对当前会话角色与授权。")
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
        if code != 0 or data is None or not isinstance(data.get("allowed"), bool):
            die("PERMISSION_UNREADABLE", "协作权限查询失败或响应不符合契约。停止本次激活，由维护者检查 CLI 与服务版本。")
        if data["allowed"]:
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
    actual_phase = load_state(session_id)["phase"]
    if actual_phase in ("AWAIT_PK_SPEAK_START", "AWAIT_PK_VOTE_START"):
        phase = actual_phase
    entry = SELF_LOCK.get({"AWAIT_PK_SPEAK_START": "AWAIT_NEXT_ROUND", "AWAIT_PK_VOTE_START": "AWAIT_VOTE_START"}.get(phase, phase))
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


def submit_run(
    session_id: str,
    yaml_path: str,
    input_path: str,
    panel_params_path: str,
    panel_tab: dict[str, str],
    bindings: list[str],
) -> dict[str, Any]:
    args = ["collaborate", "run", yaml_path, "--session", session_id]
    for b in bindings:
        args += ["--binding", b]
    args += [
        "--input", "@" + input_path,
        "--panel-component", PANEL_COMPONENT,
        "--panel-params", "@" + panel_params_path,
        "--panel-tab-id", panel_tab["id"],
        "--panel-tab-title", panel_tab["title"],
        "--panel-tab-closable", "false",
    ]
    code, out, err = bcs_cli(*args)
    data = last_json(out)
    if code != 0 or data is None:
        die(
            "SUBMIT_FAILED",
            f"提交运行失败（exit={code}）：{(err or out)[:300]}。"
            "不要重复提交，先告诉人类，再走卡住诊断。",
        )
    return data


def session_roster(session_id: str, referee_uuid: str) -> tuple[dict[str, Any] | None, list[tuple[str, str]]]:
    """Validate the delivery recipient against the authoritative session roster."""
    code, out, _ = bcs_cli("session", "get", session_id)
    data = last_json(out)
    if code != 0 or data is None:
        die("SESSION_GET_FAILED", "读不到当前 BCS 会话。停止本次激活，由维护者检查运行环境。")
    if data.get("id", data.get("session_id")) != session_id:
        die("SESSION_MISMATCH", "BCS 返回的会话 ID 与本次投递不一致。停止，不发牌。")
    participants = data.get("participants")
    if not isinstance(participants, list) or not all(isinstance(p, dict) for p in participants):
        die("ROSTER_MISMATCH", "BCS 会话缺少有效 participants。停止，不猜测成员。")
    ids = [p.get("bot_uuid") for p in participants]
    if any(not isinstance(value, str) or not value.strip() for value in ids) or len(set(ids)) != len(ids):
        die("ROSTER_MISMATCH", "会话成员 ID 为空或重复。停止，不发牌。")
    managers = [p for p in participants if p.get("actor_kind", "bot") == "bot" and p.get("role") == "manager"]
    if len(managers) != 1 or managers[0]["bot_uuid"] != referee_uuid:
        die("REFEREE_MISMATCH", "本次 GroupContext.recipient 必须精确匹配会话中唯一 manager 的 bot_uuid。停止；不要按名称猜测或 connect 新身份。")
    humans = [p for p in participants if p.get("actor_kind") == "human"]
    if len(humans) > 1:
        die("ROSTER_MISMATCH", "本游戏只支持一名人类玩家。停止，请核对会话成员。")
    human = None if not humans else {
        "actor_id": humans[0]["bot_uuid"],
        "present": humans[0].get("mode") == "present",
    }
    bots = [(str(p.get("bot_name") or p["bot_uuid"]), p["bot_uuid"]) for p in participants
            if p.get("actor_kind", "bot") == "bot" and p.get("role") == "worker"]
    if not bots:
        die("NO_BOTS", "当前会话没有 worker 玩家。停止，请核对会话角色。")
    return human, bots


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


def bump_render(state: dict[str, Any], kind: str) -> int:
    """记下这一轮的某个运行渲染到第几次，并返回次数。第一次是 1。"""
    renders = current_stage(state).setdefault("renders", {})
    renders[kind] = renders.get(kind, 0) + 1
    return renders[kind]


def run_file_kind(kind: str, rnd: int, attempt: int) -> str:
    return f"{kind}-r{rnd}" if attempt <= 1 else f"{kind}-r{rnd}-retry{attempt - 1}"


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


def history_block(state: dict[str, Any]) -> str:
    """全场公开发言，逐条带轮次和座位号。写给已出局的玩家看，所以人类那一席叫「人类玩家」。"""
    lines = []
    for rnd in public_history(state):
        for sp in rnd["speeches"]:
            lines.append(
                f"第{rnd['round']}轮{' PK' if rnd.get('stage') == 'pk' else ''} {sp['seat']}号 {public_name(state, sp['seat'])}：「{sp['text']}」"
            )
    return "\n".join(lines)


def build_public_result(state: dict[str, Any]) -> dict[str, Any]:
    """Called only by reveal after the game has reached FINISHED."""
    winner = state["result"]["winner"]
    title = "平民阵营获胜" if winner == "civilian" else "卧底阵营获胜"
    lines = [f"游戏结束！{title}。", state["result"]["reason"],
             f"词对：平民词「{state['words']['civilian']}」／卧底词「{state['words']['undercover']}」", "全员身份："]
    for seat in sorted(state["seats"], key=lambda item: item["seat"]):
        role = "平民" if seat["role"] == "civilian" else "卧底"
        lines.append(f"{seat['seat']}号 {seat['display']} — {role}（{seat['word']}）")
    for entry in public_vote_history(state):
        phase = "PK 投票" if entry["stage"] == "pk" else "常规投票"
        lines.append(f"第 {entry['round']} 轮 · {phase}")
        lines.extend(f"{v['seatNumber']}号 {v['displayName']}：{v['text']}" for v in entry["votes"])
        lines.append("、".join(f"{c['seatNumber']}号 {c['votes']}票" for c in entry["counts"]) or "零有效票")
    return {
        "kind": "undercover.game-result", "version": 1, "status": "finished",
        **({"stage": "pk"} if is_pk(state) else {}),
        "gameSessionId": state["session_id"], "hostActorId": state["referee_uuid"],
        "round": state["round"], "attempt": current_stage(state)["renders"]["vote"],
        "winner": winner, "reason": state["result"]["reason"], "summary": "\n".join(lines),
    }


def publish_public_result(state: dict[str, Any]) -> None:
    """Caller holds the session lock. Reconcile a lost upload response before retrying."""
    if state.get("result_file_id"):
        return
    result = state["public_result"]
    name = current_stage(state)["result_file"]
    session = state["session_id"]
    offset = 0
    found_file_id = ""
    while True:
        code, out, _ = bcs_cli("--json", "session", "file", "list", "--session", session,
                               "--prefix", name, "--limit", "100", "--offset", str(offset))
        page = last_json(out)
        if code or not isinstance(page, dict) or not isinstance(page.get("items"), list) or not isinstance(page.get("total"), int):
            die("RESULT_PUBLISH_FAILED", "无法核对终局文件；修复后重试原命令。")
        for item in page["items"]:
            if (item.get("file_name") != name or item.get("session_id") != session
                    or str(item.get("status", "")).lower() != "ready"
                    or str(item.get("owner", {}).get("actor_kind", "")).lower() != "bot"
                    or item.get("owner", {}).get("actor_id") != state["referee_uuid"]):
                continue
            with tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / "result.json"
                code, _, _ = bcs_cli("--json", "session", "file", "download", "--session", session,
                                      "--file-id", item["file_id"], "--out", str(target))
                try:
                    existing = json.loads(target.read_text(encoding="utf-8")) if code == 0 else None
                except (OSError, ValueError):
                    existing = None
            if existing != result:
                die("RESULT_PUBLISH_FAILED", "已有终局文件无法验证或内容冲突；请维护者排查，不能覆盖。")
            found_file_id = item["file_id"]
        offset += len(page["items"])
        if offset >= page["total"]:
            break
        if not page["items"] or offset >= 1000:
            die("RESULT_PUBLISH_FAILED", "终局文件列表未完整读取，请维护者排查。")
    if found_file_id:
        state["result_file_id"] = found_file_id
        save_state(state)
        return
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / name
        target.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        code, out, _ = bcs_cli("--json", "session", "file", "upload", "--session", session,
                               "--path", str(target), "--name", name, "--mime", "application/json")
    uploaded = last_json(out)
    if (code or not isinstance(uploaded, dict) or not uploaded.get("file_id")
            or str(uploaded.get("status", "")).lower() != "ready"
            or uploaded.get("session_id") != session or uploaded.get("file_name") != name
            or str(uploaded.get("owner", {}).get("actor_kind", "")).lower() != "bot"
            or uploaded.get("owner", {}).get("actor_id") != state["referee_uuid"]):
        die("RESULT_PUBLISH_FAILED", "终局文件发布未确认；修复后重试原命令，脚本会核对已上传结果。")
    state["result_file_id"] = uploaded["file_id"]
    save_state(state)


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
        required=True,
        help="本次 BCS GroupContext.recipient 的正式 Bot ID；必须匹配当前 session 的唯一 manager。",
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
    p.add_argument("--failed-run", help="核验结果对应运行并返回安全恢复动作")
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
    p.add_argument("--referee-uuid", required=True)
    p.add_argument("--difficulty", default="medium", choices=["easy", "medium", "hard"])
    p.add_argument("--undercover", type=int, default=1)
    p.add_argument("--max-rounds", type=int, default=6)
    p.set_defaults(func=cmd_begin)

    p = with_session(sub.add_parser("open-round", help="开一轮发言：查槽位、渲染、提交"))
    p.add_argument("--retry", action="store_true", help="重开当前这一轮的发言运行。轮次不推进。")
    p.add_argument("--failed-run", help="关联失败 run，脚本查询服务端并幂等核验")
    p.set_defaults(func=cmd_open_round)

    p = with_session(sub.add_parser("open-vote", help="开投：查槽位、渲染、提交"))
    p.add_argument("--retry", action="store_true", help="重开当前这一轮的投票运行。之前的票作废。")
    p.add_argument("--failed-run", help="关联失败 run，脚本查询服务端并幂等核验")
    p.set_defaults(func=cmd_open_vote)

    with_session(sub.add_parser("render-ping", help="渲染遗言或预备任务")).set_defaults(
        func=cmd_render_ping
    )
    with_session(sub.add_parser("reveal", help="终局公布真相")).set_defaults(func=cmd_reveal)

    with_session(sub.add_parser("publish-result", help="重试发布已生成的终局结果，不重复揭晓")).set_defaults(func=cmd_publish_result)

    with_session(sub.add_parser("finish", help="公布终局后关闭 BCS 会话；失败修复后可重试")).set_defaults(func=cmd_finish)

    p = with_session(sub.add_parser("speeches-set", help="提交本轮发言：检查、遮蔽、落盘"))
    p.add_argument("--round", type=int, default=None, help="当前节点指令中的主轮号；PK 必填")
    p.add_argument("--stage", choices=("regular", "pk"), default="regular")
    p.add_argument("--attempt", type=int, default=None, help="当前节点指令中的运行尝试号；PK 必填")
    p.add_argument("--json", required=True, metavar='{"1":"...","3":"..."}')
    p.add_argument("--flag", action="append", default=[], metavar="座位=原因")
    p.set_defaults(func=cmd_speeches_set)

    p = with_session(sub.add_parser("votes-set", help="提交本轮投票：解析、计票、判定"))
    p.add_argument("--round", type=int, default=None, help="当前节点指令中的主轮号；PK 必填")
    p.add_argument("--stage", choices=("regular", "pk"), default="regular")
    p.add_argument("--attempt", type=int, default=None, help="当前节点指令中的运行尝试号；PK 必填")
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



def _bind_handler(handler):
    # Resolve dependencies on each call, as recovery() does, so CLI and direct
    # import callers use the same storage, identity and transport boundaries.
    def invoke(*args, **kwargs):
        return handler(SimpleNamespace(**globals()), *args, **kwargs)
    return invoke


for _module_name in ("game_rules", "run_rendering", "game_commands"):
    _module_spec = importlib.util.spec_from_file_location(
        "undercover_" + _module_name, Path(__file__).with_name(_module_name + ".py"))
    _module = importlib.util.module_from_spec(_module_spec)
    _module_spec.loader.exec_module(_module)
    for _handler_name in _module.__all__:
        globals()[_handler_name] = _bind_handler(getattr(_module, _handler_name))


if __name__ == "__main__":
    main()
