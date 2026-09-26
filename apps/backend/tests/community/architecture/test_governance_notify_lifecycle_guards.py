"""通知发送状态机收口守卫 — economy governance notify-log repo 转移原语边界
(对齐工单机 test_governance_state_machine_guards.py 的双 grep 守卫范式)。

锚定"通知发送状态机正常路径单一驱动"(spec A1/A2)的收口成果:

**守卫 A — 转移原语白名单**:``notify_log_repo.py`` 里直接写 ``notify_status``
的转移原语(改变通知投入态的方法)只许白名单 7 个存在。新增第 8 个是 review-level
决策(别偷偷加)。白名单:

  - ``claim_pending``              — pending→sending,SQL CAS(并发领用原子性),
    driver 内部豁免(``NotifyLifecycleService.claim`` 调)。
  - ``mark_sent``                  — sending→sent,SQL WHERE 守卫(遗留原语,
    driver ``mark_sent`` 走领域往返 + save_notification 不直调本原语;保留供
    后台/测试直查,下版可移除)。
  - ``mark_send_failed``           — sending→failed/pending,SQL WHERE 守卫(同
    上,遗留)。
  - ``cancel_pending_by_ticket``   — pending→cancelled,ticket 变化副作用批量。
  - ``bulk_close_open_muted``      — 批量取消,WHERE + 循环守卫(紧急制动)。
  - ``bulk_cancel_by_bots``        — 批量取消,WHERE 守卫(批量加白)。
  - ``update_delivery_status``     — by-id 投递状态变更(manual / admin._run_delivery
    手动投递路径专用,操作者已知在干嘛,豁免前置状态校验)。

**守卫 B — 前置状态守卫**:白名单原语(除 ``update_delivery_status`` 手动豁免)
必须含 ``notify_status`` 前置状态校验(SQL ``WHERE notify_status == X`` 或等价
循环判断),不得无条件改 ``notify_status`` —— 避免误覆盖已投递(sent/failed)的行。

豁免 ``update_delivery_status`` 的理由:它是 admin ``deliver_pending`` /
``deliver_by_worker`` / 卡片回调等**手动/单点**投递路径的写回(经 admin._run_delivery
按已知 notification_id 改),非自动 cron 状态推进;前置状态由调用方语境保证,不强
求 SQL WHERE。锁死后若要改手动路径前置校验,需 review。
"""
from __future__ import annotations

import ast
import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                 # .../src/backend
_REPO_PATH = (
    _BACKEND_ROOT
    / "src" / "agentclaw" / "community" / "core" / "repository"
    / "implementations" / "governance" / "notify_log.py"
)


# ---------------------------------------------------------------------------
# 守卫 A — notify_log_repo 里写 notify_status 的转移原语白名单
# ---------------------------------------------------------------------------
# 每个值为 one-line justification。
_TRANSFER_PRIMITIVES_ALLOWLIST: dict[str, str] = {
    "claim_pending": "pending→sending SQL CAS,并发领用原子性,driver 内部豁免。",
    "mark_sent": "sending→sent WHERE 守卫;遗留 SQL 原语(driver 走 save_notification),保留供后台/测试直查。",
    "mark_send_failed": "sending→failed/pending WHERE 守卫;遗留 SQL 原语(driver 走 save_notification),同上。",
    "cancel_pending_by_ticket": "pending→cancelled WHERE 守卫;ticket 变化副作用批量。",
    "bulk_close_open_muted": "批量取消 WHERE + 循环守卫;紧急制动 cancel_pending/close_all_open。",
    "bulk_cancel_by_bots": "批量取消 WHERE 守卫;批量加白副作用。",
    "update_delivery_status": "by-id 手动/单点投递写回(admin._run_delivery),操作者已知,豁免前置。",
}

# update_delivery_status 豁免守卫 B(手动路径,不强求 SQL WHERE 前置)。
_GUARD_B_EXEMPT: frozenset[str] = frozenset({"update_delivery_status"})


def _methods_writing_notify_status(path: pathlib.Path) -> dict[str, int]:
    """Return ``{method_name: lineno}`` of repo methods that assign to
    ``notify_status`` (either ``row.notify_status = X`` attribute write OR
    SQL ``.update({Orm.notify_status: X})`` dict-key write).

    Tracks the enclosing ``FunctionDef`` name for each such write.

    Known limitation(follow-up):不解析预建 dict 变量(``update_values = {Orm.notify_status: X}``
    再 ``.update(update_values)`` 传 ast.Name 的写法)。``update_delivery_status`` 用此
    pattern,靠白名单手工收录兜住;若将来新增同 pattern 方法,需手动加白名单或加固
    detector(回溯 function body 内 ast.Name 的 dict 赋值)。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    results: dict[str, int] = {}

    def _scan(node: ast.AST, current_func: ast.FunctionDef | None) -> None:
        nonlocal tree
        if isinstance(node, ast.FunctionDef):
            current_func = node
        # Detect attribute-write: <X>.notify_status = <Y>
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr == "notify_status":
                    if current_func is not None:
                        results.setdefault(current_func.name, current_func.lineno)
        # Detect SQL .update({...Orm.notify_status: <Y>}) — dict key is Attribute with attr notify_status
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "update":
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    for k in arg.keys:
                        if isinstance(k, ast.Attribute) and k.attr == "notify_status":
                            if current_func is not None:
                                results.setdefault(current_func.name, current_func.lineno)
        for child in ast.iter_child_nodes(node):
            _scan(child, current_func)

    _scan(tree, None)
    return results


def _method_has_notify_status_guard(path: pathlib.Path, method_name: str) -> bool:
    """True if ``method_name`` body references ``notify_status`` as a comparison
    predicate (``notify_status == X`` / ``notify_status.in_(...)`` / loop guard
    ``row.notify_status == X``) before/around mutating it.

    Conservative: any ``notify_status`` read-comparison in the method counts
    (SQL WHERE filter or Python ``if row.notify_status ==``).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == method_name:
            target = node
            break
    if target is None:
        return False

    found_guard = False
    for node in ast.walk(target):
        # SQL WHERE filter: GovernanceNotificationOrm.notify_status == X  or .in_(...)
        # → ast.Compare with a Comparable Attribute(attr=notify_status) on left.
        if isinstance(node, ast.Compare):
            left = node.left
            if isinstance(left, ast.Attribute) and left.attr == "notify_status":
                found_guard = True
        # Python loop guard: if <X>.notify_status == Y  (left Attribute attr notify_status)
        # also covered by ast.Compare above.
        # SQLAlchemy .filter(...) call with notify_status inside is covered by ast.Compare
        # since the filter args are Compare nodes.
    return found_guard


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_guard_a_transfer_primitives_allowlist():
    """守卫 A:notify_log_repo 写 notify_status 的方法只在白名单 7 个内。

    新增第 8 个转移原语 = review-level 决策(别偷偷加);加到
    ``_TRANSFER_PRIMITIVES_ALLOWLIST`` 并补 one-line justification。
    """
    methods = _methods_writing_notify_status(_REPO_PATH)
    extra = set(methods) - set(_TRANSFER_PRIMITIVES_ALLOWLIST)
    if extra:
        pytest.fail(
            "notify_log_repo 出现白名单外写 notify_status 的方法(守卫 A 违规):\n  "
            + "\n  ".join(f"{m} (line {methods[m]})" for m in sorted(extra))
            + "\n\nFix: 通知发送状态机正常路径经 NotifyLifecycleService(领域往返 + "
            "save_notification);批量/紧急/手动路径走白名单原语。新增原语加到 "
            "_TRANSFER_PRIMITIVES_ALLOWLIST 并补 justification。"
        )


def test_guard_a_allowlist_methods_exist():
    """豁免项必须指向真实方法定义,防 stale 条目藏覆盖缺口。"""
    tree = ast.parse(_REPO_PATH.read_text(encoding="utf-8"))
    defined = {
        n.name for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
    }
    missing = [m for m in _TRANSFER_PRIMITIVES_ALLOWLIST if m not in defined]
    if missing:
        pytest.fail(
            "守卫 A 白名单方法已失效(删方法时忘了删白名单): " + ", ".join(missing)
        )


def test_guard_b_primitives_have_status_precondition():
    """守卫 B:白名单转移原语(除 update_delivery_status 手动豁免)必须有
    ``notify_status`` 前置状态守卫(SQL WHERE 或等价循环判断),不得无条件改."""
    unguarded: list[str] = []
    for method in _TRANSFER_PRIMITIVES_ALLOWLIST:
        if method in _GUARD_B_EXEMPT:
            continue
        if not _method_has_notify_status_guard(_REPO_PATH, method):
            unguarded.append(method)
    if unguarded:
        pytest.fail(
            "notify_log_repo 转移原语缺 notify_status 前置守卫(守卫 B 违规):\n  "
            + "\n  ".join(unguarded)
            + "\n\nFix: 每个原语必须 SQL WHERE notify_status==X 或循环判断,避免"
            "误覆盖已投递(sent/failed)的行。update_delivery_status 是手动路径豁免。"
        )
