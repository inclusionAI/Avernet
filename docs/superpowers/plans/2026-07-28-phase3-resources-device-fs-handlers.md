# Phase 3: Resources openapi_v1 剩余 5 个 device_fs handler 接通

**Goal:** 接通 `openapi_v1/resources/router.py` 剩余 5 个 stub handler —— `PUT /{id}` update、`DELETE /{id}`、`POST /upload`、`GET /{id}/download`、`GET /{id}/preview`。全部涉及 device_fs。

**Architecture:** 延续 Phase 1 + 架构宪法 Rule 7(thinner adapter)。核心决策分两批:
- **Phase 3a(3 handler, service 已有 device_fs 入口)**:update/delete/upload —— `service.update_link_resource`/`delete_resource`/`upload_file` 已收 device_fs 参数。openapi handler 注入 `resolver`+`device_fs_dispatcher`+`bot_repo` 三 dep,解析 device_fs 后传 service。
- **Phase 3b(2 handler, service 无入口 — 补 service 方法)**:download/preview —— service 无 `download_resource`/`preview_resource`。**补 service 方法**让 adapter 薄(不选 adapter 层编排 download 流,违反 Rule 7 需 waiver)。

**Tech Stack:** Python / FastAPI / pydantic / fastapi_injector(`Injected`)。Phase 0 + Phase 1 已落地(ac_resource guard + 4 个纯 DB handler)。

**前置依赖:** Phase 1 的 `_to_openapi_resource`/`_request_id_from`/`ResourceServiceFactoryProtocol` 注入范式已验证。本 Phase 复用。

---

## device_fs 解析范式(openapi handler 共用)

全 sync,无 await。注入 4 dep:`factory`(service 用)、`resolver: DeviceContextResolver`、`device_fs_dispatcher: DeviceFilesystemDispatcher`、`bot_repo: BotRepository`。

```python
# 本期 principal=None,owner_id 从 ac_bots 取(Phase 0 guard 跨租户已过滤)
bot = bot_repo.get_bot(bot_id)             # 返回 dict;跨租户 → None
if not bot:
    raise HTTPException(status_code=404, detail=f"Bot {bot_id} not found")
owner_id = bot.get("owner_id") or bot_id   # fallback
ctx = resolver.resolve_for_bot(bot_id, owner_id)   # sync, 可能抛 DeviceNotBoundError
device_fs = device_fs_dispatcher.dispatch(ctx)       # sync
# 然后传给 service.delete_resource(id, ..., device_fs=device_fs)
```

> `device_info_lookup.get_device_info` 返回 (device_provider, sandbox_id) 给 `delete_resource`。openapi handler 也可调它(`from agentclaw.community.core.devices.services.device_info import get_device_info`)。**Phase 3a Task 2(delete)用**;Task 1(update)/Task 3(upload) 只需 device_fs。

---

## Task 1: PUT /{id} update(中, link 改写不需要 device_fs)

`service.update_link_resource` legacy 是 factory 注入(`:482`) + 部分 helper(yuque resolve)。**openapi 仅做基本 link 更新(不接 yuque resolve/yuque permission sync,留 follow-up)**——`service` 有 `update_link_resource`?查一下。

- core/resources/services/resource_service.py 有无 `update_link_resource`?`grep -n "update_link_resource\|def update" core/resources/services/resource_service.py`
- 若有,签名接什么?若只有 `repo.update(id, dict)`,本 Task 用 `service.update_link_resource()` 或 `factory.create(bot=...)._repo.update`(后者破坏 factory 抽象,**不可**——用 service 层方法)。
- 若 `service` 无 `update_link_resource`,**先看 legacy update_link_resource 用的什么 service 方法**,照搬最小集。

openapi handler body(初稿,实现时据 service 签名调整):

```python
@router.put("/{resource_id}", response_model=Envelope[Resource])
async def update_resource(
    resource_id: str,
    body: ResourceUpdate,           # {name?, url?}
    principal: PrincipalDep,
    bot_id: str | None = None,
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    request: Request = None,
) -> Envelope[Resource]:
    """Update a resource (link rename / url change). Phase 3a: link only."""
    effective_bot_id = bot_id or "default"
    service = factory.create(bot_id=effective_bot_id)
    update = {"name": body.name} if body.name is not None else {}
    if body.url is not None:
        update["url"] = body.url
    # service 入口待核:update_link_resource 或 repo.update
    # ... 调用,拿回 resource,404 if not found
    return Envelope(code=CODE_OK, message="OK",
                    data=_to_openapi_resource(r), request_id=_request_id_from(request))
```

## Task 2: DELETE /{id}(中, delete_resource 已接 device_fs)

```python
@router.delete("/{resource_id}", response_model=Envelope[Deleted])
async def delete_resource(
    resource_id: str, principal: PrincipalDep,
    bot_id: str | None = None,
    factory: ResourceServiceFactoryProtocol = Injected(ResourceServiceFactoryProtocol),
    bot_repo: BotRepository = Injected(BotRepository),
    resolver: DeviceContextResolver = Injected(DeviceContextResolver),
    device_fs_dispatcher: DeviceFilesystemDispatcher = Injected(DeviceFilesystemDispatcher),
    request: Request = None,
) -> Envelope[Deleted]:
    effective_bot_id = bot_id or "default"
    bot = bot_repo.get_bot(effective_bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    owner_id = bot.get("owner_id") or effective_bot_id
    device_provider, sandbox_id = get_device_info(effective_bot_id, owner_id, bot_repo)
    device_fs = device_fs_dispatcher.dispatch(resolver.resolve_for_bot(effective_bot_id, owner_id))
    service = factory.create(bot_id=effective_bot_id)
    ok = service.delete_resource(resource_id, device_provider=device_provider,
                                  sandbox_id=sandbox_id, device_fs=device_fs)
    if not ok:
        raise HTTPException(status_code=404, detail="Resource not found")
    return Envelope(code=CODE_OK, message="OK", data=Deleted(deleted=True),
                    request_id=_request_id_from(request))
```

## Task 3: POST /upload(中, upload_file 已接 device_fs)

openapi stub `POST /upload` body 是 `name: str` + `content: bytes(Annotated[...octet-stream])`。

```python
@router.post("/upload", status_code=201, response_model=Envelope[Resource])
async def upload_resource(
    principal: PrincipalDep,
    name: str,
    content: Annotated[bytes, Body(media_type="application/octet-stream")],
    bot_id: str | None = None,
    factory: ...,
    bot_repo: ..., resolver: ..., device_fs_dispatcher: ...,
    request: Request = None,
) -> Envelope[Resource]:
    effective_bot_id = bot_id or "default"
    bot = bot_repo.get_bot(effective_bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    owner_id = bot.get("owner_id") or effective_bot_id
    device_fs = device_fs_dispatcher.dispatch(resolver.resolve_for_bot(effective_bot_id, owner_id))
    service = factory.create(bot_id=effective_bot_id)
    r = await service.upload_file(
        data=content, filename=name, user_id=owner_id, created_by=owner_id,
        device_fs=device_fs,
    )
    return Envelope(code=CODE_CREATED, message="Created",
                    data=_to_openapi_resource(r), request_id=_request_id_from(request))
```

## Task 4(Phase 3b):补 service.download_resource + preview_resource → 接 GET /{id}/download + /preview

legacy `download`/`preview` 在 router 层直接 `device_fs.read_file`+`StreamingResponse`/`PreviewResponse`,**没走 service**。**补 2 个 service 方法**让 adapter 薄:

```python
# ResourceService 新增(core/resources/services/resource_service.py)
async def download_resource(self, resource_id: str, *, device_fs: "DeviceFileSystem") -> tuple[bytes, str] | None:
    """Download a FILE resource's raw bytes. Returns (bytes, content_type) or None if not a file/missing."""
    item = self._repo.get_by_id(resource_id)
    if not item: return None
    resource = Resource(**item)
    if not resource.is_file or resource.is_directory: return None
    # path from attributes; read via device_fs
    content = await device_fs.read_file(resource.path)
    mime = resource.mime_type or "application/octet-stream"
    return (content, mime)

async def preview_resource(self, resource_id: str, *, device_fs, max_size=...) -> dict | None:
    """Preview a FILE resource. Returns {content, size} or None."""
    # 类似 download,截图 + size 限制(对齐 legacy 的 413 逻辑)
```

openapi handlers:

```python
@router.get("/{resource_id}/download")
async def download_resource(resource_id, principal, bot_id, factory, bot_repo, resolver,
                            device_fs_dispatcher, request) -> Response:
    # 解析 device_fs(同 Task 2/3 范式)
    result = await service.download_resource(resource_id, device_fs=device_fs)
    if result is None:
        raise HTTPException(status_code=404, detail="File not found")
    content, mime = result
    return Response(content=content, media_type=mime)  # 不包 envelope(裸字节)

@router.get("/{resource_id}/preview", response_model=Envelope[Preview])
async def preview_resource(...):
    result = await service.preview_resource(resource_id, device_fs=device_fs)
    if result is None:
        raise HTTPException(status_code=404, detail="Cannot preview")
    return Envelope(code=CODE_OK, message="OK",
                    data=Preview(resource_id=resource_id, content_type=result["mime"],
                                content=result["content"]), request_id=_request_id_from(request))
```

## Task 5(Phase 3b cont):全量验证 + served schema

- `pytest tests/community/contracts/gateway/test_public_namespace.py` 绿(`Injected` 不进 schema,新加 dep 也不该进——验)
- 全量 + ruff + mypy

---

## 约束

- 不要 commit(用户收尾)
- 不要切分支
- Phase 3b 的 service 新方法必须加 conformance test(service 层单测,不只 handler)
- `Injected(...)` 不进 served schema(Phase 1 已验证多次)
- 跨租户 device_fs 解析由 ac_bots guard 兜底(bot_repo.get_bot 跨租户返回 None → 404)
