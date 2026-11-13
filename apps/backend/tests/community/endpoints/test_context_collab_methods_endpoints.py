"""L5 method 操作的内部 /api/v1 delivery tests(2026-11-12 spec §7)。

沿 ``test_context_collab_endpoints.py`` 同款 recipe:全新 ``FastAPI()``
只挂 ``context_collab_methods`` router,``get_current_user`` 覆写固定
``AuthenticatedUser``,injector 绑定四个真实服务协议(共享同一
``InMemoryMethodStore``,lifecycle 另依赖真 repository over
in-memory SQLite)——delivery 只测装订与错误映射,行为面在
core/task/context_collab 的服务单测覆盖。

覆盖(Task 14):
- 前缀逐字 pin:``/api/v1/collaboration/task`` 单数 task;
- 每资源族 1 happy:lifecycle(bind/activate/inspect)、Group(invite/
  members)、Swarm(declare/submit/validate/adopt)、Open(publish/
  submit application);
- RF #1:顶层裸值 method_kind(裸 Group/Swarm)经 HTTP 创建
  context 即拒(422 映射)——``create /context_collab`` 是 §6.1 面,
  这里验证的是裸值不能经嵌套 bind 顶替顶层之外,顶层入口的拒;
- 1 个 409 映射:group change-role 携带陈旧 roster revision →
  ``ContextCollabVersionConflict`` → 409 envelope;
- 错误语义:unknown binding → 404 而非 500。
"""

from contextlib import contextmanager

# Side-effect import: registers the 2 context collab models on Base.metadata.
import agentclaw.community.core.task.context_collab.persistence.models  # noqa: F401

# Match the real app's DI-first bootstrap (auth-first isolated imports are not
# a supported baseline; see knowledge_management/conftest.py for the precedent).
import agentclaw.community.di  # noqa: E402,F401
import pytest
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.api.task.context_collab_service import (
    ContextCollabServiceProtocol,
)
from agentclaw.community.core.base import Base
from agentclaw.community.core.task.context_collab.g1_secure_govern.authorization import (
    StructuralContextCollabAuthorizer,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.method_authorization import (
    MethodOpsAuthorizerProtocol,
    StructuralMethodOpsAuthorizer,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.lifecycle import (
    MethodLifecycleService,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.open.service import (
    OpenMethodService,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.single_group import (
    SingleGroupMethodService,
)
from agentclaw.community.core.task.context_collab.l5_methods.operations.swarm.service import (
    SwarmMethodService,
)
from agentclaw.community.core.task.context_collab.method_service_api import (
    GroupMethodOpsServiceProtocol,
    MethodLifecycleServiceProtocol,
    OpenMethodOpsServiceProtocol,
    SwarmMethodOpsServiceProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.method_store import (
    InMemoryMethodStore,
    MethodStoreProtocol,
)
from agentclaw.community.core.task.context_collab.persistence.repository import (
    ContextCollabRepository,
)
from agentclaw.community.core.task.context_collab.services.context_collab_service import (
    ContextCollabService,
)
from agentclaw.community.core.task.context_collab.services.method_bootstrap import (
    ProductMethodBindingRecovery,
)
from fastapi.testclient import TestClient
from injector import Injector
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from tests.community.core.task.context_collab.product_catalog import PRODUCT_CATALOG

PREFIX = "/api/v1/collaboration/task"  # spec §6.1: singular "task"

AUTHENTICATED_STAFF_ID = "u_1001"


class InMemorySqliteDB:
    """Minimal DatabasePlugin stand-in offering orm_session()."""

    def __init__(self, engine):
        self._factory = sessionmaker(bind=engine, autoflush=False)

    @contextmanager
    def orm_session(self):
        db = self._factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()


@pytest.fixture
def client_world():
    from agentclaw.community.adapters.http.task.context_collab import (
        router as context_collab_router,
    )
    from agentclaw.community.adapters.http.task.context_collab_methods import (
        router,
    )
    from fastapi import FastAPI
    from fastapi_injector import attach_injector

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    repo = ContextCollabRepository(
        InMemorySqliteDB(engine), product_catalog=PRODUCT_CATALOG
    )
    store = InMemoryMethodStore()
    authorizer = StructuralMethodOpsAuthorizer()
    lifecycle = MethodLifecycleService(
        store=store,
        authorizer=authorizer,
        collab_repo=repo,
        recovery=ProductMethodBindingRecovery(store, repo, PRODUCT_CATALOG),
    )
    services = {
        ContextCollabServiceProtocol: ContextCollabService(
            repository=repo,
            method_store=store,
            # §6.1 面用既有 ContextCollabAuthorizer 协议实现;L5 方法面
            # 授权器是并列接缝(spec §8.1),不互通。
            authorizer=StructuralContextCollabAuthorizer(),
            product_catalog=PRODUCT_CATALOG,
        ),
        MethodStoreProtocol: store,
        MethodOpsAuthorizerProtocol: authorizer,
        MethodLifecycleServiceProtocol: lifecycle,
        GroupMethodOpsServiceProtocol: SingleGroupMethodService(
            store=store,
            authorizer=authorizer,
            collab_repo=repo,
            lifecycle=lifecycle,
        ),
        SwarmMethodOpsServiceProtocol: SwarmMethodService(
            store=store,
            authorizer=authorizer,
            collab_repo=repo,
        ),
        OpenMethodOpsServiceProtocol: OpenMethodService(
            store=store,
            authorizer=authorizer,
            collab_repo=repo,
        ),
    }

    app = FastAPI()
    app.include_router(context_collab_router)  # §6.1 create/append/status 面
    app.include_router(router)

    def authenticated_user():
        return AuthenticatedUser(
            id="SSO", staffId=AUTHENTICATED_STAFF_ID, operatorName="login"
        )

    app.dependency_overrides[get_current_user] = authenticated_user
    injector = Injector(auto_bind=False)
    for binding, impl in services.items():
        injector.binder.bind(binding, to=impl)
    attach_injector(app, injector)
    with TestClient(app) as client:
        yield client
    engine.dispose()


def create_body(method_kind: str = "Single") -> dict:
    """复用 create_context wire 示例;产品必须声明兼容的方法/工作流。"""
    product, workflow = {
        "Open + Group": ("public_discussion", "forum_public_discussion"),
        "Open": ("task_commission", "task_commission_goal"),
    }.get(method_kind, ("team_todo", "team_todo_execution"))
    return {
        "space": {"space_id": "space_workspace_001", "environment": "prod"},
        "principal_ref": {"principal_type": "human", "principal_id": "u_1001"},
        "product_kind": product,
        "workflow_kind": workflow,
        "method_kind": method_kind,
        "executor_ref": {"agent_type": "bot", "agent_id": "bot_coding_001"},
        "objective": "整理 Context 协作协议评审结论",
        "resource_refs": [],
    }


class TestMethodBindingsDelivery:
    def test_router_prefix_is_singular_task(self):
        """路由前缀 spec §7 pin:并入既有分组,单数 task 不变。"""
        from agentclaw.community.adapters.http.task.context_collab_methods import (
            router,
        )

        assert router.prefix == PREFIX

    def test_lifecycle_happy_path_bind_activate_inspect(self, client_world):
        client = client_world
        # 顶层:制造一个活 Collet(create_context 在既有 §6.1 面)。
        created = client.post(
            f"{PREFIX}/context_collab", json=create_body("Open + Group")
        )
        assert created.status_code == 200, created.text
        collab_id = created.json()["data"]["collab_context_id"]
        # 嵌套 bind(Open):HTTP happy。
        bound = client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings",
            json={
                "method_kind": "Open",
                "method_binding_id": "mb_open_http",
                "parent_scope_ref": "task_scope_root",
            },
        )
        assert bound.status_code == 200, bound.text
        assert bound.json()["data"]["binding"]["method_binding_id"] == ("mb_open_http")
        # activate + inspection(GET)。
        activated = client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/mb_open_http/activate",
            json={"reason": "HTTP happy"},
        )
        assert activated.status_code == 200, activated.text
        assert activated.json()["data"]["state"] == "ACTIVE"
        inspected = client.get(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings/mb_open_http"
        )
        assert inspected.status_code == 200, inspected.text
        inspection = inspected.json()["data"]
        assert inspection["binding"]["method_binding_id"] == "mb_open_http"
        assert "capabilities" in inspection
        assert "conformance_evidence" in inspection

    def test_group_operations_happy_path(self, client_world):
        client = client_world
        created = client.post(
            f"{PREFIX}/context_collab", json=create_body("Open + Group")
        )
        collab_id = created.json()["data"]["collab_context_id"]
        top_binding = f"mb_top_{collab_id}"
        invited = client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/{top_binding}/group/invite",
            json={"participant_ref": "bot_happy_001"},
        )
        assert invited.status_code == 200, invited.text
        assert invited.json()["data"]["state"] == "INVITED"
        added = client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/{top_binding}/group/members",
            json={"participant_ref": "bot_happy_001"},
        )
        assert added.status_code == 200, added.text
        assert added.json()["data"]["state"] == "ACTIVE"

    def test_swarm_operations_happy_path(self, client_world):
        client = client_world
        created = client.post(
            f"{PREFIX}/context_collab", json=create_body("Open + Group")
        )
        collab_id = created.json()["data"]["collab_context_id"]
        # 为 Top(Group/Open+Group)先建一条嵌套 Swarm binding 并激活。
        client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings",
            json={
                "method_kind": "Swarm",
                "method_binding_id": "mb_swarm_http",
                "parent_scope_ref": "task_scope_root",
            },
        )
        activated = client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/mb_swarm_http/activate",
            json={"reason": ""},
        )
        assert activated.status_code == 200
        profile = client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/mb_swarm_http/swarm/execution-profiles",
            json={
                "mode": "NATIVE_COMPUTE",
                "target_ref": "compute://runner/001",
                "supported_work_kinds": ["EXECUTE"],
                "input_contract_ref": "contract://in",
            },
        )
        assert profile.status_code == 200, profile.text
        assert profile.json()["data"]["state"] == "ACTIVE"
        submitted = client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/mb_swarm_http/swarm/plans",
            json={
                "task_ref": "task_swarm_http",
                "swarm_plan_id": "sp_http",
                "max_parallelism": 1,
                "max_depth": 2,
                "stages": [
                    {
                        "stage_id": "st1",
                        "name": "scan",
                        "completion_condition": {"found": True},
                    }
                ],
            },
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["data"]["state"] == "DRAFT"
        # validate 只读报告位(命名裁决见 router docstring)。
        report = client.get(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/mb_swarm_http/swarm/plans/sp_http/validate"
        )
        assert report.status_code == 200, report.text
        assert report.json()["data"]["valid"] is True
        adopted = client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/mb_swarm_http/swarm/plans/sp_http/adopt",
            json={"reason": ""},
        )
        assert adopted.status_code == 200, adopted.text
        assert adopted.json()["data"]["state"] == "READY"

    def test_open_operations_happy_path(self, client_world):
        client = client_world
        created = client.post(
            f"{PREFIX}/context_collab", json=create_body("Open + Group")
        )
        collab_id = created.json()["data"]["collab_context_id"]
        client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings",
            json={
                "method_kind": "Open",
                "method_binding_id": "mb_open_http",
                "parent_scope_ref": "task_scope_root",
            },
        )
        client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/mb_open_http/activate",
            json={"reason": ""},
        )
        published = client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/mb_open_http/open/opportunities",
            json={
                "scope_ref": "task_scope_open_http",
                "participation_type": "SINGLE_CONTRIBUTION",
                "opens_at": "2026-01-01T00:00:00+00:00",
                "opportunity_id": "opp_http",
            },
        )
        assert published.status_code == 200, published.text
        assert published.json()["data"]["state"] == "OPEN"
        application = client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/mb_open_http/open/opportunities/opp_http/applications",
            json={
                "applicant_agent_ref": "bot_contrib_001",
                "accountable_principal_ref": "u_member_001",
                "proposed_artifact_refs": ["artifact://reply"],
            },
        )
        assert application.status_code == 200, application.text
        assert application.json()["data"]["state"] == "SUBMITTED"

    def test_bare_top_level_method_kind_rejected_through_http(self, client_world):
        """裸 Group/Swarm 不能替代产品声明的顶层组合;Open 是代办合法组合。"""
        client = client_world
        for method_kind in ("Group", "Swarm"):
            rejected = client.post(
                f"{PREFIX}/context_collab", json=create_body(method_kind)
            )
            assert rejected.status_code == 422, rejected.text

    def test_incompatible_product_composition_maps_422(self, client_world):
        body = create_body("Single")
        body["method_kind"] = "Open + Group"
        rejected = client_world.post(f"{PREFIX}/context_collab", json=body)
        assert rejected.status_code == 422, rejected.text

    def test_open_commission_is_a_valid_top_level_composition(self, client_world):
        created = client_world.post(
            f"{PREFIX}/context_collab", json=create_body("Open")
        )
        assert created.status_code == 200, created.text

    def test_group_change_role_conflict_maps_409(self, client_world):
        """409 映射:陈旧 roster revision 的 change-role →
        ``ContextCollabVersionConflict`` → 409 envelope。"""
        client = client_world
        created = client.post(
            f"{PREFIX}/context_collab", json=create_body("Open + Group")
        )
        collab_id = created.json()["data"]["collab_context_id"]
        top_binding = f"mb_top_{collab_id}"
        client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/{top_binding}/group/invite",
            json={"participant_ref": "bot_conflict_001"},
        )
        client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/{top_binding}/group/members",
            json={"participant_ref": "bot_conflict_001"},
        )
        # 顶层投影建了 roster(principal/executor 两行,revision=3);
        # 携带陈旧 revision=1 写 → 409。
        conflicted = client.post(
            f"{PREFIX}/context_collab/{collab_id}/method-bindings"
            f"/{top_binding}/group/change-role",
            json={
                "participant_ref": "bot_conflict_001",
                "role_binding_refs": ["role_binding_x"],
                "expected_revision": 1,
            },
        )
        assert conflicted.status_code == 409, conflicted.text

    def test_unknown_binding_maps_404(self, client_world):
        client = client_world
        missing = client.get(
            f"{PREFIX}/context_collab/collet_ghost/method-bindings/mb_ghost"
        )
        assert missing.status_code == 404, missing.text
        body = missing.json()
        # envelope 结构:固定 message,不泄内部栈。
        assert "message" in body
