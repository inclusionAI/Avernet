"""方案 A 收口守卫 — economy 治理工单状态机不可回退(spec 验收 1)。

两条守卫锚定 Tickets 9 合并后的收口成果:

**守卫 A(无 `governance_status =` 字面量赋值)**:除豁免路径外,任何模块
不得用 ``governance_status = <literal>`` 直接写工单主状态。状态变更必须经
:class:`GovernanceLifecycleService`(find→领域守卫→save)发起 —— 唯一驱动者
由分层结构保证,不是调用方自律。

豁免(允许直接写 `governance_status` 的位点,逐条 having-justification):
  - ``domain/ticket.py`` — 领域模型状态机方法(``transition_to`` 设
    ``self.governance_status``)。这是守卫本身的所在,模型方法是状态变更的
    单一物理实现。
  - ``../repository/implementations/governance/task_record.py`` — 仅 ``bulk_close_open`` 全量原语
    (SQL ``WHERE status IN ('open','scheduled')`` 等价守卫,方案 A 唯一豁免)。
    repo 不再有逐条状态机方法。
  - ``../repository/implementations/governance/notify_log.py`` — 通知侧镜像列写入(``bulk_close_open_muted`` /
    ``bulk_cancel_by_bots``),这是通知投递机的展示镜像,非工单主状态机。
  - ``services/lifecycle_service.py`` — 驱动服务内部不字面量赋值(走模型方法),
    但守卫豁免它以防 find→save 链路偶尔需直写;实际本守卫不依赖此项,留白。

**守卫 B(repo 无状态机推进入口)**:``task_record_repo`` 不得定义 9 个语义
command(close_ticket / accept_feedback / pause_ticket / review_ticket /
resume_ticket / transition_schedule_due / auto_silence_close /
advance_reminder / refresh_snapshot)。这些在 Task 9 删除;"唯一驱动者"要求
repo 上不存在可绕过驱动服务的状态推进入口。``bulk_close_open`` 是全量豁免。

两条守卫都预查豁免列表;豁免项 each has a one-line justification。新增豁免
是 review-level 决策(别偷偷加)。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                 # .../src/backend
_GOV_ROOT = (
    _BACKEND_ROOT
    / "src" / "agentclaw" / "community" / "core" / "economy" / "governance"
)
# The governance repository bodies moved to the consolidated repository package.
# Both trees are governed: allowlist keys under this root are prefixed "repository/".
_GOV_REPO_ROOT = (
    _BACKEND_ROOT
    / "src" / "agentclaw" / "community" / "core" / "repository"
    / "implementations" / "governance"
)


def _gov_files():
    """(path, allowlist-key) for every governed governance file, both roots."""
    for py in _GOV_ROOT.rglob("*.py"):
        yield py, str(py.relative_to(_GOV_ROOT))
    for py in _GOV_REPO_ROOT.rglob("*.py"):
        yield py, "repository/" + str(py.relative_to(_GOV_REPO_ROOT))


def _gov_path(rel: str) -> pathlib.Path:
    """Resolve an allowlist key back to its file under the right root."""
    if rel.startswith("repository/"):
        return _GOV_REPO_ROOT / rel[len("repository/"):]
    return _GOV_ROOT / rel


# ---------------------------------------------------------------------------
# 守卫 A — governance_status 字面量赋值豁免列表(相对 governance/ 的路径)
# ---------------------------------------------------------------------------
_GUARD_A_ALLOWLIST: dict[str, str] = {
    "domain/ticket.py": (
        "领域模型状态机方法 transition_to 设 self.governance_status —— "
        "状态变更的单一物理实现,守卫本身的所在。"
    ),
    "repository/task_record.py": (
        "仅 bulk_close_open 全量原语 WHERE 守卫(方案 A 唯一豁免);"
        "repo 不含逐条状态机方法。"
    ),
    "repository/notify_log.py": (
        "通知侧镜像列写入(bulk_close_open_muted / bulk_cancel_by_bots "
        "把通知侧 governance_status/closed 等镜像置 CLOSED)—— notify "
        "投递机的展示镜像,非工单主状态机。"
    ),
}


def _governance_status_literal_assigns(path: pathlib.Path, rel: str | None = None) -> list[str]:
    """Return ``"<rel>:<line> governance_status = <literal>"`` violations.

    Flags ``row.governance_status = <const>`` / ``obj.governance_status = X``
    attribute assignments where the RHS is a literal (str / Name / attribute
    of enum) — i.e. a direct status write, not a copy from another variable.
    Bare ``self.governance_status = target`` in the domain model is allowed
    via allowlist (ticket.py:transition_to).
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    if rel is None:
        rel = str(path.relative_to(_GOV_ROOT))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (
                    isinstance(tgt, ast.Attribute)
                    and tgt.attr == "governance_status"
                ):
                    out.append(f"{rel}:{node.lineno} governance_status =")
    return out


def test_guard_a_no_governance_status_literal_outside_allowlist():
    """守卫 A:除豁免外,无 governance_status 字面量赋值(验收 1 后半句)。"""
    violations: list[str] = []
    for py, rel in _gov_files():
        if rel in _GUARD_A_ALLOWLIST:
            continue
        violations.extend(_governance_status_literal_assigns(py, rel))
    if violations:
        pytest.fail(
            "发现 governance_status = 直接赋值(守卫 A 违规,验收 1):\n  "
            + "\n  ".join(violations)
            + "\n\nFix: 工单主状态变更须经 GovernanceLifecycleService"
            "(find→领域守卫→save)。唯一豁免=domain/ticket.py 模型方法、"
            "task_record_repo.bulk_close_open 全量原语、notify_log_repo "
            "镜像列。若确需新增豁免,加到 _GUARD_A_ALLOWLIST 并补 one-line "
            "justification。"
        )


def test_guard_a_allowlist_paths_exist():
    """豁免项必须指向真实文件,防 stale 条目藏覆盖缺口。"""
    missing = [
        rel for rel in _GUARD_A_ALLOWLIST if not _gov_path(rel).exists()
    ]
    if missing:
        pytest.fail(
            "守卫 A 豁免项已失效(删对应文件时忘了删豁免): " + ", ".join(missing)
        )


# ---------------------------------------------------------------------------
# 守卫 B — task_record_repo 不得定义 9 个语义 command
# ---------------------------------------------------------------------------
_FORBIDDEN_REPO_COMMANDS: set[str] = {
    "close_ticket",
    "accept_feedback",
    "pause_ticket",
    "review_ticket",
    "resume_ticket",
    "transition_schedule_due",
    "auto_silence_close",
    "advance_reminder",
    "refresh_snapshot",
}


def test_guard_b_repo_has_no_semantic_commands():
    """守卫 B:task_record_repo 上不存在 9 个语义 command(验收 1 前半句)。

    repo 无状态机推进入口 —— "唯一驱动者"由分层保证。
    bulk_close_open 是全量豁免(SQL WHERE 守卫),不在此 9 个内。
    """
    repo_path = _GOV_REPO_ROOT / "task_record.py"
    tree = ast.parse(repo_path.read_text(encoding="utf-8"))
    forbidden_found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in _FORBIDDEN_REPO_COMMANDS:
            forbidden_found.append(f"{node.name} (line {node.lineno})")
    if forbidden_found:
        pytest.fail(
            "task_record_repo 仍定义已删的语义 command(守卫 B 违规):\n  "
            + "\n  ".join(forbidden_found)
            + "\n\nFix: 这些 command 已在 Task 9 删除,状态机推进上移 "
            "GovernanceLifecycleService。若函数确实需要存在,说明方案 A "
            "收口被回退 —— 复核 design intent。"
        )


def test_no_external_callers_of_repo_semantic_commands():
    """锦上添花守卫:全仓(除 repo 自身)不得调用 task_repo.<9 命令>(...).

    调用方都应走 lifecycle_svc。命中说明有入口服务绕过驱动直接推 repo
    (方案 A 唯一驱动者被绕过)。
    """
    backend = _BACKEND_ROOT / "src" / "agentclaw"
    hits: list[str] = []
    # task_repo. is the typical attribute name in services/tests.
    for py in backend.rglob("*.py"):
        rel = str(py.relative_to(backend))
        # 跳过 repo 自身(已删,但避免把方法定义误判为调用)与 lifecycle
        # driver(driver 的方法名同名但走 self.<cmd> 不走 task_repo.<cmd>)。
        if "repository/task_record.py" in rel:
            continue
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in _FORBIDDEN_REPO_COMMANDS
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "_task_repo"
            ):
                hits.append(f"{rel}:{node.lineno} _task_repo.{node.func.attr}(...)")
    if hits:
        pytest.fail(
            "仍有模块直接调 _task_repo.<语义 command> —— 应走 lifecycle_svc"
            "(方案 A 唯一驱动者):\n  " + "\n  ".join(hits)
        )
