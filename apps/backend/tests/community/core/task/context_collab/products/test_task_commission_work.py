"""任务代办产品层 Work 六动作单测(plan T2;spec §3/§5.2/裁决 J-K)。

覆盖(Review Focus 逐条):
- **状态推进非恒等**:六动作后快照 status 由机器派写——全链
  created→in_progress→reviewing→done 逐段断言;
- **时序门(裁决 J)**:越时序(created 上 submit、reviewing 上 start)与
  终态后(done/cancelled 上六动作)一律 ValidationError;
- **提交权(裁决 K)**:submit 仅指定 bot(executor 值匹配);发起人越权拒;
- **守卫**:submit 无 artifacts 拒(required_artifacts_present FAIL 响亮冒出);
- G1:陌生 actor 推进拒(principal/executor 双路径之外);
- 边界:外 profile 404、collet 不存在 404、CAS 409 透传。
"""

import pytest

from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabNotFoundError,
    ContextCollabValidationError,
    ContextCollabVersionConflict,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextStatusRecord,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service import (
    TaskCommissionService,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    PublishTaskCommand,
    TaskAdvanceCommand,
)

from tests.community.core.task.context_collab.products.test_task_commission_publish import (
    ASSIGNEE,
    BODY,
    PRINCIPAL,
    SPACE,
    TITLE,
    _FakeRepo,
    _actor,
    _bot_actor,
    _publish_command,
    _service,
)

def _advance(collab_context_id, **overrides):
    fields = {
        "collab_context_id": collab_context_id,
        "note": None,
        "artifacts": [],
    }
    fields.update(overrides)
    return TaskAdvanceCommand(**fields)


def _publish(service):
    return service.publish_task(_publish_command(), _actor())


class TestFullLifecycle:
    def test_full_lifecycle_walks_machine(self):
        """"发布→开工→进度→提交→验收" 全链, 快照 status 逐段机器派写。"""
        service, repo, _ = _service()
        created = _publish(service)
        collet_id = created.collet_id

        started = service.start_task(
            _advance(collet_id), _bot_actor(ASSIGNEE)
        )
        assert started.status == "in_progress"
        assert started.context_version == 3

        progress = service.report_progress(
            _advance(collet_id, note="过半"), _bot_actor(ASSIGNEE)
        )
        assert progress.status == "in_progress"

        submitted = service.submit_task(
            _advance(collet_id, note="done", artifacts=["doc://final-report"]),
            _bot_actor(ASSIGNEE),
        )
        assert submitted.status == "reviewing"

        accepted = service.accept_task(
            _advance(collet_id, note="验收通过"), _actor()
        )
        assert accepted.status == "done"

        record = repo._status[collet_id]
        assert record.current_snapshot.status == "done"
        kinds = [e.event_kind for e in record.events]
        assert kinds == [
            "task.created",
            "user_interaction.posted",
            "task.started",
            "task.progress.updated",
            "task.submitted",
            "task.accepted",
        ]

    def test_reject_loop_returns_to_in_progress(self):
        service, _, _ = _service()
        created = _publish(service)
        collet_id = created.collet_id
        service.start_task(_advance(collet_id), _bot_actor(ASSIGNEE))
        service.submit_task(
            _advance(collet_id, artifacts=["doc://draft"]), _bot_actor(ASSIGNEE)
        )

        rejected = service.reject_task(
            _advance(collet_id, note="返工"), _actor()
        )
        assert rejected.status == "in_progress"

        # 打回后 bot 可再提交。
        resubmitted = service.submit_task(
            _advance(collet_id, artifacts=["doc://draft-v2"]),
            _bot_actor(ASSIGNEE),
        )
        assert resubmitted.status == "reviewing"

    def test_cancel_from_created(self):
        service, _, _ = _service()
        created = _publish(service)

        cancelled = service.cancel_task(
            _advance(created.collet_id, note="不要了"), _actor()
        )
        assert cancelled.status == "cancelled"


class TestSequencingGate:
    """裁决 J:时序门先于一切(终态与越序不落事件)。"""

    def test_advance_rejected_out_of_order(self):
        service, repo, _ = _service()
        created = _publish(service)
        collet_id = created.collet_id

        with pytest.raises(ContextCollabValidationError, match="cannot advance"):
            service.submit_task(
                _advance(collet_id, artifacts=["doc://x"]),
                _bot_actor(ASSIGNEE),
            )
        with pytest.raises(ContextCollabValidationError, match="cannot advance"):
            service.accept_task(_advance(collet_id), _actor())
        # 越序未落任何事件。
        assert len(repo.append_calls) == 1  # 仅首帖正文

    def test_advance_rejected_out_of_order_from_reviewing(self):
        """plan Focus #2 点名:reviewing 上 start 越序拒。"""
        service, _, _ = _service()
        created = _publish(service)
        collet_id = created.collet_id
        service.start_task(_advance(collet_id), _bot_actor(ASSIGNEE))
        service.submit_task(
            _advance(collet_id, artifacts=["doc://x"]), _bot_actor(ASSIGNEE)
        )

        with pytest.raises(ContextCollabValidationError, match="cannot advance"):
            service.start_task(_advance(collet_id), _bot_actor(ASSIGNEE))

    def test_advance_rejected_after_cancelled(self):
        """plan Focus #1 点名:cancelled 终态上动作全拒。"""
        service, _, _ = _service()
        created = _publish(service)
        collet_id = created.collet_id
        service.cancel_task(_advance(collet_id), _actor())

        for action, actor in (
            (service.start_task, _bot_actor(ASSIGNEE)),
            (service.report_progress, _bot_actor(ASSIGNEE)),
            (service.accept_task, _actor()),
            (service.cancel_task, _actor()),
        ):
            with pytest.raises(
                ContextCollabValidationError, match="cannot advance"
            ):
                action(_advance(collet_id), actor)

    def test_advance_rejected_after_terminal(self):
        service, _, _ = _service()
        created = _publish(service)
        collet_id = created.collet_id
        service.start_task(_advance(collet_id), _bot_actor(ASSIGNEE))
        service.submit_task(
            _advance(collet_id, artifacts=["doc://x"]), _bot_actor(ASSIGNEE)
        )
        service.accept_task(_advance(collet_id), _actor())

        for action in (
            service.start_task,
            service.report_progress,
        ):
            with pytest.raises(ContextCollabValidationError, match="cannot advance"):
                action(_advance(collet_id), _bot_actor(ASSIGNEE))
        with pytest.raises(ContextCollabValidationError, match="cannot advance"):
            service.accept_task(_advance(collet_id), _actor())

        # cancel 也拒于终态。
        with pytest.raises(ContextCollabValidationError, match="cannot advance"):
            service.cancel_task(_advance(collet_id), _actor())


class TestGuardsAndRoles:
    def test_submit_rejects_principal(self):
        """裁决 K:提交权仅指定 bot;发起人越权拒。"""
        service, repo, _ = _service()
        created = _publish(service)
        collet_id = created.collet_id
        service.start_task(_advance(collet_id), _bot_actor(ASSIGNEE))

        with pytest.raises(ContextCollabValidationError, match="executor"):
            service.submit_task(
                _advance(collet_id, artifacts=["doc://x"]), _actor()
            )
        assert repo._status[collet_id].current_snapshot.status == "in_progress"

    def test_submit_requires_artifacts(self):
        """守卫 required_artifacts_present FAIL 必须响亮冒出。"""
        service, repo, _ = _service()
        created = _publish(service)
        collet_id = created.collet_id
        service.start_task(_advance(collet_id), _bot_actor(ASSIGNEE))

        with pytest.raises(ContextCollabValidationError, match="artifacts"):
            service.submit_task(_advance(collet_id), _bot_actor(ASSIGNEE))
        assert repo._status[collet_id].current_snapshot.status == "in_progress"

    def test_accept_rejects_bot_actor(self):
        """spec §7 点名"bot 越权验收拒":executor 过 G1,机器守卫
        (actor_is_responsible_principal)响亮拒——验收权在发起人。"""
        service, _, _ = _service()
        created = _publish(service)
        collet_id = created.collet_id
        service.start_task(_advance(collet_id), _bot_actor(ASSIGNEE))
        service.submit_task(
            _advance(collet_id, artifacts=["doc://x"]), _bot_actor(ASSIGNEE)
        )

        with pytest.raises(ContextCollabValidationError, match="guard"):
            service.accept_task(_advance(collet_id), _bot_actor(ASSIGNEE))

    def test_advance_rejects_snapshotless_row(self):
        """执行者裁决:快照 None 的外部部分行无可裁决状态,写面刚性拒绝。"""
        service, repo, _ = _service()
        created = _publish(service)
        collet_id = "collet_snapshotless_01"
        import dataclasses

        record = repo._status[created.collet_id]
        repo._status[collet_id] = dataclasses.replace(
            record,
            collet=dataclasses.replace(record.collet, collet_id=collet_id),
            current_snapshot=None,
        )

        with pytest.raises(
            ContextCollabValidationError, match="no snapshot"
        ):
            service.start_task(_advance(collet_id), _bot_actor(ASSIGNEE))

    def test_stranger_actor_gets_g1_rejection_before_machine_guards(self):
        """G1 先于机器守卫(spec §5.2 权威管线;整支评审 Important#1):

        陌生 actor 的拒词必须是 G1 的授权话术("only the principal,
        executor…"),不得先撞机器守卫把内部 rule_id/规则语义泄露给
        本应 G1 最先拒掉的人。
        """
        service, _, _ = _service()
        created = _publish(service)

        with pytest.raises(
            ContextCollabValidationError, match="only the principal, executor"
        ):
            service.start_task(
                _advance(created.collet_id),
                _bot_actor("bot_not_assigned_999"),
            )

    def test_stranger_report_progress_rejected_by_g1(self):
        """进度事件的守卫(event_matches_task)不查 actor——陌生者防护
        完全依赖 G1,该路径必须有覆盖(评审 Important#1 补洞)。"""
        service, _, _ = _service()
        created = _publish(service)
        service.start_task(_advance(created.collet_id), _bot_actor(ASSIGNEE))

        with pytest.raises(
            ContextCollabValidationError, match="only the principal, executor"
        ):
            service.report_progress(
                _advance(created.collet_id, note="偷跑进度"),
                _bot_actor("bot_not_assigned_999"),
            )


class TestBoundary:
    def test_unknown_or_foreign_profile_not_found(self):
        service, repo, _ = _service()
        # 外 profile:直接以仓储合同造一个 bbs_public_discussion Collet。
        from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
            Collet,
            ColletState,
        )
        record = ContextStatusRecord(
            collet=Collet(
                collet_id="collet_foreign_todo_0001",
                initiator_ref=PRINCIPAL,
                state=ColletState.ACTIVE,
                scope=SPACE,
                current_event_id=None,
                version=1,
            ),
            context_spec=_bbs_spec(),
            current_snapshot=None,
            events=[],
        )
        repo._status["collet_foreign_todo_0001"] = record

        with pytest.raises(ContextCollabNotFoundError):
            service.start_task(
                _advance("collet_foreign_todo_0001"), _bot_actor(ASSIGNEE)
            )
        with pytest.raises(ContextCollabNotFoundError):
            service.start_task(
                _advance("collet_missing_404"), _bot_actor(ASSIGNEE)
            )

    def test_version_conflict_propagates(self):
        service, repo, _ = _service()
        created = _publish(service)

        original = repo.append_event

        def _boom(collab_context_id, request):
            raise ContextCollabVersionConflict("stale expected_version")

        repo.append_event = _boom
        try:
            with pytest.raises(ContextCollabVersionConflict):
                service.start_task(
                    _advance(created.collet_id), _bot_actor(ASSIGNEE)
                )
        finally:
            repo.append_event = original


def _bbs_spec():
    from agentclaw.community.core.task.context_collab.l4_context_primitives.collet import (
        ContextSpec,
    )

    return ContextSpec(
        space=SPACE,
        principal_ref=PRINCIPAL,
        method_kind="Open + Group",
        executor_ref=None,
        objective="公开讨论帖(外产品)",
        resource_refs=(),
        product_kind="bbs_public_discussion",
        workflow_kind="bbs_public_discussion",
    )
