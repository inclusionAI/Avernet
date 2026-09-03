"""BBS 任务列表 HTTP 路由契约测试:GET /api/v1/collaboration/tasks/bbs/list。

内部面(不经 spanner),与 /bbs/claim|attach|result 同处。adapter 只转协议(Rule 22):
手写 stub TaskServiceProtocol 返 canned ``BbsTaskOverviewRecord`` 分页 ``(records, total)``,
验证 envelope + DTO 解析(task_spec.metadata.title→title / goal.objective→goal /
goal.acceptances→acceptances / extend_props.assignee_name→assignee_name /
task_info.owner_bot_id→publisher),以及 page/page_size 分页透传与默认值。
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.router import router as task_internal_router
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.domain.models import Status
from agentclaw.community.core.task.repository.types import BbsTaskOverviewRecord

_TASK_SPEC = {
    "metadata": {"task_id": "bbs-1", "title": "BBS 任务标题", "instruction": "执行"},
    "context": {"background": "bg"},
    "goal": {
        "objective": "达成目标",
        "acceptances": [{"id": "a1", "description": "验收1"}],
    },
}


def _record(
    task_id: str = "bbs-1",
    title: str = "BBS 任务标题",
    *,
    status: Status = Status.RUNNING,
    extend_props: dict | None = None,
    task_spec: dict | None = None,
    publisher_name: str | None = None,
) -> BbsTaskOverviewRecord:
    spec = task_spec if task_spec is not None else {
        "metadata": {"task_id": task_id, "title": title, "instruction": "执行"},
        "context": {"background": "bg"},
        "goal": {
            "objective": "达成目标",
            "acceptances": [{"id": "a1", "description": "验收1"}],
        },
    }
    return BbsTaskOverviewRecord(
        task_id=task_id,
        node_id=f"n-{task_id}",
        run_mode="bbs",
        retry=0,
        assignee_id="asg-1",
        status=status,
        acceptance_result={"verdict": "PASS", "acceptances_metric": [], "gaps": []},
        extend_props={"assignee_name": "Alice"} if extend_props is None else extend_props,
        relay_create_time=datetime(2026, 9, 1, 10, 0, 0),
        relay_begin_time=datetime(2026, 9, 1, 10, 0, 1),
        relay_end_time=datetime(2026, 9, 1, 10, 5, 0),
        task_spec=spec,
        publisher="pub-1",
        publisher_name=publisher_name,
    )


def _canned_record() -> BbsTaskOverviewRecord:
    return _record("bbs-1")


class _StubTaskService:
    """按真实服务语义分页 + 过滤:``list_bbs_tasks(page, page_size, *, search_word, status)
    → (page_records, filtered_total)``。过滤模拟生产端:status 等值;search_word 对
    task_spec/extend_props 的 JSON 文本大小写不敏感包含(整列 like 的等价模拟)。"""

    def __init__(self, records: list[BbsTaskOverviewRecord]) -> None:
        self._records = records

    def list_bbs_tasks(
        self,
        page: int = 1,
        page_size: int = 20,
        *,
        search_word: str | None = None,
        status: str | None = None,
    ):
        rs = list(self._records)
        if status is not None:
            rs = [r for r in rs if str(r.status) == status]
        if search_word is not None:
            w = search_word.lower()
            rs = [
                r
                for r in rs
                if w
                in (
                    json.dumps(r.task_spec, ensure_ascii=False)
                    + json.dumps(r.extend_props or {}, ensure_ascii=False)
                ).lower()
            ]
        total = len(rs)
        offset = (page - 1) * page_size
        return rs[offset : offset + page_size], total


class _StubTaskServiceModule(Module):
    """仅绑定 TaskServiceProtocol → 手写 stub(返预设 record);/bbs/list 路由只依赖它。"""

    def __init__(self, records: list[BbsTaskOverviewRecord]) -> None:
        super().__init__()
        self._records = records

    @singleton
    @provider
    def task_service(self) -> TaskServiceProtocol:
        return _StubTaskService(self._records)  # type: ignore[return-value]


def _client(records: list[BbsTaskOverviewRecord]) -> TestClient:
    injector = Injector([_StubTaskServiceModule(records)])
    app = FastAPI()
    app.include_router(task_internal_router)
    attach_injector(app, injector)
    return TestClient(app)


@pytest.fixture
def client():
    return _client([_canned_record()])


def test_bbs_list_route_returns_envelope_with_parsed_fields(client):
    r = client.get("/api/v1/collaboration/tasks/bbs/list")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 200000
    page = body["data"]
    assert page["total"] == 1
    items = page["items"]
    assert len(items) == 1
    it = items[0]
    # ── SQL 直投字段 ──
    assert it["task_id"] == "bbs-1"
    assert it["node_id"] == "n-bbs-1"
    assert it["run_mode"] == "bbs"
    assert it["retry"] == 0
    assert it["assignee_id"] == "asg-1"
    assert it["status"] == "RUNNING"
    assert it["acceptance_result"] == {"verdict": "PASS", "acceptances_metric": [], "gaps": []}
    assert it["extend_props"] == {"assignee_name": "Alice"}
    assert it["task_spec"]["metadata"]["task_id"] == "bbs-1"
    assert it["publisher"] == "pub-1"
    # ── relay 时间三态(ISO 字符串)──
    assert it["relay_create_time"].startswith("2026-09-01T10:00:00")
    assert it["relay_begin_time"].startswith("2026-09-01T10:00:01")
    assert it["relay_end_time"].startswith("2026-09-01T10:05:00")
    # ── task_spec / extend_props 二次解析字段 ──
    assert it["title"] == "BBS 任务标题"
    assert it["goal"] == "达成目标"
    assert it["acceptances"] == [{"id": "a1", "description": "验收1"}]
    assert it["assignee_name"] == "Alice"


def test_bbs_list_route_empty_when_no_bbs():
    c = _client([])
    r = c.get("/api/v1/collaboration/tasks/bbs/list")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"] == 200000
    assert body["data"] == {"total": 0, "items": []}


def test_bbs_list_route_defaults_when_params_omitted():
    """page/page_size 均不传 → 缺省 page=1/page_size=20;行数 < page_size 时返回全部,total 同步。"""
    c = _client([_record("bbs-1"), _record("bbs-2"), _record("bbs-3")])
    r = c.get("/api/v1/collaboration/tasks/bbs/list")
    assert r.status_code == 200, r.text
    page = r.json()["data"]
    assert page["total"] == 3
    assert [it["task_id"] for it in page["items"]] == ["bbs-1", "bbs-2", "bbs-3"]


def test_bbs_list_route_paginates_by_page_and_page_size():
    """page/page_size 透传到服务,按 (page-1)*page_size 切片,total 恒为全量。"""
    c = _client([_record("bbs-1"), _record("bbs-2"), _record("bbs-3")])

    first = c.get("/api/v1/collaboration/tasks/bbs/list", params={"page": 1, "page_size": 2})
    assert first.status_code == 200, first.text
    page1 = first.json()["data"]
    assert page1["total"] == 3
    assert [it["task_id"] for it in page1["items"]] == ["bbs-1", "bbs-2"]

    second = c.get("/api/v1/collaboration/tasks/bbs/list", params={"page": 2, "page_size": 2})
    assert second.status_code == 200, second.text
    page2 = second.json()["data"]
    assert page2["total"] == 3
    assert [it["task_id"] for it in page2["items"]] == ["bbs-3"]


def test_bbs_list_route_page_beyond_range_empty_with_total():
    """页越界 → items=[] 但 total 真实。"""
    c = _client([_record("bbs-1"), _record("bbs-2")])
    r = c.get("/api/v1/collaboration/tasks/bbs/list", params={"page": 5, "page_size": 10})
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"total": 2, "items": []}


def test_bbs_list_route_rejects_invalid_page_params():
    """page<1 / page_size<1 / page_size>100 → 422(Query 校验),不进入服务。"""
    c = _client([_canned_record()])
    assert c.get("/api/v1/collaboration/tasks/bbs/list", params={"page": 0}).status_code == 422
    assert (
        c.get("/api/v1/collaboration/tasks/bbs/list", params={"page_size": 0}).status_code == 422
    )
    assert (
        c.get("/api/v1/collaboration/tasks/bbs/list", params={"page_size": 101}).status_code == 422
    )


# ── status 过滤 ──


def test_bbs_list_route_filters_by_status():
    """?status=RUNNING 只返回 RUNNING;total 为过滤后行数。"""
    c = _client([
        _record("bbs-run", status=Status.RUNNING),
        _record("bbs-done", status=Status.DONE),
        _record("bbs-run2", status=Status.RUNNING),
    ])
    r = c.get("/api/v1/collaboration/tasks/bbs/list", params={"status": "RUNNING"})
    assert r.status_code == 200, r.text
    page = r.json()["data"]
    assert page["total"] == 2
    assert sorted(it["task_id"] for it in page["items"]) == ["bbs-run", "bbs-run2"]


def test_bbs_list_route_status_lowercased_matches():
    """status 大小写归一:小写 running 也命中。"""
    c = _client([_record("bbs-1", status=Status.RUNNING)])
    r = c.get("/api/v1/collaboration/tasks/bbs/list", params={"status": "running"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] == 1


def test_bbs_list_route_status_not_filtered_when_omitted():
    """不传 status → 不按状态过滤,返回全部。"""
    c = _client([
        _record("bbs-1", status=Status.RUNNING),
        _record("bbs-2", status=Status.DONE),
    ])
    r = c.get("/api/v1/collaboration/tasks/bbs/list")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] == 2


def test_bbs_list_route_rejects_invalid_status():
    """status 非枚举值 → 400。"""
    c = _client([_canned_record()])
    assert (
        c.get("/api/v1/collaboration/tasks/bbs/list", params={"status": "NOPE"}).status_code == 400
    )


def test_bbs_list_route_rejects_multi_value_status():
    """逗号多值 status(RUNNING,DONE)→ 400,强制单值契约。"""
    c = _client([_canned_record()])
    assert (
        c.get(
            "/api/v1/collaboration/tasks/bbs/list", params={"status": "RUNNING,DONE"}
        ).status_code
        == 400
    )


# ── search_word 模糊匹配 ──


def test_bbs_list_route_filters_by_search_word_task_spec():
    """search_word 命中 task_spec(标题)→ 只返回匹配;total 为过滤后行数。"""
    c = _client([
        _record("bbs-1", task_spec={"metadata": {"title": "AlphaUnique"}}),
        _record("bbs-2", task_spec={"metadata": {"title": "BetaUnique"}}),
    ])
    r = c.get("/api/v1/collaboration/tasks/bbs/list", params={"search_word": "alphaunique"})
    assert r.status_code == 200, r.text
    page = r.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["task_id"] == "bbs-1"


def test_bbs_list_route_filters_by_search_word_extend_props():
    """search_word 命中 extend_props(assignee_name)→ 返回匹配(task_spec 不含该词)。"""
    c = _client([
        _record("bbs-1", extend_props={"assignee_name": "ZoeUnique"}),
        _record("bbs-2", extend_props={"assignee_name": "Other"}),
    ])
    r = c.get("/api/v1/collaboration/tasks/bbs/list", params={"search_word": "zoeunique"})
    assert r.status_code == 200, r.text
    page = r.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["task_id"] == "bbs-1"


def test_bbs_list_route_search_word_case_insensitive():
    """search_word 大小写不敏感。"""
    c = _client([_record("bbs-1", task_spec={"metadata": {"title": "MixedCaseTitle"}})])
    r = c.get("/api/v1/collaboration/tasks/bbs/list", params={"search_word": "mixedcasetitle"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] == 1


def test_bbs_list_route_search_word_no_match():
    """search_word 无命中 → total=0、items=[]。"""
    c = _client([_canned_record()])
    r = c.get("/api/v1/collaboration/tasks/bbs/list", params={"search_word": "zzznotfound"})
    assert r.status_code == 200, r.text
    assert r.json()["data"] == {"total": 0, "items": []}


def test_bbs_list_route_search_word_not_filtered_when_omitted():
    """不传 search_word → 不过滤,返回全部。"""
    c = _client([
        _record("bbs-1", task_spec={"metadata": {"title": "Alpha"}}),
        _record("bbs-2", task_spec={"metadata": {"title": "Beta"}}),
    ])
    r = c.get("/api/v1/collaboration/tasks/bbs/list")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] == 2


def test_bbs_list_route_search_word_blank_treated_as_omitted():
    """空白 search_word(空串/纯空格)→ 视为不过滤。"""
    c = _client([_record("bbs-1"), _record("bbs-2")])
    r = c.get("/api/v1/collaboration/tasks/bbs/list", params={"search_word": "   "})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["total"] == 2


def test_bbs_list_route_combines_status_and_search_word():
    """status + search_word 同时过滤:交集;total 同步。"""
    c = _client([
        _record("bbs-1", status=Status.RUNNING, task_spec={"metadata": {"title": "Alpha"}}),
        _record("bbs-2", status=Status.DONE, task_spec={"metadata": {"title": "AlphaBeta"}}),
        _record("bbs-3", status=Status.RUNNING, task_spec={"metadata": {"title": "Gamma"}}),
    ])
    r = c.get(
        "/api/v1/collaboration/tasks/bbs/list",
        params={"status": "RUNNING", "search_word": "alpha"},
    )
    assert r.status_code == 200, r.text
    page = r.json()["data"]
    assert page["total"] == 1
    assert page["items"][0]["task_id"] == "bbs-1"


# ── publisher_name 展示字段(stub 已模拟 service enrich)──


def test_bbs_list_route_returns_publisher_name_when_present():
    """record 带 publisher_name 时透出到 DTO(stub 模拟 service 已 enrich)。"""
    c = _client([_record("bbs-1", publisher_name="张三客服Bot")])
    r = c.get("/api/v1/collaboration/tasks/bbs/list")
    assert r.status_code == 200, r.text
    it = r.json()["data"]["items"][0]
    assert it["publisher"] == "pub-1"
    assert it["publisher_name"] == "张三客服Bot"


def test_bbs_list_route_publisher_name_none_when_absent():
    """record 无 publisher_name(降级)→ DTO.publisher_name=None。"""
    c = _client([_record("bbs-1")])  # publisher_name 默认 None
    r = c.get("/api/v1/collaboration/tasks/bbs/list")
    assert r.status_code == 200, r.text
    assert r.json()["data"]["items"][0]["publisher_name"] is None
