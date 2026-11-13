"""用户发布/查询 → 模拟 Bot 主动上报、完工自动提交 → 用户验收。

用户通过产品 Service API 操作和观察;模拟 Bot 通过签名 A2A HTTP
入站使用真实产品 Profile、仓储、Open 适配器和 G1/G4。Open 负责
参与录取,进度与成果提交属于 L4 Work,不是新增的 Open 方法。
仅模拟 Bot 执行、任务交接和网关签名;本地产物不上传,出站通知仍用
MockA2aDelivery,不声称验证真实通知接收、网关转发或 LLM 执行。
"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
import secrets
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Binder, Injector

from agentclaw.community.adapters.http.task import commission_a2a_auth
from agentclaw.community.adapters.http.task.commission_a2a import router
from agentclaw.community.core.gateway_principal import PrincipalVerifierConfig
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
    TaskCommissionCreatedView,
    TaskCommissionServiceProtocol,
)
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.di.modules.task_persistence_module import TaskPersistenceModule
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT

from tests.community.factories.task_commission import (
    BOT_ID as BOT_ASSIGNEE,
    OWNER,
    commission_report_request,
)

HUMAN = ActorContext(actor=PrincipalRef("human", "u_owner_001"), source="api")
SPACE = SpaceRef(space_id="space_workspace_001", environment="test")
TITLE = "生成季度安全报告"
BODY = "汇总 Q3 安全事件,输出 Markdown 报告文件。"


@pytest.fixture
def commission_runtime(db, monkeypatch):
    """真实产品 DI/SQL/HTTP;仅替换本地签名配置,出站使用默认 MockA2aDelivery。"""

    def local_database(binder: Binder) -> None:
        binder.bind(DatabasePlugin, to=db)

    injector = Injector([TaskPersistenceModule(), TaskModule(), local_database])
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, injector)
    key = secrets.token_hex(32)
    monkeypatch.setattr(
        commission_a2a_auth, "get_principal_verifier_config",
        lambda: PrincipalVerifierConfig(key, "backend", "gateway"),
    )
    now = datetime.now(timezone.utc)
    signed_principal = jwt.encode({
        "iss": "gateway", "aud": "backend", "iat": now,
        "exp": now + timedelta(minutes=5),
        "principals": [{"type": "bot", "tenant": DEFAULT_AVERNET_TENANT, "bot": {
            "bot_uuid": BOT_ASSIGNEE, "owner_id": OWNER, "app_id": 1,
            "agent_code": "writer", "tenant": DEFAULT_AVERNET_TENANT,
        }}],
    }, key, algorithm="HS256")
    with TestClient(app) as client:
        yield (
            injector.get(TaskCommissionServiceProtocol),
            SimulatedWorkingBot(client, signed_principal),
        )


class SimulatedWorkingBot:
    """测试用 Bot runtime:执行中提供上报工具,成功返回后才自动提交成果。"""

    def __init__(self, client: TestClient, signed_principal: str):
        self.client = client
        self.headers = {"X-Avernet-Principal": signed_principal}

    def run(
        self,
        created: TaskCommissionCreatedView,
        execute: Callable[[Callable[[int, str], None]], list[str]],
    ) -> None:
        revision = created.context_version

        def send(action: str, payload: dict) -> None:
            nonlocal revision
            request = commission_report_request(created, action, revision, payload)
            message = request["params"]["message"]
            operation = message["parts"][0]["data"]
            request["id"] = str(uuid4())
            message["messageId"] = str(uuid4())
            operation["operation_id"] = str(uuid4())
            operation["context"]["request_id"] = str(uuid4())
            response = self.client.post(
                "/a2a/task-commission", json=request, headers=self.headers,
            )
            assert response.status_code == 200, response.text
            result = response.json()["result"]["parts"][0]["data"]
            assert result["status"] == "SUCCEEDED", result
            revision = int(result["result_refs"][0]["version"])

        def report_progress(percent: int, note: str) -> None:
            send("progress", {"percent": percent, "note": note})

        send("start", {})
        # 不是 finally:执行或进度上报失败时不能伪造成功并自动提交。
        artifacts = execute(report_progress)
        send("submit", {"note": "报告已产出", "artifacts": artifacts})


def test_end_to_end_commission_flow(commission_runtime, tmp_path):
    """用户发布后,Bot 干活时主动上报,完工自动提交,用户检查成果后验收。"""
    service, working_bot = commission_runtime
    report_file = tmp_path / "2026-q3-security.md"
    artifact = report_file.as_uri()

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

    # 3. Bot 业务函数:写初稿 → 主动上报 → 继续执行 → 返回成果。
    #    用户在函数返回前就能看到进展。任务由测试交接,不验证真实出站通知。
    #    测试不直接调用 submit_task;由 Bot runtime 在业务成功后自动提交。
    def generate_report(report_progress):
        assert service.detail_task(task_id).status == "in_progress"
        report_file.write_text("# Q3 安全报告\n\n初稿,待校对。", encoding="utf-8")
        report_progress(60, "初稿完成,校对中")

        progress_detail = service.detail_task(task_id)
        assert progress_detail.status == "in_progress"
        assert progress_detail.events[-1]["event_kind"] == "task.progress.updated"
        assert progress_detail.events[-1]["payload"] == {
            "note": "初稿完成,校对中", "percent": 60,
        }
        assert progress_detail.events[-1]["actor_id"] == BOT_ASSIGNEE
        assert all(event["event_kind"] != "task.submitted" for event in progress_detail.events)

        report_file.write_text("# Q3 安全报告\n\n安全事件汇总完成,已校对。", encoding="utf-8")
        return [artifact]

    working_bot.run(created, generate_report)

    # 4. 用户查看待验收任务及产物引用,确认结果后验收。
    [summary] = service.list_tasks()
    assert summary.collet_id == task_id
    review_detail = service.detail_task(task_id)
    assert review_detail.status == "reviewing"
    assert "已校对" in report_file.read_text(encoding="utf-8")
    assert review_detail.events[-1]["payload"]["artifacts"] == [artifact]
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
    assert final_detail.events[-2]["payload"]["artifacts"] == [artifact]
    assert final_detail.events[-1]["payload"]["note"] == "通过"
    assert final_detail.events[-1]["actor_id"] == HUMAN.actor.principal_id


def test_failed_bot_execution_does_not_auto_submit(commission_runtime):
    """Bot 已上报进度但执行失败时,不能将任务错误推进到待验收。"""
    service, working_bot = commission_runtime
    created = service.publish_task(
        PublishTaskCommand(title=TITLE, body=BODY, space=SPACE, assignee_agent_id=BOT_ASSIGNEE),
        HUMAN,
    )

    def failed_work(report_progress):
        report_progress(30, "正在收集安全事件")
        raise RuntimeError("生成报告失败")

    with pytest.raises(RuntimeError, match="生成报告失败"):
        working_bot.run(created, failed_work)

    detail = service.detail_task(created.collet_id)
    assert detail.status == "in_progress"
    assert [event["event_kind"] for event in detail.events] == [
        "task.created", "user_interaction.posted", "task.started", "task.progress.updated",
    ]
    assert detail.events[-1]["actor_id"] == BOT_ASSIGNEE
