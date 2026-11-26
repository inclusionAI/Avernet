"""Claude Code / AICoding 沙箱活跃探测脚本。

本模块被 ClaudeCodeActiveHealthChecker 以源码形式 base64 后下发到 Arca
沙箱内用裸 ``python3 -`` 执行，因此只允许依赖标准库，不得 import
secbaas / agentclaw 的任何模块。

设计结论（2026-11, 由 6 个生产沙箱实测得出）:

- 会话活跃双源:
  ``.claude/projects`` 下 ``*.jsonl`` 的 mtime + ``.aicoding/sessions`` 下
  ``sessions.sqlite3`` 的 ``max(sessions.updated_at)``（Relay 汇总 cc/codex
  等所有引擎的会话）。sessions.sqlite3 的文件 mtime 不可作库活跃信号:
  WAL 模式下 Relay 启动等系统行为就会写 ``-wal/-shm``，必须查表内
  ``max(updated_at)``。
- 内置任务不算活跃信号，且 MemoryOS 家族的产物必须排除:
  MemoryOS 任务（每日知识诊断/定时保鲜记忆…）为 agentTurn+bound 模式，
  即使 run 报错，bound session 也每天在调度点被刷新，且其 bound workspace
  内会持续产生 claude jsonl 转录 —— 双源都会被污染，必须按
  boundSessionKey（sessions 表）+ session uuid（jsonl 路径）双重排除。
- 内置生码任务（``7*24小时自动生码``）仅从 hasEnabledCron 计数中排除
  （防轻投递版被“任务存在”误豁免）；其产物不排除 —— 本地执行版产生
  会话/转录属于真实生产活动，应判活跃。
- 用户自建任务（如 afsc需求自动承接）不在名单内，enabled 即活跃。

输出协议与 openclaw ActiveHealthChecker 对齐: BaaS 侧
``_determine_alive_status`` 读取 ``lastSessionTime`` / ``hasEnabledCron``，
其余字段仅用于排障观测。
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3

# 沙箱内默认路径（与 openclaw checker 的硬编码口径一致）
DEFAULT_CC_DIR = "/home/admin/.claude/projects"
DEFAULT_SESSIONS_DIR = "/home/admin/.aicoding/sessions"

# 内置任务名单: enabled 任务名命中前缀即不计入 hasEnabledCron。
# 名单由 aicoding 产品侧维护，新增内置任务时同步更新（勿按精确名匹配，
# 用家族前缀覆盖名称变体，如 "MemoryOS定时保鲜记忆" 漏网案例）。
BUILTIN_CRON_NAME_PREFIXES: tuple[str, ...] = (
    "MemoryOS",
    "7*24小时自动生码",
)

# 需要排除会话产物的内置任务（其 boundSessionKey 对应的 sessions 行与
# bound workspace 下的 jsonl 转录都不算活跃信号）。生码类不在此列。
BOUND_SESSION_EXCLUDE_PREFIXES: tuple[str, ...] = ("MemoryOS",)

_SQLITE_TIMEOUT_S = 2


def _session_uuid(session_key: str) -> str | None:
    """从 Relay session key 提取 uuid。

    key 形如 ``user:<uid>:session:<uuid>:agent:<bot>``；claude jsonl 转录
    目录名形如 ``-home-admin--aicoding-workspace-<uuid>``，按 uuid 子串
    即可定位需要排除的转录文件。
    """
    parts = session_key.split(":")
    try:
        idx = parts.index("session")
    except ValueError:
        return None
    if idx + 1 < len(parts):
        return parts[idx + 1]
    return None


def _load_cron_jobs(sessions_dir: str) -> list[dict]:
    path = os.path.join(sessions_dir, "cron-tasks.json")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [job for job in data if isinstance(job, dict)]


def _load_cron_state(
    sessions_dir: str,
    builtin_name_prefixes: tuple[str, ...],
    bound_exclude_prefixes: tuple[str, ...],
) -> dict:
    """解析 cron-tasks.json: 用户自建 enabled 计数 + 内置 bound 排除清单。

    返回 dict:
      cron_enabled_count: enabled 且名字不在内置名单的任务数 (hasEnabledCron)
      builtin_enabled_count: enabled 且名字在内置名单的任务数
      bound_keys: 需要排除会话的内置任务 boundSessionKey 列表
      bound_uuids: 上述 session key 中的 uuid（用于 jsonl 路径排除）
    """
    enabled_user_jobs = 0
    builtin_enabled = 0
    bound_keys: list[str] = []
    bound_uuids: list[str] = []

    for job in _load_cron_jobs(sessions_dir):
        if not job.get("enabled"):
            continue
        name = str(job.get("name") or "")
        is_builtin = any(name.startswith(p) for p in builtin_name_prefixes)
        if is_builtin:
            builtin_enabled += 1
        else:
            enabled_user_jobs += 1
        if any(name.startswith(p) for p in bound_exclude_prefixes):
            key = job.get("boundSessionKey")
            if isinstance(key, str) and key:
                bound_keys.append(key)
                uuid = _session_uuid(key)
                if uuid:
                    bound_uuids.append(uuid)

    return {
        "cron_enabled_count": enabled_user_jobs,
        "builtin_enabled_count": builtin_enabled,
        "bound_keys": bound_keys,
        "bound_uuids": bound_uuids,
    }


def _latest_cc_jsonl(cc_dir: str, excluded_uuids: list[str]) -> str:
    """claude jsonl 最新 mtime；路径命中内置 bound uuid 的转录文件跳过。"""
    latest_mtime = 0.0
    found = False
    for root, _dirs, files in os.walk(cc_dir):
        for fname in files:
            if not fname.endswith(".jsonl"):
                continue
            path = os.path.join(root, fname)
            if any(u in path for u in excluded_uuids):
                continue
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime > latest_mtime:
                latest_mtime = mtime
                found = True
    if not found:
        return ""
    return datetime.datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d %H:%M:%S")


def _latest_session_time(db_path: str, bound_keys: list[str]) -> str:
    """sessions 表 user kind 的 max(updated_at)，排除内置 bound session。

    updated_at 为 RFC3339 UTC 文本，转沙箱本地时区输出，与 jsonl 源口径一致。
    只读连接 + 2s 超时，库不存在/锁定/损坏时返回空字符串。
    """
    if not os.path.exists(db_path):
        return ""
    try:
        con = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, timeout=_SQLITE_TIMEOUT_S
        )
        try:
            if bound_keys:
                placeholders = ",".join("?" * len(bound_keys))
                sql = (
                    "SELECT max(updated_at) FROM sessions "
                    f"WHERE session_kind='user' AND session_key NOT IN ({placeholders})"
                )
                row = con.execute(sql, bound_keys).fetchone()
            else:
                sql = "SELECT max(updated_at) FROM sessions WHERE session_kind='user'"
                row = con.execute(sql).fetchone()
        finally:
            con.close()
    except Exception:
        return ""
    raw = row[0] if row else None
    if not raw:
        return ""
    try:
        iso = datetime.datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return iso.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return ""


def probe(
    cc_dir: str = DEFAULT_CC_DIR,
    sessions_dir: str = DEFAULT_SESSIONS_DIR,
    builtin_name_prefixes: tuple[str, ...] = BUILTIN_CRON_NAME_PREFIXES,
    bound_exclude_prefixes: tuple[str, ...] = BOUND_SESSION_EXCLUDE_PREFIXES,
) -> dict:
    """采集三源活跃信号并汇总为 checker response。

    Args:
        cc_dir: claude 转录根目录。
        sessions_dir: aicoding sessions 目录（含 sessions.sqlite3 与
            cron-tasks.json）。
        builtin_name_prefixes: hasEnabledCron 排除用的内置任务名前缀。
        bound_exclude_prefixes: 需要排除会话产物的内置任务名前缀。

    Returns:
        dict: 与 openclaw active checker 对齐的 response，另含观测字段。
    """
    cron_state = _load_cron_state(
        sessions_dir, builtin_name_prefixes, bound_exclude_prefixes
    )
    cc_latest = _latest_cc_jsonl(cc_dir, cron_state["bound_uuids"])
    db_latest = _latest_session_time(
        os.path.join(sessions_dir, "sessions.sqlite3"),
        cron_state["bound_keys"],
    )
    last_session_time = max(cc_latest, db_latest)
    cron_enabled_count = int(cron_state["cron_enabled_count"])

    return {
        "ok": True,
        "lastSessionTime": last_session_time,
        "hasEnabledCron": cron_enabled_count > 0,
        "ccLatest": cc_latest,
        "dbLatest": db_latest,
        "cronEnabledCount": cron_enabled_count,
        "builtinEnabledCount": int(cron_state["builtin_enabled_count"]),
        "builtinBoundExcluded": len(cron_state["bound_keys"]),
    }


if __name__ == "__main__":
    print(json.dumps(probe()))
