"""Read model for the paginated BBS task overview projection."""

from __future__ import annotations

from sqlalchemy import and_, case, func, or_

from agentclaw.community.core.task.repository.models import (
    TaskInfoModel,
    TaskNodeModel,
    TaskNodeRunInfoModel,
)
from agentclaw.community.core.task.repository.types import BbsTaskOverviewRecord
from agentclaw.community.utils.env_utils import get_current_env


class TaskGraphBbsOverviewMixin:
    """One environment-scoped, read-only BBS overview query implementation."""

    def list_bbs_tasks_overview(
        self,
        page: int = 1,
        page_size: int = 20,
        *,
        search_word: str | None = None,
        status: str | None = None,
    ) -> "tuple[list[BbsTaskOverviewRecord], int]":
        """列 BBS 接力任务的一页(1-based):``task_node_run_info`` ⋈ ``task_node``
        (task_id+node_id),再按 distinct task_id 批量补 ``task_info.owner_bot_id``→publisher(缺失→None)。

        BBS 判定(二选一 OR):① ``run_mode='bbs'``;② ``extend_props``(JSON)的 ``actual_run_mode='bbs'``
        (BBS 经理-员工群派发时 scoped 节点 ``run_mode='coop_group'`` 但 ``actual_run_mode='bbs'``)。
        只读投影;返回 ``(records, total)``——``total`` 为**过滤后**行数,``records`` 为当前页
        (按 ``task_node_run_info.id`` 降序(最新优先)稳定切片,LIMIT/OFFSET;页越界 → 空列表,``total`` 仍真实)。

        可选过滤(为 None 即不拼,退化为纯分页):``status``(单值,对 ``task_node.status`` 等值);
        ``search_word``(大小写不敏感模糊匹配 ``task_node.task_spec`` 或 ``task_node_run_info.extend_props``
        两列文本;``%``/``_`` 视作通配符,不做转义)。count 与分页两查询共用同一组 filters,保证 total 与页一致。

        ``task_spec``/``extend_props``/``acceptance_result`` 复用模型 ``to_record()`` 的 JSON 解析;
        title/goal/acceptances/assignee_name 由 adapter translator 二次解析(不在此 record 内)。
        """
        page = max(1, page)
        page_size = max(1, page_size)
        offset = (page - 1) * page_size
        join_clause = and_(
            TaskNodeRunInfoModel.task_id == TaskNodeModel.task_id,
            TaskNodeRunInfoModel.node_id == TaskNodeModel.node_id,
        )
        with self._db.orm_session() as db:
            # BBS 任务判定(二选一):① task_node_run_info.run_mode='bbs';
            # ② extend_props(JSON)的 actual_run_mode='bbs'。后者覆盖 BBS 经理-员工群派发——scoped
            # 节点 run_mode='coop_group' 但 extend_props.actual_run_mode='bbs'(见 bbs_modal_executor.notify
            # 落库),原 run_mode 单判会漏这批。跨方言 JSON 提取:json_valid 护栏(非 JSON/NULL → NULL);
            # 非 sqlite 需 json_unquote(MySQL JSON_EXTRACT 返回带引号标量,SQLite json_extract 已去引号)。
            dialect = db.get_bind().dialect.name
            actual_run_mode_val = case(
                (
                    func.json_valid(TaskNodeRunInfoModel.extend_props) == 1,
                    func.json_extract(TaskNodeRunInfoModel.extend_props, "$.actual_run_mode"),
                ),
                else_=None,
            )
            if dialect != "sqlite":
                actual_run_mode_val = func.json_unquote(actual_run_mode_val)
            filters: list = [
                or_(
                    TaskInfoModel.env == get_current_env(),
                    TaskInfoModel.task_id.is_(None),
                ),
                or_(
                    TaskNodeRunInfoModel.run_mode == "bbs",
                    actual_run_mode_val == "bbs",
                ),
                TaskNodeModel.is_deleted.is_(False),
            ]
            if status is not None:
                filters.append(TaskNodeModel.status == status)
            if search_word is not None:
                pat = f"%{search_word.lower()}%"
                filters.append(
                    or_(
                        func.lower(TaskNodeModel.task_spec).like(pat),
                        func.lower(TaskNodeRunInfoModel.extend_props).like(pat),
                    )
                )
            total = (
                db.query(func.count(TaskNodeRunInfoModel.task_id))
                .join(TaskNodeModel, join_clause)
                .outerjoin(TaskInfoModel, TaskInfoModel.task_id == TaskNodeRunInfoModel.task_id)
                .filter(*filters)
                .scalar()
            ) or 0

            joined = (
                db.query(TaskNodeRunInfoModel, TaskNodeModel)
                .join(TaskNodeModel, join_clause)
                .outerjoin(TaskInfoModel, TaskInfoModel.task_id == TaskNodeRunInfoModel.task_id)
                .filter(*filters)
                .order_by(TaskNodeRunInfoModel.id.desc())
                .limit(page_size)
                .offset(offset)
                .all()
            )
            if not joined:
                return [], total

            # publisher:按 distinct task_id 一次 in_() 批查 task_info.owner_bot_id + owner_user_id,避免 N+1。
            task_ids = {run.task_id for run, _ in joined}
            publishers: dict[str, str] = {}
            owner_users: dict[str, str] = {}
            if task_ids:
                publisher_rows = (
                    db.query(
                        TaskInfoModel.task_id,
                        TaskInfoModel.owner_bot_id,
                        TaskInfoModel.owner_user_id,
                    )
                    .filter(TaskInfoModel.task_id.in_(task_ids))
                    .all()
                )
                publishers = {tid: oid for tid, oid, _ in publisher_rows if oid}
                owner_users = {tid: uid for tid, _, uid in publisher_rows if uid}

            records: list[BbsTaskOverviewRecord] = []
            for run, node in joined:
                run_rec = run.to_record()  # 已 JSON 解析 extend_props/acceptance_result
                node_rec = node.to_record()  # 已 JSON 解析 task_spec;status → Status
                records.append(
                    BbsTaskOverviewRecord(
                        task_id=run_rec.task_id,
                        node_id=run_rec.node_id,
                        run_mode=run_rec.run_mode,
                        retry=run_rec.retry,
                        assignee_id=run_rec.assignee,
                        status=node_rec.status,
                        acceptance_result=run_rec.acceptance_result,
                        extend_props={
                            **(run_rec.extend_props or {}),
                            **({"output": run_rec.output} if run_rec.output else {}),
                        },
                        relay_create_time=node_rec.gmt_create,
                        relay_begin_time=run_rec.gmt_create,
                        relay_end_time=run_rec.gmt_modified,
                        task_spec=node_rec.task_spec or {},
                        publisher=publishers.get(run_rec.task_id),
                        owner_user_id=owner_users.get(run_rec.task_id),
                        # publisher_name 由 TaskService._enrich_bbs_publisher_names 批量补(repo 不查 BotService)
                    )
                )
            return records, total
