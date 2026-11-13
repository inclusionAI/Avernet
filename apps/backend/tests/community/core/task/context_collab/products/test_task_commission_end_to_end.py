"""用户发布/查询 → 模拟 Bot 主动上报、完工自动提交 → 用户验收。

用户通过产品 Service API 操作和观察;模拟 Bot 通过签名 A2A HTTP
入站使用真实产品 Profile、仓储、Open 适配器和 G1/G4。Open 负责
参与录取,进度与成果提交属于 L4 Work,不是新增的 Open 方法。
仅模拟 Bot 执行、任务交接和网关签名;本地产物不上传,出站通知仍用
MockA2aDelivery,不声称验证真实通知接收、网关转发或 LLM 执行。
"""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from functools import wraps
import secrets
from uuid import uuid4

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Binder, Injector

from agentclaw.community.adapters.http.task import commission_a2a, commission_a2a_auth
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
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    ContextCollabAuthorizerProtocol,
)
from agentclaw.community.core.task.context_collab.l1_interoperate_deliver.delivery import (
    A2aDeliveryPort,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    OpenMethodOpsServiceProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepositoryProtocol,
)
from agentclaw.community.core.task.context_collab.products.task_commission import (
    service as commission_service_module,
)
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import (
    PublishTaskCommand,
    TaskAdvanceCommand,
    TaskCommissionCreatedView,
    TaskCommissionServiceProtocol,
    TaskDispatchPort,
    TaskMethodOpsPort,
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


_TEST_DIRECTORY = __name__.rsplit(".", 1)[0].replace(".", "/")


def _flow_log(directory: str, message: str) -> None:
    print(f"[commission-flow][{directory}] {message}", flush=True)


def _trace_call(monkeypatch, owner, name: str, description: str) -> None:
    """只观察真实调用,不替换业务结果,不打印参数/凭证/完整响应。"""
    original = getattr(owner, name)
    directory = original.__module__.removeprefix("agentclaw.community.")
    directory = directory.rsplit(".", 1)[0].replace(".", "/")
    label = original.__qualname__

    @wraps(original)
    def traced(*args, **kwargs):
        detail = description
        if name == "_advance":
            detail += f", action={args[1]}"
        elif name == "resolve_workflow_status":
            detail += (
                f", event={kwargs['event_kind']}, "
                f"from={getattr(kwargs['snapshot'], 'status', None)}"
            )
        _flow_log(directory, f"→ {label}: {detail}")
        try:
            result = original(*args, **kwargs)
        except Exception as exc:
            _flow_log(directory, f"! {label}: 失败 ({type(exc).__name__})")
            raise
        snapshot = getattr(result, "current_snapshot", None)
        status = getattr(result, "status", getattr(result, "state", None))
        if snapshot is not None:
            status = snapshot.status
        if isinstance(result, str):
            status = result  # resolve_workflow_status 的返回值。
        suffix = f", status={status}" if status is not None else ""
        _flow_log(directory, f"← {label}: 完成{suffix}")
        return result

    monkeypatch.setattr(owner, name, traced)


def _trace_runtime(monkeypatch, injector: Injector) -> None:
    """测试局部安装跟踪;记录实际进入/返回路径,用 pytest -s 实时观察。"""
    for protocol, operations in (
        (TaskCommissionServiceProtocol, {
            "publish_task": "用户发布代办,启动产品编排",
            "report_execution": "接收 Bot 的 start/progress/submit 上报并校验上下文",
            "_advance": "按产品 WorkflowProfile 推进 L4 Work 动作",
            "accept_task": "发布人验收成果",
            "list_tasks": "查询用户任务列表",
            "detail_task": "读取任务状态、正文及操作历史",
        }),
        (TaskMethodOpsPort, {"admit_designated_assignee": "编排指定 Bot 的 Open 参与录取"}),
        (OpenMethodOpsServiceProtocol, {
            "publish_opportunity": "L5 Open 发布参与机会",
            "submit_application": "L5 Open 由发布人代提交指定 Bot 的申请",
            "screen": "L5 Open 审核参与申请",
            "admit": "L5 Open 录取指定 Bot",
        }),
        (TaskDispatchPort, {"dispatch_task_info": "封装任务并交给 L1/A2A 交付端口"}),
        (A2aDeliveryPort, {"send_message": "本地 Mock 交付,不代表真实远端 Bot 收到通知"}),
        (ContextCollabAuthorizerProtocol, {
            "check_create": "G1 校验创建权限",
            "check_append": "G1 校验事件写入权限",
        }),
        (ContextCollabRepositoryProtocol, {
            "create_context": "创建并持久化 Collet 与初始任务快照",
            "append_event": "追加事件并持久化快照;返回已保存状态",
        }),
    ):
        implementation = type(injector.get(protocol))
        for name, description in operations.items():
            _trace_call(monkeypatch, implementation, name, description)
    for module, name, description in (
        (commission_a2a_auth, "verify_bot_principal_token", "验证签名 Bot 身份,不记录凭证"),
        (commission_a2a, "_execute", "A2A HTTP 入站转换命令并调用产品服务"),
        (commission_service_module, "prepare_product_spec", "L6 校验产品 Profile 并准备创建合同"),
        (commission_service_module, "validate_product_event", "G4 校验产品合同、角色及时序"),
        (commission_service_module, "resolve_workflow_status", "G4 状态机计算事件后的目标状态"),
    ):
        _trace_call(monkeypatch, module, name, description)


@pytest.fixture
def commission_runtime(db, monkeypatch):
    """真实产品 DI/SQL/HTTP;仅替换本地签名配置,出站使用默认 MockA2aDelivery。"""

    def local_database(binder: Binder) -> None:
        binder.bind(DatabasePlugin, to=db)

    injector = Injector([TaskPersistenceModule(), TaskModule(), local_database])
    app = FastAPI()
    app.include_router(commission_a2a.router)
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
    _trace_runtime(monkeypatch, injector)
    _flow_log("di/modules", "TaskModule + TaskPersistenceModule 装配完成,启用本地调用跟踪")
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
            _flow_log(
                _TEST_DIRECTORY,
                f"模拟 Bot → POST /a2a/task-commission: action={action}, "
                f"task={created.collet_id}, expected_revision={revision}",
            )
            response = self.client.post(
                "/a2a/task-commission", json=request, headers=self.headers,
            )
            assert response.status_code == 200, response.text
            result = response.json()["result"]["parts"][0]["data"]
            assert result["status"] == "SUCCEEDED", result
            revision = int(result["result_refs"][0]["version"])
            _flow_log(_TEST_DIRECTORY, f"模拟 Bot 收到 {action} 成功回执, revision={revision}")

        def report_progress(percent: int, note: str) -> None:
            send("progress", {"percent": percent, "note": note})

        send("start", {})
        # 不是 finally:执行或进度上报失败时不能伪造成功并自动提交。
        _flow_log(_TEST_DIRECTORY, "模拟 Bot 开始执行任务业务函数")
        artifacts = execute(report_progress)
        _flow_log(_TEST_DIRECTORY, "模拟 Bot 业务函数成功返回产物,自动触发 submit")
        send("submit", {"note": "报告已产出", "artifacts": artifacts})


def test_end_to_end_commission_flow(commission_runtime, tmp_path):
    """用户发布后,Bot 干活时主动上报,完工自动提交,用户检查成果后验收。"""
    service, working_bot = commission_runtime
    report_file = tmp_path / "2026-q3-security.md"
    artifact = report_file.as_uri()

    _flow_log(_TEST_DIRECTORY, "用户旅程开始:发布 → Bot 干活/上报/自动提交 → 用户验收")
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
        _flow_log(_TEST_DIRECTORY, "模拟 Bot 写完初稿,主动上报 60% 进度")
        report_progress(60, "初稿完成,校对中")

        progress_detail = service.detail_task(task_id)
        assert progress_detail.status == "in_progress"
        assert progress_detail.events[-1]["event_kind"] == "task.progress.updated"
        assert progress_detail.events[-1]["payload"] == {
            "note": "初稿完成,校对中", "percent": 60,
        }
        assert progress_detail.events[-1]["actor_id"] == BOT_ASSIGNEE
        assert all(event["event_kind"] != "task.submitted" for event in progress_detail.events)

        _flow_log(_TEST_DIRECTORY, "用户已看到执行中进度;模拟 Bot 继续校对,尚未提交")
        report_file.write_text("# Q3 安全报告\n\n安全事件汇总完成,已校对。", encoding="utf-8")
        return [artifact]

    working_bot.run(created, generate_report)

    # 4. 用户查看待验收任务及产物引用,确认结果后验收。
    [summary] = service.list_tasks()
    assert summary.collet_id == task_id
    review_detail = service.detail_task(task_id)
    assert review_detail.status == "reviewing"
    _flow_log(_TEST_DIRECTORY, "用户查询确认:成果已自动提交, status=reviewing,等待验收")
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
    _flow_log(_TEST_DIRECTORY, "用户旅程结束:成果验收通过, status=done")


def test_failed_bot_execution_does_not_auto_submit(commission_runtime):
    """Bot 已上报进度但执行失败时,不能将任务错误推进到待验收。"""
    service, working_bot = commission_runtime
    created = service.publish_task(
        PublishTaskCommand(title=TITLE, body=BODY, space=SPACE, assignee_agent_id=BOT_ASSIGNEE),
        HUMAN,
    )

    def failed_work(report_progress):
        report_progress(30, "正在收集安全事件")
        _flow_log(_TEST_DIRECTORY, "模拟 Bot 执行失败,不应自动触发 submit")
        raise RuntimeError("生成报告失败")

    with pytest.raises(RuntimeError, match="生成报告失败"):
        working_bot.run(created, failed_work)

    detail = service.detail_task(created.collet_id)
    assert detail.status == "in_progress"
    assert [event["event_kind"] for event in detail.events] == [
        "task.created", "user_interaction.posted", "task.started", "task.progress.updated",
    ]
    assert detail.events[-1]["actor_id"] == BOT_ASSIGNEE
