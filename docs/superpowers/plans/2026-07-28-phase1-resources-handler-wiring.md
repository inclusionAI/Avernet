# Phase 1: Resources openapi_v1 4 个纯 DB handler 接通 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Subagent-driven (Task 3-5 已示范)。Steps use checkbox (`- [ ]`)。

**Goal:** 把 `openapi_v1/resources/router.py` 的 4 个纯 DB handler stub(`raise NotImplementedError`)接通真实 `ResourceService`——`GET /openapi/v1/bots/resources`(list)、`GET /openapi/v1/bots/resources/check-name`、`GET /openapi/v1/bots/resources/{resource_id}`(get)、`POST /openapi/v1/bots/resources`(create,**仅 LINK 分流**)。返回标准 `Envelope[T]`,factory 注入,**不碰 legacy helper**。

**Architecture:** 架构宪法 Rule 7(Core 库风格 + 薄 adapter)、Rule 6/8(分层边界 CI 强校验,adapter 不 import 别 adapter 私有 helper)、Rule 24(增量小步)。openapi_v1 handler 只做"协议转换 + service 调用 + Envelope 序列化",不持有 domain policy。注入 `ResourceServiceFactoryProtocol`(`api/resource_service.py:31`),per-request `factory.create(bot_id=...)`,与 legacy `/api/resources` 用同一 service 行为零变化。device_fs 类的(upload/download/preview/update/delete + create FOLDER/FILE)**留 Phase 3**(spec §8)。

**Tech Stack:** Python / FastAPI / pydantic v2 / fastapi_injector(`Injected`)。`openapi_v1/contracts.py` 已有 `Envelope[T]`/`Page[T]`/`PageParams`/`Code` 常量;`openapi_v1/dependencies.py` 有 `require_principal`(stub,本期占位)+ `resolve_avernet_tenant`(stub,本期返回 `teamclaw`)。

**前置依赖:** Phase 0(ac_resource 加列 + guard)已在分支 `rongzhi_0727` 落地(commit `e9f822f1`→`4f271964`),Phase 0 guard 保证这 4 个 handler 调 service 时 `ac_resource` 已按租户隔离(`teamclaw` 单租户下行为不变)。

**范围边界:**
- ✅ 接通:list / check-name / get / create(仅 LINK 分流)
- ⏸ Phase 3(含 device_fs,留后续):upload、download、preview、update、delete、create FOLDER/FILE 分流
- 不碰 legacy `adapters/http/resources/router.py`
- 不碰 `ResourceService` 既有方法签名(R2 `update_resource` 通用入口、R3 device_fs service 适配 = Phase 3 才做)

---

## File Structure

- **Modify** `src/backend/src/agentclaw/community/adapters/http/openapi_v1/resources/router.py` — 替换 4 个 stub handler body,新增模块级 `_to_openapi_resource()` 映射函数 + `_request_id()` helper
- **Possibly Modify** `src/backend/src/agentclaw/community/adapters/http/openapi_v1/resources/schemas.py` — 若 `ResourceCreate` 字段与 service `create_url_resource` 对不上需补注释;不破坏 served 契约
- **Create** `src/backend/tests/community/adapters/http/openapi_v1/test_resources_handlers.py` — handler 单测

---

## 关键事实(已代码级核实)

- 注入点:`ResourceServiceFactoryProtocol.create(*, bot_id) -> ResourceServiceProtocol`(`api/resource_service.py:31`),legacy `/api/resources/router.py:226` 同款用法
- `ResourceService` 真实方法(全 DB,无 device_fs):
  - `list_resources(resource_type, parent_path, user_id, status, limit, offset) -> List[Resource]`(`resource_service.py:201`)
  - `check_name_exists(name, resource_type, parent_path, user_id, exclude_id) -> bool`(`:152`,async)
  - `get_resource(resource_id) -> Optional[Resource]`(`:617`)
  - `create_url_resource(name, url, method, headers, parent_path, user_id, created_by) -> Resource`(`:543`,async)— **本期 create 只走这个**
- legacy `ResourceService.__init__`(`:102-150`)per-request 构造,接 `bot_id/entity_id/user_id/engine_type/entity_type`;`list_resources` 用 `bolt_id=self._bot_id` 过滤
- legacy `bot_id` 来源:`bot_id or ctx.bot_id or "default"`(`legacy router.py:191,234,261,313`);openapi_v1 handler 无 `ctx`(用 `require_principal` stub 本期 None),所以 `effective_bot_id = bot_id or "default"`
- openapi `Resource` schema(`resources/schemas.py:18-28`):`resource_id`/`name`/`type`/`source`/`url`/`size`/`gmt_create`/`gmt_modified`
- legacy `Resource`(`core/resources/models.py:34-49`):`id`/`resource_type`/`attributes` dict(含 path/url/size 等)/`gmt_created`/`gmt_modified`/`user_id`/`bolt_id`
- 映射 `_to_openapi_resource`:`resource_id=str(legacy.id)`、`type=legacy.resource_type`(Legacy ResourceType → openapi ResourceType,需处理 URL→LINK 归并见 R1a)、`url=legacy.url`(property)、`size=legacy.size`、`gmt_create=gmt_created.isoformat()`、`gmt_modified=gmt_modified.isoformat()`、`source=legacy.source`

---

## Task 1: 写 `_to_openapi_resource` 映射 + `_request_id` helper(纯函数,TDD RED→GREEN)

**Files:**
- Modify: `src/backend/src/agentclaw/community/adapters/http/openapi_v1/resources/router.py`(模块级加 2 个 helper)
- Test: `src/backend/tests/community/adapters/http/openapi_v1/test_resources_handlers.py`(新建)

- [ ] **Step 1: 写红测试(映射函数)**

```python
# tests/community/adapters/http/openapi_v1/test_resources_handlers.py
"""openapi_v1 resources handler 单测:映射 + handler 行为。"""
from datetime import datetime
import pytest

from agentclaw.community.core.resources.models import Resource, ResourceType as LegacyType
from agentclaw.community.adapters.http.openapi_v1.resources.router import _to_openapi_resource
from agentclaw.community.adapters.http.openapi_v1.resources.schemas import (
    ResourceType as OpenapiType, Resource as OpenapiResource,
)


def _legacy(**ov) -> Resource:
    base = dict(id=1, name="r", resource_type=LegacyType.LINK,
                 attributes={"url": "https://example.com", "link_type": "external"},
                 user_id="u", created_by="c", source="yuque", bolt_id="bot-a")
    base.update(ov)
    return Resource(**base)


def test_to_openapi_resource_maps_basic_fields():
    r = _legacy()
    o = _to_openapi_resource(r)
    assert o.resource_id == "1"
    assert o.name == "r"
    assert o.source == "yuque"
    assert o.type == OpenapiType.LINK  # legacy LINK → openapi LINK


def test_to_openapi_resource_url_flattened_from_attributes():
    r = _legacy()
    assert _to_openapi_resource(r).url == "https://example.com"


def test_to_openapi_resource_file_has_size():
    r = _legacy(resource_type=LegacyType.FILE, attributes={"path": "/p", "size": 42})
    o = _to_openapi_resource(r)
    assert o.type == OpenapiType.FILE
    assert o.size == 42


def test_to_openapi_resource_iso_timestamps():
    ts = datetime(2026, 7, 28, 10, 0)
    r = _legacy(gmt_created=ts, gmt_modified=ts)
    o = _to_openapi_resource(r)
    assert o.gmt_create == ts.isoformat()
    assert o.gmt_modified == ts.isoformat()
```

- [ ] **Step 2: 跑测试确认红**(`_to_openapi_resource` 还没定义 → ImportError/AttributeError 红)

Run: `cd /Users/rongzhi/PycharmProjects/Avernet/src/backend && uv run pytest tests/community/adapters/http/openapi_v1/test_resources_handlers.py -v`
Expected: FAIL(`_to_openapi_resource` 不存在)

- [ ] **Step 3: 实现 `_to_openapi_resource` + `_request_id`**

在 `openapi_v1/resources/router.py` 模块顶部(import 之后,`router = APIRouter(...)` 之前)加:

```python
from agentclaw.community.core.resources.models import Resource as _LegacyResource
from agentclaw.community.core.resources.models import ResourceType as _LegacyType
from agentclaw.community.tracer import get_trace_id as _get_trace_id  # 待核 tracer 入口名


def _request_id() -> str:
    """Mirror the X-Trace-Id response header as request_id (envelope field)."""
    try:
        return str(_get_trace_id())
    except Exception:
        return ""


# legacy ResourceType → openapi ResourceType(R1a: URL 归并进 LINK;NODE/DATABASE/API 不在 openapi 契约,
# 出现时映射为 None 由 caller 决定 —— 但 create handler 本期只支持 LINK,不会进这里)
_TYPE_MAP: dict[_LegacyType, "ResourceType"] = {
    _LegacyType.FILE: ResourceType.FILE,
    _LegacyType.LINK: ResourceType.LINK,
    _LegacyType.URL: ResourceType.LINK,  # R1a: URL 归并进 LINK
    _LegacyType.FOLDER: ResourceType.FOLDER if hasattr(ResourceType, "FOLDER") else ResourceType.LINK,
}


def _to_openapi_resource(legacy: _LegacyResource) -> Resource:
    """Map a legacy Resource(domain pydantic)→ openapi Resource(public schema).

    Flattens type-specific attributes(url/size)to top-level fields; the storage
    location is never exposed (per public schema contract).
    """
    return Resource(
        resource_id=str(legacy.id) if legacy.id is not None else "",
        name=legacy.name,
        type=_TYPE_MAP.get(legacy.resource_type, ResourceType.LINK),
        source=legacy.source,
        url=legacy.url,            # property: attributes["url"] for URL/LINK
        size=legacy.size if legacy.resource_type == _LegacyType.FILE else None,
        gmt_create=legacy.gmt_created.isoformat() if legacy.gmt_created else "",
        gmt_modified=legacy.gmt_modified.isoformat() if legacy.gmt_modified else "",
    )
```

> **注意 `_LegacyType.FOLDER`**:legacy `ResourceType`(`core/resources/models.py:18-24`)枚举有 FILE/URL/NODE/LINK/DATABASE/API,**没有 FOLDER**。openapi 有 FOLDER。所以 `_TYPE_MAP` 的 FOLDER 行其实是 `hasattr` False → 退回 LINK。本 batch 不接 create FOLDER(handler 报 400),所以这个 mapping 只对读路径用,FILE/LINK/URL 是读路径主要类型,FOLDER 不会从 legacy 来。**实现时若 mypy/ruff 报 FOLDER 不在 enum,删掉那行 hasanattr 三元 + 把 _TYPE_MAP FOLDER 行删掉**,只留 FILE/LINK/URL 三映射。

> **`tracer` 入口名待核**:上面用了 `from agentclaw.community.tracer import get_trace_id`。**实现前先 grep 确认真实入口**:`grep -rn "def get_trace_id\|class.*Tracer" src/agentclaw/community/ plugins/community/tracer.py`(若不是 tracer.get_trace_id 而是 plugins 路径,改 import)。如果找不到统一入口,先 fallback `return ""`,handler 测试用 `_request_id` 时自己注入,不阻塞。

- [ ] **Step 4: 跑测试确认绿**

Run: `cd /Users/rongzhi/PycharmProjects/Avernet/src/backend && uv run pytest tests/community/adapters/http/openapi_v1/test_resources_handlers.py -v`
Expected: 4 个映射测试全绿。

- [ ] **Step 5: Commit**(不 commit,按用户要求"最终我自己搞" — 本 plan 所有 step 给出 commit 命令但**不执行 git add/commit**,留给用户)

```bash
# 不执行 — 仅供用户参考
cd /Users/rongzhi/PycharmProjects/Avernet/src/backend
git add src/agentclaw/community/adapters/http/openapi_v1/resources/router.py \
        tests/community/adapters/http/openapi_v1/test_resources_handlers.py
git commit -m "feat(backend): openapi_v1 resources — _to_openapi_resource mapper (Phase 1 Task 1)

Flattens legacy Resource → openapi Resource (id→resource_id, type map, url/size
from attributes). R1a: legacy URL → openapi LINK. Per arch Rule 7 (thin adapter)."
```

---

## Task 2: 接通 `GET /openapi/v1/bots/resources`(list)

**Files:** Modify `openapi_v1/resources/router.py` 的 `list_resources` handler(当前 `:31-39`)
**Test:** 同 Task 1 测试文件,加 list 测试

- [ ] **Step 1: 写 handler 单测红测试(用 TestClient 真请求)**

```python
@pytest.fixture
def app_with_resources(monkeypatch):
    """Build a FastAPI app with the resources router + a stub factory."""
    from agentclaw.community.api.resource_service import ResourceServiceFactoryProtocol
    from agentclaw.community.adapters.http.openapi_v1.resources import router as res_router
    # ... stub factory.create(bot_id=...) returns a service that returns a fixed [Resource]
    # 待核:用什么方式注入 stub factory(fastapi_injector override 或 monkeypatch 模块级 binding)
    # 实现时先读 tests/community/adapters/http/ 现有 openapi 测试怎么注入 service mock

# 待实现 참고:看 tests/community/adapters/http/resources/ 现有 fixture 形态
```

> **实现注:** list handler 测试要 mock service 返回。**先 grep 看 tests/community/adapters/http/ 有没有现成的 openapi_v1 handler 测试 fixture**(应该没有,因为 handler 全是 stub),没有就新建最小 fixture。**若 mock 注入方式在 backend 此前没范式可抄**,标 BLOCKED 报告。

- [ ] **Step 2: 跑确认红**

- [ ] **Step 3: 接通 handler body**

替换 `openapi_v1/resources/router.py:31-39` 的 `list_resources`:

```python
@router.get("", response_model=Envelope[Page[Resource]])
async def list_resources(
    page: PageParamsDep,
    principal: PrincipalDep,
    bot_id: str | None = None,
    type: ResourceType | None = None,
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Page[Resource]]:
    """List resources (filter + paginate)."""
    effective_bot_id = bot_id or "default"
    service = factory.create(bot_id=effective_bot_id)
    # openapi ResourceType(FILE/LINK/FOLDER)→ legacy ResourceType mapping needed
    # for the service filter; but legacy list_resources takes resource_type str.
    legacy_type = type.value if type else None
    items = service.list_resources(resource_type=legacy_type, bolt_id=effective_bot_id)
    # R1 reverse-map each → openapi; paginate
    openapi_items = [_to_openapi_resource(r) for r in items]
    page_items = openapi_items[(page.page - 1) * page.page_size : page.page * page.page_size]
    return Envelope(
        code=CODE_OK, message="OK",
        data=Page(total=len(openapi_items), items=page_items),
        request_id=_request_id(),
    )
```

> **注意 signature 改动:** 原 stub(:31-37)的 fn 参数是 `(page, principal, bot_id, type)`,无 `factory`。**加 `factory` 参数会改 served OpenAPI schema**(`factory` 是依赖注入不该进 schema)。**用 `fastapi_injector` 的 `Injected()` 不进 OpenAPI schema**(它是 Depends 形式)。**实现时确认 `Injected` 不出现在生成的 schema 里**(ruff/测试时验)。如果它进 schema 了,改用 `Depends` 包一层。

- [ ] **Step 4: 跑测试绿 + 全量无回归**

```
cd /Users/rongzhi/PycharmProjects/Avernet/src/backend
uv run pytest tests/community/adapters/http/openapi_v1/test_resources_handlers.py -v
uv run pytest tests/community/contracts/gateway/test_public_namespace.py -v  # namespace 不变式不能破
UV_DEFAULT_INDEX=https://pypi.org/simple uv run pytest tests/community -q  # 全量,只增不减 0 fail
```

- [ ] **Step 5: Commit 命令(不执行,参考用)**

```bash
git commit -m "feat(backend): openapi_v1 resources — GET list handler (Phase 1 Task 2)

Factory-injected ResourceService, paginates, maps via _to_openapi_resource.
Envelope[Page[Resource]] per public contract. No legacy helper (Rule 7)."
```

---

## Task 3: 接通 `GET /check-name`

同 Task 2 形态。handler body:

```python
@router.get("/check-name", response_model=Envelope[NameCheck])
async def check_resource_name(
    name: str, principal: PrincipalDep,
    type: ResourceType | None = None,
    bot_id: str | None = None,
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[NameCheck]:
    effective_bot_id = bot_id or "default"
    service = factory.create(bot_id=effective_bot_id)
    exists = await service.check_name_exists(name=name, resource_type=type.value if type else "file")
    return Envelope(code=CODE_OK, message="OK",
                    data=NameCheck(name=name, exists=exists), request_id=_request_id())
```

> 注意原 stub signature(:42-47)是 `(name, principal)`,加 `type`/`bot_id`/`factory`。`type`/`bot_id` 是 query 该进 schema OK,`factory` 不该进。

---

## Task 4: 接通 `GET /{resource_id}`(get)

```python
@router.get("/{resource_id}", response_model=Envelope[Resource])
async def get_resource(
    resource_id: str, principal: PrincipalDep,
    bot_id: str | None = None,
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Resource]:
    effective_bot_id = bot_id or "default"
    service = factory.create(bot_id=effective_bot_id)
    r = service.get_resource(resource_id)
    if r is None:
        # 404 via envelope(code=404000);或 raise HTTPException(404)
        raise HTTPException(status_code=404, detail="Resource not found")
    return Envelope(code=CODE_OK, message="OK",
                    data=_to_openapi_resource(r), request_id=_request_id())
```

> 404 处理:**先核 other openapi_v1 handler 的 404 范式**(bots group 如果有,照搬)。若 openapi 契约是 envelope {code:404000}而非 HTTP 404,改用 envelope。

---

## Task 5: 接通 `POST ""`(create,**仅 LINK 分流**)

```python
@router.post("", status_code=201, response_model=Envelope[Resource])
async def create_resource(
    body: ResourceCreate, principal: PrincipalDep,
    bot_id: str | None = None,
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
) -> Envelope[Resource]:
    if body.type != ResourceType.LINK:
        # Phase 1 只支持 LINK;FILE→upload(Phase 3),FOLDER→create_directory(Phase 3)
        if body.type == ResourceType.FILE:
            raise HTTPException(status_code=400, detail="Use POST /upload for file resources")
        raise HTTPException(status_code=501, detail=f"Create {body.type.value} not supported yet (Phase 3)")
    if not body.url:
        raise HTTPException(status_code=400, detail="url is required for link resources")
    effective_bot_id = bot_id or "default"
    service = factory.create(bot_id=effective_bot_id)
    r = await service.create_url_resource(name=body.name, url=body.url)
    return Envelope(code=CODE_CREATED, message="Created",
                    data=_to_openapi_resource(r), request_id=_request_id())
```

> `parent_id` 字段(在 ResourceCreate schema:`parent_id: str | None`)——legacy `create_url_resource` 接 `parent_path` 不是 `parent_id`。**映射**:`parent_path = body.parent_id`(语义近似,占位)。若 mypy 报或测试暴露歧义,记 follow-up 不是 blocker。

---

## Task 6: 全量验证 + served OpenAPI 契约测试

- [ ] **Step 1: served OpenAPI 4 个 handler 真出现 + 走 `Envelope` + `require_principal`**

```
cd /Users/rongzhi/PycharmProjects/Avernet/src/backend
uv run pytest tests/community/contracts/gateway/ -v
UV_DEFAULT_INDEX=https://pypi.org/simple uv run pytest tests/community -q
```

- [ ] **Step 2: ruff + mypy**

```
uv run ruff check src/agentclaw/community/adapters/http/openapi_v1/resources/
uv run mypy src/agentclaw/community/adapters/http/openapi_v1/resources/router.py
```

- [ ] **Step 3(不 commit):给用户参考的合并 commit**

```bash
git commit -m "feat(backend): openapi_v1 resources — wire 4 pure-DB handlers (Phase 1)

list/check-name/get/create-link per arch Rule 7 (thin adapter, factory-injected,
no legacy helper). create only LINK branch (FILE→upload, FOLDER→create_directory
in Phase 3). Envelope[T] + _to_openapi_resource field mapping. namespace invariant
+ full suite green."
```

---

## Self-Review

- spec §3.1.3 resources handler 表:本期覆盖 list/check-name/get/create **4 个**(create 仅 LINK);upload/download/preview/update/delete **留 Phase 3** ✓
- spec §1.4 R1a(url→LINK 归并):`_TYPE_MAP` URL→LINK ✓
- 架构 Rule 7:factory 注入、不 import legacy helper ✓
- spec §3.1.3 R2/R3:本期不碰(update_resource/device_fs 留 Phase 3)✓
- handler signature 加 `factory` 不进 OpenAPI schema:需用 `Injected` 形式验(Task 2 Step 3 注意项已标注)
- 真零 commit:本 plan 所有 step 给 commit 命令**不执行**,留给用户收尾 ✓

## 待核实(动笔前,implementer 跑)

1. `tracer` 入口名(`plugins/community/tracer.py` 或 `agentclaw.community.tracer`)— Task 1 Step 3
2. openapi_v1 handler 测试 fixture 注入方式(mark injection override)— Task 2 Step 1
3. 404 处理范式(envelope vs HTTPException)— Task 4
4. `Injected(factory)` 是否进 served schema — Task 2 Step 3
