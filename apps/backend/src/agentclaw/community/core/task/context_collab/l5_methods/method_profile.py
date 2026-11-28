"""L5 MethodProfile:代码内版本化声明式合同(ACCS-105 §3.1)。

代码即合同来源,不建持久化注册表、不建外部注册中心;四个核心
Profile 剧本与本地 MultiRollout@1.0 扩展(ACCS-301 Single / 302 Group / 303 Swarm / 304 Open)随包
声明,供 inspection capabilities 与 Task 3 组合矩阵对齐自检消费。
"""

from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)


@dataclass(frozen=True)
class MethodProfileRef:
    """Exact MethodProfile identity, not another Method contract or runtime object.

    The method/version field names preserve existing ProductInstance JSON.
    """

    method: str
    version: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(item, str) or not item.strip()
            for item in (self.method, self.version)
        ):
            raise ContextCollabValidationError(
                "Method profile reference needs non-empty method and version"
            )


@dataclass(frozen=True)
class MethodProfile:
    """L5 方法 Profile 声明;字段语义见 L5 method objects spec §3.1。

    - ``capabilities``:对外可声明的能力(snake_case 操作族语义);
    - ``primitives_used``:声明使用的 L4 原语子集;
    - ``participation_model``:"selected" | "roster" | "stage" | "admission"
      四选一;
    - ``composable_relations``:该 Profile 作为父方法允许的组合关系
      (小写关系 token,与 spec §3.3 组合矩阵行一致)。
    """

    method_kind: str
    profile_version: str = "0.2"
    capabilities: tuple[str, ...] = ()
    primitives_used: tuple[str, ...] = ()
    participation_model: str = ""
    composable_relations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # Definitions and their persisted references share the exact identity.
        MethodProfileRef(self.method_kind, self.profile_version)

    @property
    def ref(self) -> MethodProfileRef:
        return MethodProfileRef(self.method_kind, self.profile_version)


# 顶层产品显式声明 Single / Open / Open + Group / Open + Single;
# Group/Open/Swarm 裸值经嵌套 bind 进入,此处四者均为核心方法剧本。
METHOD_PROFILE_REGISTRY: dict[str, MethodProfile] = {
    # Local extension; existing ACCS core contracts remain pinned to @0.2.
    "MultiRollout": MethodProfile(
        method_kind="MultiRollout",
        profile_version="1.0",
        capabilities=(
            "multi_rollout.run.start",
            "multi_rollout.run.stop",
            "multi_rollout.candidate.observe",
            "multi_rollout.selection.select",
            "multi_rollout.adjustment.propose",
        ),
        primitives_used=(
            "Task",
            "Message",
            "Artifact",
            "Decision",
            "Activity",
            "Query",
        ),
        participation_model="body",
        composable_relations=("nest",),
    ),
    # ACCS-301 Single:选中者执行;escalate 至 Group/Swarm 纳入可组合关系。
    "Single": MethodProfile(
        method_kind="Single",
        capabilities=(
            "single.selection.select",
            "single.selection.replace_atomic",
            "single.selection.release",
            "single.selection.delegate",
        ),
        primitives_used=(
            "Task",
            "Message",
            "Artifact",
            "Activity",
            "Decision",
            "Query",
        ),
        participation_model="selected",
        composable_relations=("nest", "delegate_to", "support", "escalate"),
    ),
    # ACCS-302 Group:名册治理;可 escalate 至 Swarm。
    "Group": MethodProfile(
        method_kind="Group",
        capabilities=(
            "group.roster.invite",
            "group.roster.add_member",
            "group.roster.suspend_member",
            "group.roster.resume_member",
            "group.roster.leave",
            "group.roster.remove_member",
            "group.roster.replace_member_atomic",
            "group.roster.change_role",
        ),
        primitives_used=(
            "Task",
            "Message",
            "Artifact",
            "Decision",
            "Query",
            "Activity",
        ),
        participation_model="roster",
        composable_relations=(
            "nest",
            "delegate_to",
            "support",
            "parallelize",
            "escalate",
        ),
    ),
    # ACCS-303 Swarm:阶段化并行;SWARM 已是最大参与规模,无 escalate 出边。
    "Swarm": MethodProfile(
        method_kind="Swarm",
        capabilities=(
            "swarm.plan.submit_candidate",
            "swarm.plan.validate",
            "swarm.plan.adopt",
            "swarm.plan.start",
            "swarm.plan.pause",
            "swarm.plan.resume",
            "swarm.plan.terminate",
            "swarm.stage.allocate",
            "swarm.stage.complete",
            "swarm.stage.fail",
            "swarm.execution_profile.declare",
        ),
        primitives_used=(
            "Task",
            "Message",
            "Artifact",
            "Decision",
            "Query",
            "Activity",
        ),
        participation_model="stage",
        composable_relations=("nest", "delegate_to", "support", "parallelize"),
    ),
    # ACCS-304 Open:机会发布 + 申请录取;仅参与式组合,无 escalate/parallelize。
    "Open": MethodProfile(
        method_kind="Open",
        capabilities=(
            "open.opportunity.publish",
            "open.opportunity.pause",
            "open.opportunity.reopen",
            "open.opportunity.close",
            "open.application.submit",
            "open.application.screen",
            "open.application.admit",
            "open.application.reject",
            "open.application.withdraw",
            "open.application.revoke",
        ),
        primitives_used=("Query", "Artifact", "Decision", "Message"),
        participation_model="admission",
        composable_relations=("nest", "delegate_to", "support"),
    ),
}


def get_profile(method: str, version: str = "0.2") -> MethodProfile:
    """Return the exact core contract; the default is fixed @0.2, never latest."""
    try:
        profile = METHOD_PROFILE_REGISTRY[method]
    except KeyError:
        raise ContextCollabValidationError(
            f"unknown method profile: {method!r}"
        ) from None
    if profile.profile_version != version:
        raise ContextCollabValidationError(
            f"unknown method profile version: {method!r}@{version}"
        )
    return profile
