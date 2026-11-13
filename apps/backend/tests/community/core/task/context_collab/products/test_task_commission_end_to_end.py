"""任务代办端到端流程用例(用户故事四步,一个例子全链贯通)。

1. 用户 human 发布代办任务并指定 bot 执行;
2. 系统自动把任务信息送达指定 bot(交付缝捕获);
3. bot 执行任务:开工→汇报进度→产出产物(文件)提交;
4. 用户在平台上查询进度与最终产物信息(详情读面逐段可见)。
"""

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service import (
    TaskCommissionService,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    DispatchedTaskInfo,
    PublishTaskCommand,
    TaskAdvanceCommand,
    TaskDispatchPort,
)

from tests.community.core.task.context_collab.products.test_task_commission_publish import (
    _FakeRepo,
)

HUMAN = ActorContext(actor=PrincipalRef("human", "u_owner_001"), source="api")
BOT_ASSIGNEE = "bot_writer_001"
BOT = ActorContext(actor=PrincipalRef("bot", BOT_ASSIGNEE), source="api")
SPACE = SpaceRef(space_id="space_workspace_001", environment="prod")


class _CapturingDispatchPort(TaskDispatchPort):
    """交付缝 fake:记录系统送达给 bot 的每份任务信息。"""

    def __init__(self) -> None:
        self.dispatched: list[DispatchedTaskInfo] = []

    def dispatch_task_info(self, info: DispatchedTaskInfo) -> None:
        self.dispatched.append(info)


def _service(repo=_FakeRepo(), port=None):
    port = port or _CapturingDispatchPort()
    service = TaskCommissionService(
        collab_repo=repo,
        authorizer=StructuralContextCollabAuthorizer(),
        method_store=InMemoryMethodStore(),
        dispatch_port=port,
    )
    return service, repo, port


def _advance(collab_context_id, **overrides):
    fields = {
        "collab_context_id": collab_context_id,
        "note": None,
        "percent": None,
        "artifacts": [],
    }
    fields.update(overrides)
    return TaskAdvanceCommand(**fields)


def test_end_to_end_commission_flow():
    """四步用户故事,一例贯通(发布→送达→执行产出→查询)。"""
    service, repo, port = _service()

    # ── 1. 用户发布代办任务,指定 bot 执行 ────────────────────────────
    created = service.publish_task(
        PublishTaskCommand(
            title="生成季度安全报告",
            body="汇总 Q3 安全事件,输出 Markdown 报告文件。",
            space=SPACE,
            assignee_agent_id=BOT_ASSIGNEE,
        ),
        HUMAN,
    )
    collet_id = created.collet_id

    # ── 2. 系统自动把任务信息送达指定 bot(交付缝,无需 bot 轮询) ──────
    assert len(port.dispatched) == 1
    sent = port.dispatched[0]
    assert isinstance(sent, DispatchedTaskInfo)
    assert sent.collet_id == collet_id
    assert sent.title == "生成季度安全报告"
    assert sent.body == "汇总 Q3 安全事件,输出 Markdown 报告文件。"
    assert sent.assignee_agent_id == BOT_ASSIGNEE

    # ── 3. bot(凭送达的信息)执行任务:开工 → 汇报进度 → 提交产物文件 ──
    started = service.start_task(_advance(collet_id), BOT)
    assert started.status == "in_progress"

    progressing = service.report_progress(
        _advance(collet_id, note="初稿完成,校对中", percent=60), BOT
    )
    assert progressing.status == "in_progress"

    submitted = service.submit_task(
        _advance(
            collet_id,
            note="报告已产出",
            artifacts=["file://reports/2026-q3-security.md"],
        ),
        BOT,
    )
    assert submitted.status == "reviewing"

    # 产物文件经通用读路径持久:提交事件 payload 携带 artifacts 清单。
    submitted_event = repo._status[collet_id].events[-1]
    assert submitted_event.payload["artifacts"] == [
        "file://reports/2026-q3-security.md"
    ]

    # ── 4. 用户在平台上查询:进度信息与最终产物逐段可见 ────────────────
    in_progress_detail = service.detail_task(collet_id)
    assert in_progress_detail.collet_id == collet_id
    assert in_progress_detail.status == "reviewing"
    event_kinds = [e["event_kind"] for e in in_progress_detail.events]
    assert event_kinds == [
        "task.created",
        "user_interaction.posted",
        "task.started",
        "task.progress.updated",
        "task.submitted",
    ]
    progress_event = next(
        e for e in in_progress_detail.events
        if e["event_kind"] == "task.progress.updated"
    )
    assert progress_event["payload"]["percent"] == 60
    assert progress_event["payload"]["note"] == "初稿完成,校对中"
    assert progress_event["actor_id"] == BOT_ASSIGNEE

    # 用户验收,查询最终态:done + 产物清单在提交事件里可查。
    accepted = service.accept_task(_advance(collet_id, note="通过"), HUMAN)
    assert accepted.status == "done"

    final_detail = service.detail_task(collet_id)
    assert final_detail.status == "done"
    submit_event = next(
        e for e in final_detail.events if e["event_kind"] == "task.submitted"
    )
    assert submit_event["payload"]["artifacts"] == [
        "file://reports/2026-q3-security.md"
    ]
    assert final_detail.body is not None
    assert final_detail.body.content == "汇总 Q3 安全事件,输出 Markdown 报告文件。"
