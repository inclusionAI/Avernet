"""Private read-model functions used by the TaskService facade.

The facade remains the sole public service; this module only keeps query and display
enrichment implementations below the repository file-size limit.
"""

from __future__ import annotations

import logging
from dataclasses import replace

from agentclaw.community.core.task.domain.models import (
    TaskContext,
    TaskExecutionGraph,
    effective_run_mode,
)
from agentclaw.community.core.task.repository.types import (
    BbsTaskOverviewRecord,
    TaskInfoRecord,
)
from agentclaw.community.core.task.task_center.task_service_support import (
    parse_status_filter,
    split_owner_bot_id,
)

logger = logging.getLogger("task.service")


def _hydrate_root_dashboard_runtime(graph: TaskExecutionGraph, task_id: str) -> None:
    """Fill missing root runtime identity for dashboard compatibility.

    ``initialize_graph`` creates a planning root before the concrete executor
    exists, so its run mode/assignee/session can legitimately be empty. The
    dashboard still needs a stable root context, so fill only missing fields
    from graph metadata. This is a read-side projection and must not overwrite
    real execution values written later by workflow/BCS/BBS dispatch.
    """
    root = next((node for node in graph.tasks if node.node_id == task_id), None)
    if root is None:
        return

    graph_props = graph.extend_props or {}
    config = graph_props.get("execution_config") or {}
    if not isinstance(config, dict):
        config = {}

    source_type = graph_props.get("source_type")
    source_type = getattr(source_type, "value", source_type)
    source_type = str(source_type or "").strip().lower()
    task_type = config.get("task_type")
    task_type = getattr(task_type, "value", task_type)
    task_type = str(task_type or "").strip().lower()

    # ``api`` is a trigger channel rather than an execution mode. Resolve its
    # concrete mode from task_type when available, otherwise retain the
    # historical single-bot default.
    run_mode_by_source = {
        "bot": "single_bot",
        "coop_group": "coop_group",
    }
    run_mode = run_mode_by_source.get(source_type)
    if source_type == "api":
        run_mode = {
            "workflow": "single_bot",
            "yaml": "coop_group",
            "bbs": "bbs",
        }.get(task_type, "single_bot")

    if not root.run_info.run_mode and run_mode:
        root.run_info.run_mode = run_mode
    if not root.run_info.assignee:
        owner_bot_id = graph_props.get("owner_bot_id")
        if owner_bot_id:
            root.run_info.assignee = str(owner_bot_id)

    if not root.run_info.extend_props.get("session_id"):
        main_session_id = config.get("main_session_id")
        if main_session_id:
            root.run_info.extend_props["session_id"] = main_session_id
    if not root.run_info.extend_props.get("assignee_owner_id"):
        owner_user_id = graph_props.get("owner_user_id")
        if owner_user_id:
            root.run_info.extend_props["assignee_owner_id"] = str(owner_user_id)


def get_task_context(self, task_id: str) -> TaskContext:
    """Return the latest graph-projected business context."""
    return self._graph.get_task_context(task_id)


def get_task_dashboard(
    self,
    task_id: str,
    node_id: str | None = None,
    *,
    include_action_log: bool = False,
) -> TaskExecutionGraph:
    """任务执行详情可视化(整图或按 node_id 子树投影),只读。

    按 root(node_id==task_id)的 ``run_info.extend_props['session_id']`` 反查 ``task_callback`` 最新回调,
    把回调审计的 ``execution_graph``(BCN/ClawMind DAG 快照)挂在图级,便于 dashboard 可见;无 session_id /
    无 callback / 未配 ``callback_repo`` → 留 ``None``。子树投影(node_id 入参)不挂(root 不在投影内)。"""
    # Dashboard requests can land on a different worker than the acceptor; re-register on every full-graph
    # read so this worker's Harness also watches persisted PENDING nodes (idempotent; skipped for subtree reads).
    if node_id is None and self._harness is not None:
        self._harness.register(task_id)
    graph = self._graph.query_task_dashboard(task_id, node_id)
    if node_id is None:
        self._hydrate_root_dashboard_runtime(graph, task_id)
    if include_action_log:
        self._graph.load_action_logs(graph)
    root = next((n for n in graph.tasks if n.node_id == task_id), None)
    sid = root.run_info.extend_props.get("session_id") if root else None
    if sid and self._callback_repo is not None:
        try:
            rec = self._callback_repo.get_latest_by_session(sid)
        except Exception as exc:  # noqa: BLE001 反查失败不阻断只读 dashboard
            logger.warning(
                "[task][dashboard] execution_graph 反查失败 session_id=%s: %s",
                sid,
                exc,
            )
            rec = None
        if rec is not None and rec.execution_graph is not None:
            graph.execution_graph = rec.execution_graph
    self._attach_assignee_bot_info(graph)
    return graph


def _attach_assignee_bot_info(self, graph: TaskExecutionGraph) -> None:
    """Attach exact Bot/owner display metadata to single-bot nodes.

    New execution rows carry ``assignee_owner_id`` separately.  When it is
    available, resolve the ``(bot_id, owner_id)`` pair instead of the
    ambiguous bot id alone.  Composite legacy assignees are split in place.
    Old rows without owner metadata retain the historical best-effort lookup
    for compatibility, but new workflow rows never take that path.
    """
    if self._bot_service is None:
        return
    pair_cache: dict[tuple[str, str], dict | None] = {}
    bot_cache: dict[str, dict | None] = {}
    pair_lookup = getattr(self._bot_service, "list_bots_by_owner_bot_pairs", None)
    for node in graph.tasks:
        if effective_run_mode(node) not in ("single_bot", "bbs"):
            continue
        assignee = (node.run_info.assignee or "").strip()
        if not assignee:
            continue
        bot_id, composite_owner_id = split_owner_bot_id(assignee, "")
        owner_id = str(
            node.run_info.extend_props.get("assignee_owner_id")
            or composite_owner_id
            or ""
        ).strip()
        info: dict | None = None
        if owner_id and callable(pair_lookup):
            key = (bot_id, owner_id)
            if key not in pair_cache:
                try:
                    result = pair_lookup(pairs=[key], page=1, page_size=1) or {}
                    items = result.get("items") or []
                    pair_cache[key] = items[0] if items else None
                except Exception as exc:  # noqa: BLE001 display-only enrichment
                    logger.warning(
                        "[task][dashboard] exact bot lookup failed bot_id=%s owner_id=%s: %s",
                        bot_id,
                        owner_id,
                        exc,
                    )
                    pair_cache[key] = None
            info = pair_cache[key]
        elif not owner_id:
            # Compatibility for old graph rows that predate split identity
            # fields.  New rows always persist the owner and use the exact
            # pair branch above.
            if bot_id not in bot_cache:
                try:
                    bot_cache[bot_id] = self._bot_service.get_bot_by_id(bot_id)
                except Exception as exc:  # noqa: BLE001 display-only enrichment
                    logger.warning(
                        "[task][dashboard] get_bot_by_id failed bot_id=%s: %s",
                        bot_id,
                        exc,
                    )
                    bot_cache[bot_id] = None
            info = bot_cache[bot_id]
        if isinstance(info, dict):
            node.run_info.extend_props["assignee_owner_id"] = info.get("owner_id")
            node.run_info.extend_props["assignee_name"] = info.get("bot_name")


def _enrich_task_owner_display(
    self, records: list[TaskInfoRecord]
) -> list[TaskInfoRecord]:
    """Return list records with normalized owner IDs and best-effort names.

    Name lookup is display enrichment only. Missing optional ports, missing
    records, and lookup failures produce ``None`` and never fail the list API.
    """
    if not records:
        return []

    normalized: list[tuple[TaskInfoRecord, str, str]] = []
    for record in records:
        bot_id, owner_id = split_owner_bot_id(record.owner_bot_id, record.owner_user_id)
        normalized.append((record, bot_id, owner_id))

    bot_names: dict[tuple[str, str], str | None] = {}
    if self._bot_service is not None:
        pairs = list(
            dict.fromkeys(
                (bot_id, owner_id)
                for _, bot_id, owner_id in normalized
                if bot_id and owner_id
            )
        )
        if pairs:
            try:
                result = (
                    self._bot_service.list_bots_by_owner_bot_pairs(
                        pairs=pairs, page=1, page_size=len(pairs)
                    )
                    or {}
                )
                for item in result.get("items") or []:
                    if not isinstance(item, dict):
                        continue
                    key = (
                        str(item.get("bot_id") or ""),
                        str(item.get("owner_id") or ""),
                    )
                    if key[0] and key[1]:
                        bot_names[key] = item.get("bot_name")
            except Exception as exc:  # noqa: BLE001 display enrichment only
                logger.warning("[task][list] owner bot name lookup failed: %s", exc)

    user_names: dict[str, str | None] = {}
    if self._staff_dept is not None:
        for _, _, owner_id in normalized:
            if not owner_id or owner_id in user_names:
                continue
            try:
                profile = self._staff_dept.get_profile_by_work_no(work_no=owner_id)
                user_names[owner_id] = getattr(profile, "nick_name", None)
            except Exception as exc:  # noqa: BLE001 display enrichment only
                logger.warning(
                    "[task][list] owner user name lookup failed user_id=%s: %s",
                    owner_id,
                    exc,
                )
                user_names[owner_id] = None

    return [
        replace(
            record,
            owner_bot_id=bot_id,
            owner_user_id=owner_id,
            owner_bot_name=bot_names.get((bot_id, owner_id)),
            owner_user_name=user_names.get(owner_id),
        )
        for record, bot_id, owner_id in normalized
    ]


def list_tasks(
    self,
    status: str | None = None,
    owner_user_id: str | None = None,
) -> list[TaskInfoRecord]:
    """列持久化 ``task_info`` 记录,可选按状态(逗号分隔的运行时态集合)和 owner 过滤。"""
    if self._task_info_repo is None:
        return []
    statuses = parse_status_filter(status)
    records = self._task_info_repo.list_records(statuses, owner_user_id=owner_user_id)
    return self._enrich_task_owner_display(records)


def list_tasks_page(
    self,
    status: str | None = None,
    owner_user_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[TaskInfoRecord], int]:
    """列持久化 ``task_info`` 记录的一页(1-based),可选按状态(逗号分隔的运行时态集合)和 owner 过滤。"""
    if self._task_info_repo is None:
        return [], 0
    statuses = parse_status_filter(status)
    records, total = self._task_info_repo.list_records_page(
        statuses, owner_user_id=owner_user_id, page=page, page_size=page_size
    )
    return self._enrich_task_owner_display(records), total


def list_bbs_tasks(
    self,
    page: int = 1,
    page_size: int = 20,
    *,
    search_word: str | None = None,
    status: str | None = None,
) -> "tuple[list[BbsTaskOverviewRecord], int]":
    """列 BBS 接力任务(有效执行模态为 'bbs')的一页(1-based):run_info ⋈ node 联合,按 task_id 补 publisher(owner_bot_id)。

    供 bbs/list 路由调用,委托 ``TaskGraphService.list_bbs_tasks_overview``,透传 status/search_word
    可选过滤(为空不过滤,退化为纯分页)。返回 ``(records, total)``——``records`` 为
    ``BbsTaskOverviewRecord``(含 task_spec/extend_props 原始 dict);title/goal/acceptances/
    assignee_name 由 adapter translator 二次解析;``total`` 为**过滤后**行数。
    查得后交 ``_enrich_bbs_publisher_names`` 把 publisher bot_id 批量解析为 name(降级 None)。
    """
    records, total = self._graph.list_bbs_tasks_overview(
        page, page_size, search_word=search_word, status=status
    )
    return self._enrich_bbs_publisher_names(records), total


def _enrich_bbs_publisher_names(
    self, records: list[BbsTaskOverviewRecord]
) -> list[BbsTaskOverviewRecord]:
    """批量解析 publisher bot_id → name(BotService.list_bots_by_owner_bot_pairs)。

    展示字段增量:``bot_service`` 缺失 / pair 缺 bot_id 或 owner_id / 查询抛错 / 未命中 →
    ``publisher_name=None``,绝不阻断列表(复用 ``_enrich_task_owner_display`` 的批量+降级模式)。
    repo 只补 ``(publisher, owner_user_id)``;name 在此用一次批量下游查询,无 N+1。
    """
    if not records or self._bot_service is None:
        return records
    pairs = list(
        dict.fromkeys(
            (r.publisher, r.owner_user_id)
            for r in records
            if r.publisher and r.owner_user_id
        )
    )
    names: dict[tuple[str, str], str | None] = {}
    if pairs:
        try:
            result = (
                self._bot_service.list_bots_by_owner_bot_pairs(
                    pairs=pairs, page=1, page_size=len(pairs)
                )
                or {}
            )
            for item in result.get("items") or []:
                if not isinstance(item, dict):
                    continue
                key = (
                    str(item.get("bot_id") or ""),
                    str(item.get("owner_id") or ""),
                )
                if key[0] and key[1]:
                    names[key] = item.get("bot_name")
        except Exception as exc:  # noqa: BLE001 display enrichment only
            logger.warning("[task][bbs] publisher bot name lookup failed: %s", exc)
    return [
        replace(r, publisher_name=names.get((r.publisher, r.owner_user_id)))
        for r in records
    ]
