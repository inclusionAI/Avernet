"""用户视角的代办产品闭环,仅通过产品 Service API 操作和观察。

用户发布/查询 → 模拟执行 Bot 开始/报告进度/提交 → 用户验收。
装配使用真实产品 Profile、仓储与 Open 服务;测试主体不读取 L5/L6
内部对象、交付信封或数据库。协议/存储细节由独立集成测试验证。
这是本地服务级验收,不是 HTTP/真实 Agent E2E;Bot 的执行和已知任务
ID 的交接由测试模拟,不验证通知接收、LLM 执行或产物文件上传。
"""

import pytest
from injector import Binder, Injector

from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import (
    SpaceRef,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    PublishTaskCommand,
    TaskAdvanceCommand,
    TaskCommissionServiceProtocol,
)
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.di.modules.task_persistence_module import TaskPersistenceModule
from agentclaw.community.plugin_api.database import DatabasePlugin

HUMAN = ActorContext(actor=PrincipalRef("human", "u_owner_001"), source="api")
BOT_ASSIGNEE = "bot_writer_001"
BOT = ActorContext(actor=PrincipalRef("bot", BOT_ASSIGNEE), source="api")
SPACE = SpaceRef(space_id="space_workspace_001", environment="test")
TITLE = "生成季度安全报告"
BODY = "汇总 Q3 安全事件,输出 Markdown 报告文件。"
ARTIFACT = "file://reports/2026-q3-security.md"


@pytest.fixture
def commission_service(db) -> TaskCommissionServiceProtocol:
    """基础设施装配留在 fixture,用户旅程只拿产品接口。

    使用隔离 SQLite 和 TaskModule 默认的 MockA2aDelivery,不替换
    产品服务、Profile Catalog、仓储、Open 适配器或 G1/G4。
    """

    def local_database(binder: Binder) -> None:
        binder.bind(DatabasePlugin, to=db)

    app = Injector([TaskPersistenceModule(), TaskModule(), local_database])
    return app.get(TaskCommissionServiceProtocol)


def test_end_to_end_commission_flow(commission_service):
    """用户发布并查询代办,执行者处理并提交,用户检查成果后验收。"""
    service = commission_service

    # 1. 用户发布:只填写产品业务字段。
    created = service.publish_task(
        PublishTaskCommand(
            title=TITLE,
            body=BODY,
            space=SPACE,
            assignee_agent_id=BOT_ASSIGNEE,
        ),
        HUMAN,
    )
    task_id = created.collet_id
    assert task_id
    assert created.title == TITLE
    assert created.status == "created"

    # 2. 用户在代办列表找到任务,打开详情确认发布内容。
    [summary] = service.list_tasks()
    assert (summary.collet_id, summary.title, summary.initiator_id) == (
        task_id,
        TITLE,
        HUMAN.actor.principal_id,
    )
    published_detail = service.detail_task(task_id)
    assert published_detail.title == TITLE
    assert published_detail.status == "created"
    assert published_detail.body.content == BODY

    # 3. 测试模拟执行 Bot 处理这项任务。任务 ID 由测试交接,
    #    不从 A2A 信封或内部存储提取,也不声称验证了真实通知接收。
    bot_detail = service.detail_task(task_id)
    assert bot_detail.body.content == BODY
    started = service.start_task(TaskAdvanceCommand(task_id), BOT)
    assert started.status == "in_progress"
    assert service.detail_task(task_id).status == "in_progress"

    progress = service.report_progress(
        TaskAdvanceCommand(task_id, note="初稿完成,校对中", percent=60), BOT
    )
    assert progress.status == "in_progress"
    progress_detail = service.detail_task(task_id)
    assert progress_detail.events[-1]["payload"] == {
        "note": "初稿完成,校对中",
        "percent": 60,
    }
    assert progress_detail.events[-1]["actor_id"] == BOT_ASSIGNEE

    submitted = service.submit_task(
        TaskAdvanceCommand(task_id, note="报告已产出", artifacts=[ARTIFACT]), BOT
    )
    assert submitted.status == "reviewing"

    # 4. 用户查看待验收任务及产物引用,确认结果后验收。
    [summary] = service.list_tasks()
    assert summary.collet_id == task_id
    review_detail = service.detail_task(task_id)
    assert review_detail.status == "reviewing"
    assert review_detail.events[-1]["payload"]["artifacts"] == [ARTIFACT]
    assert review_detail.events[-1]["actor_id"] == BOT_ASSIGNEE

    accepted = service.accept_task(TaskAdvanceCommand(task_id, note="通过"), HUMAN)
    assert accepted.status == "done"

    # 5. 用户再次打开详情,看到已完成的任务及完整操作历史。
    final_detail = service.detail_task(task_id)
    assert final_detail.title == TITLE
    assert final_detail.status == "done"
    assert final_detail.body.content == BODY
    assert [event["event_kind"] for event in final_detail.events] == [
        "task.created",
        "user_interaction.posted",
        "task.started",
        "task.progress.updated",
        "task.submitted",
        "task.accepted",
    ]
    assert final_detail.events[-2]["payload"]["artifacts"] == [ARTIFACT]
    assert final_detail.events[-1]["payload"]["note"] == "通过"
    assert final_detail.events[-1]["actor_id"] == HUMAN.actor.principal_id
