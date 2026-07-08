"""HTTP-level tests for ``engine.community.api.aicoding.skill_router``.

构建一个最小 FastAPI app 把 router 挂上来，通过 monkeypatch 替换 router 内部的
``_skill_service`` 工厂和 ``check_capability``——这样可以脱离 ``EngineManager``
单例与真正的 bash 插件，只关心 router 自己的"service 调用 + 结构规整 + 异常→HTTP
状态码"契约。

覆盖目标：
* ``GET /api/aicoding/skills``：
    - success：plugin 存在 / 不存在 two 种 skill、description="|" 原样保留、
      backend 节点杂字段被丢弃
    - 空 backends：200，backends={}
    - exit_code != 0：500 带 stderr
    - JSON 解析失败：500
    - backend value 非 dict / skill 非 dict / 缺 name：被过滤
"""
from __future__ import annotations

import importlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

# 同 aicoding_sessions 测试：``__init__`` 把 ``router`` 绑到 APIRouter 对象，
# 覆盖了同名的子模块属性，故走 importlib 拿真正的模块对象。
router_mod = importlib.import_module("engine.community.api.aicoding.skill_router")
router = router_mod.router


# ── fakes ───────────────────────────────────────────────────────────────────


@dataclass
class FakeSkillService:
    """记录调用 + 受配置驱动返回 / 抛错的 ``SkillListService`` 替身。"""

    return_payload: Any = None
    raise_exc: Optional[BaseException] = None
    calls: list[tuple] = field(default_factory=list)

    async def list_skills(self) -> dict:
        self.calls.append(("list_skills",))
        if self.raise_exc:
            raise self.raise_exc
        return self.return_payload if self.return_payload is not None else {}


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def skill_svc() -> FakeSkillService:
    return FakeSkillService()


@pytest.fixture
def client(monkeypatch, skill_svc: FakeSkillService) -> TestClient:
    """组装一个挂了 router 的 FastAPI app，并完成依赖替换。

    - ``check_capability`` 默认放行（返回 None）；如需测 501 单独覆盖。
    - ``_skill_service`` 返回 fixture 里的 fake。
    """
    monkeypatch.setattr(router_mod, "check_capability", lambda cap: None)
    monkeypatch.setattr(router_mod, "_skill_service", lambda: skill_svc)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


# ── fixtures data ───────────────────────────────────────────────────────────


# 精简自桌面 1.txt：覆盖 cc-plugin-cache(带 plugin) / cc-user-skills(无 plugin) /
# description="|" 原样保留 / backend 杂字段("meta") 三个要点。
SAMPLE_PAYLOAD: dict = {
    "backends": {
        "cc": {
            "skills": [
                {
                    "name": "aix",
                    "source": "cc-plugin-cache",
                    "description": "Run a cross-agent coding workflow with Aix.",
                    "path": "/home/admin/.claude/plugins/cache/aix/aix/1.2.3/skills/aix",
                    "plugin": {
                        "name": "aix",
                        "version": "1.2.3",
                        "marketplace": "aix",
                    },
                },
                {
                    "name": "aix-backend-test",
                    "source": "cc-user-skills",
                    "description": "|",
                    "path": "/home/admin/.claude/skills/aix-backend-test",
                },
            ],
            # backend 节点的杂字段，应在 schema 层被丢弃
            "meta": {"should": "be dropped"},
        },
    }
}


# ── /skills ─────────────────────────────────────────────────────────────────


def test_list_skills_success(client, skill_svc):
    skill_svc.return_payload = SAMPLE_PAYLOAD
    resp = client.get("/api/aicoding/skills")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert set(body["backends"].keys()) == {"cc"}

    cc = body["backends"]["cc"]
    # backend 节点只保留 skills，杂字段被丢弃
    assert set(cc.keys()) == {"skills"}
    skills = cc["skills"]
    assert len(skills) == 2

    # 带 plugin 的 skill
    aix = next(s for s in skills if s["name"] == "aix")
    assert aix["source"] == "cc-plugin-cache"
    assert aix["plugin"] == {
        "name": "aix",
        "version": "1.2.3",
        "marketplace": "aix",
    }
    # 不带 plugin 的 skill → plugin 为 None
    test_skill = next(s for s in skills if s["name"] == "aix-backend-test")
    assert test_skill["plugin"] is None
    # description="|" 原样保留，不做语义清洗
    assert test_skill["description"] == "|"


def test_list_skills_empty_backends(client, skill_svc):
    skill_svc.return_payload = {"backends": {}}
    resp = client.get("/api/aicoding/skills")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["backends"] == {}


def test_list_skills_missing_backends_key(client, skill_svc):
    # aix 返回里没有 backends 键 → 视为空，不应抛 KeyError
    skill_svc.return_payload = {"unrelated": "payload"}
    resp = client.get("/api/aicoding/skills")
    assert resp.status_code == 200
    assert resp.json()["backends"] == {}


def test_list_skills_exec_failure_returns_500(client, skill_svc):
    # exit_code != 0 → service 抛 HTTPException(500)
    skill_svc.raise_exc = HTTPException(
        status_code=500, detail="aix skill list failed: boom"
    )
    resp = client.get("/api/aicoding/skills")
    assert resp.status_code == 500
    assert "aix skill list failed: boom" in resp.json()["detail"]


def test_list_skills_parse_failure_returns_500(client, skill_svc):
    skill_svc.raise_exc = HTTPException(
        status_code=500, detail="Failed to parse aix output: bad json"
    )
    resp = client.get("/api/aicoding/skills")
    assert resp.status_code == 500
    assert "Failed to parse aix output" in resp.json()["detail"]


def test_list_skills_filters_malformed_entries(client, skill_svc):
    # backend value 非 dict / skill 非 dict / skill 缺 name 都应被过滤，不抛
    skill_svc.return_payload = {
        "backends": {
            "cc": {
                "skills": [
                    {"name": "commit", "source": "cc-user-skills"},
                    {"source": "no-name"},          # 缺 name → 丢弃
                    "not-a-dict",                   # 非 dict → 丢弃
                ]
            },
            "junk": "not-a-dict-backend",          # 非 dict backend → 丢弃
        }
    }
    resp = client.get("/api/aicoding/skills")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["backends"].keys()) == {"cc"}
    assert [s["name"] for s in body["backends"]["cc"]["skills"]] == ["commit"]
