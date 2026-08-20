# Backend Harness 与 bcsfuse Gateway 接入设计

## 背景

Avernet 开源版已经通过 `src/gateway` 提供了统一的 OpenAPI 入口，Backend 的 Bots 公共面（`/openapi/v1/bots/*`）也已接入。但 Backend 内部的 harness 接口（`diagnose/preview/apply/rollback/dim-report/dim-history`）仍然只在 `/api/harness/*` 内部暴露，未通过 gateway 对外提供。

同时，Avernet 开源版 `src/bcsfuse` 已经接入 gateway（`bcsfuse-fusion` 和 `bcsfuse-workers` 两个 domain），但内部 ocb 版的 bcsfuse 与 Avernet 开源版是两个代码库，ocb gateway 尚未补配对应 upstream 和 schema。

本设计解决：
1. **Avernet 开源版**：把 harness 接口按现有 public surface 模式接入 gateway。
2. **ocb 内部版**：参照 Avernet 开源 gateway，为内部 bcsfuse 补全 gateway 配置。

## 范围与目标

### 纳入范围

- Avernet `src/backend`：新增 `/openapi/v1/harness/bots/{bot_id}/*` 公共面。
- Avernet `src/backend/scripts/dump_openapi.py`：增加按 path prefix 过滤的能力，使 `dump_and_publish.sh` 能分别产出 `bots.openapi.json` 和 `harness.openapi.json`。
- Avernet `src/gateway/scripts/dump_and_publish.sh`：新增 harness 的 dump + gate + publish 步骤。
- Avernet `src/gateway`：新增 `harness` domain、`harness.openapi.json`、route_security 规则与测试。
- ocb `src/gateway`：新增 `bcsfuse` server、两个 bcsfuse domain、route_security 规则与 schema 文件。

### 不纳入范围

- 修改内部 bcsfuse 的业务代码（只在其现有路由与 schema 不匹配时补充 parity route）。
- 修改或下线原有 `/api/harness/*` 内部接口。
- 前端调用适配。

## 总体架构

```
                    外部调用者
                         │
                         ▼
              ┌────────────────────┐
              │   Avernet gateway  │
              │  /openapi/v1/...   │
              │  (domain map)      │
              └─────────┬──────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
  /openapi/v1      /openapi/v1      /openapi/v1
  /bots/*          /harness/bots/*  /bcsfuse/*
        │               │               │
        ▼               ▼               ▼
   Avernet         Avernet          Avernet/Internal
   Backend         Backend          bcsfuse
   公共面          harness 公共面    (ocb 内部版)
```

## Avernet Backend Harness 公开面设计

### 暴露的接口

| 方法 | Gateway 路径                         | 内部 handler                         | 用途           |
|------|--------------------------------------|--------------------------------------|----------------|
| POST | `/openapi/v1/harness/bots/{bot_id}/diagnose`   | `POST /api/harness/diagnose`         | 启动诊断扫描   |
| POST | `/openapi/v1/harness/bots/{bot_id}/preview`    | `POST /api/harness/preview`          | 预览 patch 效果 |
| POST | `/openapi/v1/harness/bots/{bot_id}/apply`      | `POST /api/harness/apply`            | 应用 patch     |
| POST | `/openapi/v1/harness/bots/{bot_id}/rollback`   | `POST /api/harness/rollback`         | 回滚 patch     |
| GET  | `/openapi/v1/harness/bots/{bot_id}/dim-report` | `GET /api/harness/diagnose/dim-report` | 最新维度报告   |
| GET  | `/openapi/v1/harness/bots/{bot_id}/dim-history`| `GET /api/harness/diagnose/dim-history`| 维度历史       |

`bot_id` 迁移到 URL path；原内部接口的 `bot_id` 从 body/query 中移除或变为 path 参数注入。

### 文件结构

```
src/backend/src/agentclaw/community/adapters/http/openapi_v1/
├── __init__.py                       # mount harness_router
├── harness/
│   ├── __init__.py                   # 导出 harness_router
│   ├── router.py                     # 6 个 public endpoints
│   └── schemas.py                    # 适配 public surface 的 Request/Response DTO
```

### 鉴权与权限

每个 public handler 声明 `UserIdDep = Depends(require_user_id)`，从 gateway 签名的 `X-Avernet-Principal` 中解析 `user_id`，并保留现有的 collaborator 权限检查。

```python
@router.post("/bots/{bot_id}/diagnose", ...)
@envelope_errors
async def harness_diagnose(
    bot_id: str,
    request: HarnessDiagnoseRequest,
    principal_user: UserIdDep,
    # 现有 collaborator 权限拦截器
):
    ...
```

### 实现模式

每个 public endpoint 是薄 wrapper，复用现有 harness service 层：

```python
@router.post(
    "/bots/{bot_id}/diagnose",
    response_model=Envelope[DiagnoseStartResponse],
)
@envelope_errors
async def harness_diagnose(
    bot_id: str,
    body: HarnessDiagnoseRequest,
    user_id: UserIdDep,
    scanner: ContentScanner = Depends(Provide[HarnessContainer.content_scanner]),
    collaborator_checker: ... = Depends(...),
):
    await collaborator_checker.ensure(bot_id=bot_id, user_id=user_id)
    scan_id = await scanner.start_scan(
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        bot_id=bot_id,
        scan_type=body.scan_type,
        layer=body.layer,
        trigger_source="openapi",
        bot_publish_id=body.bot_publish_id,
    )
    return Envelope(data=DiagnoseStartResponse(...))
```

### 与原内部 `/api/harness` 的关系

- `/api/harness/*` 保持不变，继续供前端/内部使用。
- `/openapi/v1/harness/*` 是 gateway-only 的公开面。
- 两者共享 service 层和 repository 层，避免重复业务逻辑。
- `trigger_source` 使用 `"openapi"` 以区分内部 API 和 gateway 调用。

### OpenAPI schema 拆分

现有 `dump_openapi.py` 默认把全部 `/openapi/v1` 路径都输出到一个文件。为了让 gateway 的两个 domain（`bots` 和 `harness`）各自引用一份语义清晰的 schema，需要做最小改动：

1. 给 `dump_openapi.py` 增加可选 `--path-prefix` 参数，只保留以该前缀开头的路径（默认仍为 `/openapi/v1`，保持向后兼容）。
2. 给 `dump_and_publish.sh` 增加对 harness 的处理：

```bash
_dump_upstream backend --path-prefix /openapi/v1/bots
if ! $DRY_RUN; then
    _gate_and_publish \
        backend \
        "$SCHEMAS_DIR/bots.openapi.json" \
        "$TMPDIR/backend.openapi.json"
fi

_dump_upstream backend --path-prefix /openapi/v1/harness
if ! $DRY_RUN; then
    _gate_and_publish \
        harness \
        "$SCHEMAS_DIR/harness.openapi.json" \
        "$TMPDIR/backend.openapi.json"
fi
```

3. `gate_and_publish_openapi.py` 已经接受任意 artifact 路径，因此无需改动。

这样 `bots.openapi.json` 只含 `/openapi/v1/bots/*` 路径，`harness.openapi.json` 只含 `/openapi/v1/harness/*` 路径，各自组件也会被正确裁剪。

## ocb 内部版 bcsfuse Gateway 配置

内部 bcsfuse 是独立 FastAPI 服务，已有完整 OpenAPI spec，因此 ocb 侧只需补 gateway 配置。

### 需要改动的地方

| 文件 | 改动 |
|------|------|
| `ocb/src/gateway/configs/application.yaml` | 新增 `bcsfuse` server、两个 domain、`route_security` |
| `ocb/src/gateway/configs/application-{dev,gray,prepub,prod,sim,test}.yaml` | 同样新增，环境 overlay 中设置 `bcsfuse_server_url` |
| `ocb/src/gateway/configs/schemas/bcsfuse-fusion.openapi.json` | 从 Avernet 拷贝 |
| `ocb/src/gateway/configs/schemas/bcsfuse-workers.openapi.json` | 从 Avernet 拷贝（核对内部 bcsfuse 实际路由是否一致） |

### Gateway 配置示例

```yaml
upstreams:
  base_path: /openapi/v1

  servers:
    backend:
      base_url: "${backend_server_url}"
    bcsfuse:
      base_url: "${bcsfuse_server_url}"

  domains:
    bots:
      match: /openapi/v1/bots/**
      server: backend
      schema:
        source: file
        path: schemas/bots.openapi.json

    harness:
      match: /openapi/v1/harness/bots/**
      server: backend
      schema:
        source: file
        path: schemas/harness.openapi.json

    bcsfuse-fusion:
      match: /openapi/v1/bcsfuse/groups/**
      server: bcsfuse
      protocols: [http]
      rewrite:
        from: /openapi/v1/bcsfuse/groups
        to: /api/v1/groups
      schema:
        source: file
        path: schemas/bcsfuse-fusion.openapi.json
        refresh_seconds: 300

    bcsfuse-workers:
      match: /openapi/v1/bcsfuse/workers/**
      server: bcsfuse
      protocols: [http]
      rewrite:
        from: /openapi/v1/bcsfuse/workers
        to: /v1/workers
      schema:
        source: file
        path: schemas/bcsfuse-workers.openapi.json
        refresh_seconds: 300

route_security:
  "/openapi/v1/bots/**":
    user: required
  "/openapi/v1/harness/**":
    user: required
  "/openapi/v1/bcsfuse/**":
    user: required
```

### 内部 bcsfuse 与 Avernet 开源 schema 的差异核对

在 ocb 落地前，需要验证内部 bcsfuse 是否真实支持以下 upstream path：

| Gateway 路径 | Upstream path | 内部 bcsfuse 是否支持 |
|--------------|---------------|----------------------|
| `/openapi/v1/bcsfuse/groups/{group_id}/fuse` | `/api/v1/groups/{group_id}/fuse` | 检查 `fusion_routes.py` |
| `/openapi/v1/bcsfuse/workers/{worker_id}/config` | `/v1/workers/{worker_id}/config` | 检查 `worker_routes.py` |
| `/openapi/v1/bcsfuse/workers/config/batch` | `/v1/workers/config/batch` | 检查内部 batch 实现 |

如内部实现与 schema 不匹配，优先调整 ocb schema 以匹配内部 bcsfuse 实际接口；必要时在内部 bcsfuse 中补充缺失的 parity route。

## 错误处理与鉴权数据流

### Harness 鉴权数据流

```
外部调用者
   │
   ▼ 携带 user token
Avernet gateway
   │
   ├── 验证并解析 user identity
   ├── 用 PrincipalSigner 签名
   ├── 在转发请求中新增 HTTP header: X-Avernet-Principal
   ▼
Backend /openapi/v1/harness/bots/{bot_id}/diagnose
   │
   ├── require_principal 验证签名
   ├── require_user_id 提取 user_id
   ├── 检查 user 是否为 bot_id 的 collaborator
   ▼
   调用 ContentScanner / PatchEngine
```

### 错误处理

- Public harness router 使用 `@envelope_errors` 装饰器，返回统一的 envelope 错误格式。
- Gateway 会把 backend 响应原样转发给调用者。
- 鉴权失败由 `require_principal` 抛出 401/403，gateway 负责映射为统一鉴权错误。

### 兼容性

- `harness.openapi.json` 为新增文件，无 break risk。
- 后续变更需经过 `gate_and_publish_openapi.py` 的兼容性检查；breaking change 需要显式 `--allow-breaking`。
- 原有 `/api/harness/*` 内部接口不变，向后兼容。

## 测试与验证计划

### Avernet backend 测试

| 测试类型 | 位置 | 内容 |
|----------|------|------|
| Public harness router 单元测试 | `src/backend/tests/unit/adapters/http/openapi_v1/harness/` | 6 个接口请求体验证、鉴权失败、envelope 错误 |
| OpenAPI dump prefix 过滤 | `src/backend/scripts/dump_openapi.py --path-prefix /openapi/v1/harness` | 只输出 harness 路径 |
| Publish 脚本回归 | `src/gateway/scripts/dump_and_publish.sh --dry-run` | 同时产出 `bots.openapi.json` 与 `harness.openapi.json` |
| Gateway compatibility gate | `src/gateway/scripts/gate_and_publish_openapi.py` | 对 `harness.openapi.json` 通过兼容性检查 |

### Avernet gateway 测试

- 在 `src/gateway/tests/unit/core/forwarding/test_domain_map.py` 中新增 harness domain 解析断言。
- 在 `test_served_openapi.py` 中新增 harness 路径带有 `user: required` security 的断言。

### 本地集成验证

1. 启动 backend + gateway。  
2. 用 curl 调用：
   ```bash
   curl -H "Authorization: Bearer <user_token>" \
        -X POST \
        https://gateway/openapi/v1/harness/bots/{bot_id}/diagnose \
        -d '{"entity_id":"..."}'
   ```
3. 验证 backend 日志中的 `trigger_source=openapi` 和 collaborator 检查成功。

### ocb bcsfuse 验证

1. 启动 ocb gateway 后访问 `/openapi.json`，确认存在 bcsfuse-fusion、bcsfuse-workers 路径。
2. 用 curl 调用 `/openapi/v1/bcsfuse/groups/{group_id}/fuse` 与 `/openapi/v1/bcsfuse/workers/{worker_id}/config`，验证流量转发到内部 bcsfuse。
3. 如有内部 bcsfuse 测试环境，跑通已有 fusion / worker config 集成测试。

## 关键依赖与风险

| 风险 | 缓解措施 |
|------|----------|
| 内部 bcsfuse schema 与 Avernet 不完全一致 | 接入前先核对实际路由，必要时调整 ocb schema |
| Harness public router 与 internal router 重复字段 | DTO 复用已有 internal schemas，保持薄 wrapper |
| 新增的 harness domain 与现有 domain map 冲突 | match 使用 `/openapi/v1/harness/bots/**`，与 `/openapi/v1/bots/**` 不重叠 |
| 权限模型切换导致现有内部调用受影响 | 内部 `/api/harness/*` 不改动，仅在 `/openapi/v1/harness/*` 使用 `require_principal` |

## 已解决事项

1. **ocb 内部 bcsfuse 是否真实支持 `/v1/workers/config/batch`？**
   - 接入前必须确认内部 bcsfuse 的 `worker_routes.py` 中是否存在该路由。
   - 若不存在，本次先不在 gateway 暴露 `/openapi/v1/bcsfuse/workers/config/batch`，仅暴露 `groups/{group_id}/fuse` 和 `workers/{worker_id}/config`。

2. **harness 的 `preview`/`apply`/`rollback` 中 `entity_type`/`entity_id` 默认值策略？**
   - public harness DTO 要求显式传入 `entity_type` 和 `entity_id`，不保留内部默认 `"staff"`。
   - 这样对外部调用者更明确，也降低默认实体类型导致误操作的风险。
