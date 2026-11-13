"""L5 方法存储 Swarm 切片(Task 11,spec §4.3)。

键位纪律:五类对象键均五段与 selection 键同形——(avernet_tenant,
env, collab_context_id, method_binding_id, <对象id>),对象 id 只在
binding 域内充当幂等键,行按 collab_context_id 锚定,跨上下文互不可见。
spec §4.3 YAML 只给 ``SwarmPlan`` 落 method_binding_id 字段,其余四类
对象只携带域内引用(swarm_plan_ref / stage_ref / 无),因此本切片的
键位与 selection 切片的 ``collab_context_id`` 同款:**method_binding_id
一律显式入参**,不回填、不从引用反查。

幂等裁断与其他切片同款:同键同 ``to_dict`` 返回既有实例,同键异形态
抛 ``ContextCollabValidationError``;``update_*_state`` 覆盖式换新实例,
键不在库抛 ``ContextCollabNotFoundError``。plan/stage/allocation 的
权威状态由 swarm_plan / swarm_stage / swarm_allocation 三台 G4 机器
在服务层裁决(§8.2),store 只做窗口式替换;同 Stage 并行 ACTIVE
Allocation / lease 有效性的结构断言需要 plan 上下文,在服务层
(spec §4.3 / §8.1)。execution record 只增不改——Computation Plane
派发 P1 不实现(§2),record 仅由权威面操作创建/查询;execution
profile 声明即活,无状态列。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l5_methods.swarm_profile import (
    Stage,
    StageAllocation,
    SwarmExecutionProfile,
    SwarmExecutionRecord,
    SwarmPlan,
)

if TYPE_CHECKING:  # pragma: no cover - 仅供注解,type check 期解析
    from agentclaw.community.core.task.context_collab.persistence.method_store import (
        StoreAxes,
    )

# 与 selection 同形的五段键:<对象id> 段均为 (binding, 对象id) 复合。
_SwarmKey = tuple[str, str, str, str, str]


class SwarmSlice:
    """``MethodStoreProtocol`` 的 swarm plan/stage/allocation/
    execution profile/execution record 切片实现。"""

    # ------------------------------------------------------------------
    # swarm plan 切片(spec §4.3 权威面)
    # ------------------------------------------------------------------

    def insert_plan(
        self,
        axes: "StoreAxes",
        plan: SwarmPlan,
        collab_context_id: str,
    ) -> SwarmPlan:
        """幂等插入计划:同键同 ``to_dict`` 返回既有实例;同键异形态
        重复提交抛 ``ContextCollabValidationError``。plan 值对象自带
        method_binding_id,键段取自对象。"""
        key = self._id_key(
            axes, collab_context_id, plan.method_binding_id,
            plan.swarm_plan_id,
        )
        with self._lock:
            return self._insert_swarm_object_locked(
                self._plans, key, plan, "swarm plan"
            )

    def get_plan(
        self,
        axes: "StoreAxes",
        swarm_plan_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> SwarmPlan | None:
        with self._lock:
            return self._plans.get(
                self._id_key(
                    axes, collab_context_id, method_binding_id, swarm_plan_id
                )
            )

    def list_plans(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[SwarmPlan]:
        """按插入序返回该 axes + context + binding 下的全部计划,
        不做状态过滤。"""
        with self._lock:
            return [
                plan
                for key, plan in self._plans.items()
                if self._key_in_binding(key, axes, collab_context_id,
                                        method_binding_id)
            ]

    def update_plan_state(
        self,
        axes: "StoreAxes",
        plan: SwarmPlan,
        collab_context_id: str,
    ) -> None:
        """覆盖式换新计划实例(adopt/start/pause/resume/terminate 经其
        落值);键必须已存在,否则抛 ``ContextCollabNotFoundError``。"""
        key = self._id_key(
            axes, collab_context_id, plan.method_binding_id,
            plan.swarm_plan_id,
        )
        with self._lock:
            if key not in self._plans:
                raise ContextCollabNotFoundError(
                    f"swarm plan {plan.swarm_plan_id!r} not found on method "
                    f"binding {plan.method_binding_id!r} in collaboration "
                    f"context {collab_context_id!r}"
                )
            self._plans[key] = plan

    # ------------------------------------------------------------------
    # swarm stage 切片(spec §4.3 权威面)
    # ------------------------------------------------------------------

    def insert_stage(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        stage: Stage,
        collab_context_id: str,
    ) -> Stage:
        """幂等插入 Stage(SubmitPlanCandidate 落计划名下全部 Stage);
        同键同 ``to_dict`` 返回既有实例,同键异形态抛校验错。Stage 值
        对象不携带 binding(域内引用只有 swarm_plan_ref),method_binding_id
        显式入参充当键段。"""
        key = self._id_key(
            axes, collab_context_id, method_binding_id, stage.stage_id
        )
        with self._lock:
            return self._insert_swarm_object_locked(
                self._stages, key, stage, "swarm stage"
            )

    def get_stage(
        self,
        axes: "StoreAxes",
        stage_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> Stage | None:
        with self._lock:
            return self._stages.get(
                self._id_key(
                    axes, collab_context_id, method_binding_id, stage_id
                )
            )

    def list_stages(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[Stage]:
        """按插入序返回该 axes + context + binding 下的全部 Stage,
        不做状态过滤;计划内过滤由调用方按 ``swarm_plan_ref`` 自行投影。"""
        with self._lock:
            return [
                stage
                for key, stage in self._stages.items()
                if self._key_in_binding(key, axes, collab_context_id,
                                        method_binding_id)
            ]

    def update_stage_state(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        stage: Stage,
        collab_context_id: str,
    ) -> None:
        """覆盖式换新 Stage 实例(ready/allocate/run/complete/fail
        经其落值);键必须已存在,否则抛 ``ContextCollabNotFoundError``。"""
        key = self._id_key(
            axes, collab_context_id, method_binding_id, stage.stage_id
        )
        with self._lock:
            if key not in self._stages:
                raise ContextCollabNotFoundError(
                    f"swarm stage {stage.stage_id!r} not found on method "
                    f"binding {method_binding_id!r} in collaboration "
                    f"context {collab_context_id!r}"
                )
            self._stages[key] = stage

    # ------------------------------------------------------------------
    # swarm allocation 切片(spec §4.3 权威面)
    # ------------------------------------------------------------------

    def insert_allocation(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        allocation: StageAllocation,
        collab_context_id: str,
    ) -> StageAllocation:
        """幂等插入分配(AllocateStage 一次落 ACTIVE 分配);同键同
        ``to_dict`` 返回既有实例,同键异形态抛校验错。allocation 值
        对象只携带 stage_ref,method_binding_id 显式入参充当键段。同
        Stage 并行 ACTIVE 上限是服务层结构断言(需要 plan 的
        max_parallelism)。"""
        key = self._id_key(
            axes, collab_context_id, method_binding_id,
            allocation.allocation_id,
        )
        with self._lock:
            return self._insert_swarm_object_locked(
                self._allocations, key, allocation, "swarm allocation"
            )

    def get_allocation(
        self,
        axes: "StoreAxes",
        allocation_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> StageAllocation | None:
        with self._lock:
            return self._allocations.get(
                self._id_key(
                    axes, collab_context_id, method_binding_id, allocation_id
                )
            )

    def list_allocations(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[StageAllocation]:
        """按插入序返回该 axes + context + binding 下的全部分配,
        不做状态过滤。"""
        with self._lock:
            return [
                allocation
                for key, allocation in self._allocations.items()
                if self._key_in_binding(key, axes, collab_context_id,
                                        method_binding_id)
            ]

    def update_allocation_state(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        allocation: StageAllocation,
        collab_context_id: str,
    ) -> None:
        """覆盖式换新分配实例(release/expire/revoke 经其落值);键
        必须已存在,否则抛 ``ContextCollabNotFoundError``。"""
        key = self._id_key(
            axes, collab_context_id, method_binding_id,
            allocation.allocation_id,
        )
        with self._lock:
            if key not in self._allocations:
                raise ContextCollabNotFoundError(
                    f"swarm allocation {allocation.allocation_id!r} not "
                    f"found on method binding {method_binding_id!r} in "
                    f"collaboration context {collab_context_id!r}"
                )
            self._allocations[key] = allocation

    # ------------------------------------------------------------------
    # swarm execution profile 切片(spec §4.3,声明即活)
    # ------------------------------------------------------------------

    def insert_execution_profile(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        profile: SwarmExecutionProfile,
        collab_context_id: str,
    ) -> SwarmExecutionProfile:
        """幂等插入执行剖面(DeclareExecutionProfile);同键同 ``to_dict``
        返回既有实例,同键异形态抛校验错。profile 无状态列,不做
        update 替换。"""
        key = self._id_key(
            axes, collab_context_id, method_binding_id,
            profile.execution_profile_id,
        )
        with self._lock:
            return self._insert_swarm_object_locked(
                self._execution_profiles, key, profile,
                "swarm execution profile",
            )

    def get_execution_profile(
        self,
        axes: "StoreAxes",
        execution_profile_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> SwarmExecutionProfile | None:
        with self._lock:
            return self._execution_profiles.get(
                self._id_key(
                    axes, collab_context_id, method_binding_id,
                    execution_profile_id,
                )
            )

    def list_execution_profiles(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[SwarmExecutionProfile]:
        """按插入序返回该 axes + context + binding 下的全部执行剖面。"""
        with self._lock:
            return [
                profile
                for key, profile in self._execution_profiles.items()
                if self._key_in_binding(key, axes, collab_context_id,
                                        method_binding_id)
            ]

    # ------------------------------------------------------------------
    # swarm execution record 切片(spec §4.3,P1 只增不改)
    # ------------------------------------------------------------------

    def insert_execution_record(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        record: SwarmExecutionRecord,
        collab_context_id: str,
    ) -> SwarmExecutionRecord:
        """幂等插入执行记录(权威面 provenance 行);同键同 ``to_dict``
        返回既有实例,同键异形态抛校验错。Computation Plane 派发
        P1 不实现:record 只增不改,不做 update 替换(§2)。"""
        key = self._id_key(
            axes, collab_context_id, method_binding_id,
            record.execution_record_id,
        )
        with self._lock:
            return self._insert_swarm_object_locked(
                self._execution_records, key, record,
                "swarm execution record",
            )

    def get_execution_record(
        self,
        axes: "StoreAxes",
        execution_record_id: str,
        method_binding_id: str,
        collab_context_id: str,
    ) -> SwarmExecutionRecord | None:
        with self._lock:
            return self._execution_records.get(
                self._id_key(
                    axes, collab_context_id, method_binding_id,
                    execution_record_id,
                )
            )

    def list_execution_records(
        self,
        axes: "StoreAxes",
        method_binding_id: str,
        collab_context_id: str,
    ) -> list[SwarmExecutionRecord]:
        """按插入序返回该 axes + context + binding 下的全部执行记录
        (inspection conformance evidence 的投影来源)。"""
        with self._lock:
            return [
                record
                for key, record in self._execution_records.items()
                if self._key_in_binding(key, axes, collab_context_id,
                                        method_binding_id)
            ]

    # ------------------------------------------------------------------
    # 内部:键构造与锁内插入
    # ------------------------------------------------------------------

    @staticmethod
    def _id_key(
        axes: "StoreAxes",
        collab_context_id: str,
        method_binding_id: str,
        object_id: str,
    ) -> _SwarmKey:
        return (
            axes.avernet_tenant,
            axes.env,
            collab_context_id,
            method_binding_id,
            object_id,
        )

    @staticmethod
    def _key_in_binding(
        key: _SwarmKey,
        axes: "StoreAxes",
        collab_context_id: str,
        method_binding_id: str,
    ) -> bool:
        """五段键的租户/环境/上下文/binding 四段命中断言(list 投影用)。"""
        return (
            key[0] == axes.avernet_tenant
            and key[1] == axes.env
            and key[2] == collab_context_id
            and key[3] == method_binding_id
        )

    def _insert_swarm_object_locked(
        self,
        collection: dict,
        key: _SwarmKey,
        obj,
        kind_name: str,
    ):
        """锁内幂等插入:同键同 ``to_dict`` 返回既有实例,同键异形态抛
        ``ContextCollabValidationError``;调用方必须已持 store 锁。"""
        existing = collection.get(key)
        if existing is not None:
            if existing.to_dict() == obj.to_dict():
                return existing
            raise ContextCollabValidationError(
                f"idempotent re-submit of {kind_name} differs from the "
                "stored shape"
            )
        collection[key] = obj
        return obj
