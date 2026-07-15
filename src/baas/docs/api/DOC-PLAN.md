# API Documentation — Plan & Tracker

> **Goal:** produce OpenAPI 3.1 docs for the secbaas (baas) module. All 112 endpoints
> across 10 groups are documented.

This file is the **living tracker**.

## Format

**OpenAPI 3.1 spec + short prose overview per group.** Following the same conventions as
the backend API docs (`src/backend/docs/api/`).

- **Spec** (`*.yaml`, one file per group, `$ref` to `_shared.yaml`): machine-readable contract.
- **Prose overview**: per-group narrative, cross-endpoint flows, auth model, error codes.
- **Viewer**: `index.html` loads Redoc (unpkg CDN), bundles shared components, provides a
  dropdown to switch between groups.

### Conventions

**Language: Chinese.** All human-readable text is in Chinese — OpenAPI `summary` / `description`
(operations, schemas, fields, responses, security schemes, tags). Machine/contract identifiers
stay as-is (English/literal): paths, HTTP methods, field/property names, `operationId`,
enum values, status codes, schema component names, `message` values.

**Response envelope**: `ApiResponse` (`{code, message, data}`) where `code=0` means success.
BCN downlink uses its own `{ok, error?}` format as documented. This reflects the *current* API
shape (not a target state).

**Auth**:
- `apiKeyAuth`: `Authorization: Bearer <api-key>` for open-api and health-checker groups.
- `ssoCookie`: Buservice session cookie for management/admin groups.
- `meshAuth`: No app-level auth for `/internal/*` endpoints (relies on MOSN mesh).

**In scope**: All 112 endpoints across 10 groups (see below).

---

## File Layout

```
docs/openapi/
  DOC-PLAN.md            # this tracker
  index.html             # Redoc viewer
  serve-docs.sh          # local server
  _shared.yaml           # shared components
  open-api.yaml          # Group 1: External Open API
  bots.yaml              # Group 2: Bot lifecycle & management
  publishes.yaml         # Group 3: Publish workflow
  bot-runtime.yaml       # Group 4: Bot runtime (command/HTTP/WS)
  paas-devices.yaml      # Group 5: PaaS devices
  local-paas.yaml        # Group 6: Local PaaS & machines
  config.yaml            # Group 7: Config management
  health-checker.yaml    # Group 8: Bot health checker
  admin.yaml             # Group 9: Admin & API Key management
  internal.yaml          # Group 10: Internal services
  overview/
    foundations.md       # Global conventions
    error-codes.md       # Code registry
    versioning.md        # Versioning strategy
```

## Group Inventory

| # | Group | Slug | File | Endpoints | Auth | Status |
|---|-------|------|------|-----------|------|--------|
| 1 | **开放 API（外部调用）** | `open-api` | `open-api.yaml` | 7 | Bearer API Key | ☑ done |
| 1b | **BCN 下行链路** | `bcn-downlink` | `bcn-downlink.yaml` | 1 | Pre-shared Token | ☑ done |
| 2 | **Bot · 生命周期与管理** | `bots` | `bots.yaml` | 15 | Buservice Cookie | ☑ done |
| 3 | **发布流水线** | `publishes` | `publishes.yaml` | 11 | Buservice Cookie | ☑ done |
| 4 | **Bot 运行时（命令/HTTP/WS）** | `bot-runtime` | `bot-runtime.yaml` | 14 | Buservice Cookie | ☑ done |
| 5 | **PaaS 设备管理** | `paas-devices` | `paas-devices.yaml` | 12 | Buservice Cookie | ☑ done |
| 6 | **本地 PaaS 与机器管理** | `local-paas` | `local-paas.yaml` | 3 | Buservice Cookie | ☑ done |
| 7 | **配置管理** | `config` | `config.yaml` | 25 | Buservice Cookie | ☑ done |
| 8 | **Bot 健康检查** | `health-checker` | `health-checker.yaml` | 9 | Bearer API Key | ☑ done |
| 9 | **管理后台（API Key 等）** | `admin` | `admin.yaml` | 12 | Buservice Cookie | ☑ done |
| 10 | **内部服务** | `internal` | `internal.yaml` | 3 | Mesh (no auth) | ☑ done |
| | **Total** | | | **112** | | |

## Endpoint Details

### Group 1 — 开放 API（open-api.yaml, 7 endpoints）

| Method | Path | Description |
|--------|------|-------------|
| POST | `/openapi/v1/runs` | 单次对话 |
| GET | `/openapi/v1/runs/{run_id}` | 查询对话结果 |
| POST | `/openapi/v1/messages` | 消息投递 |
| POST | `/openapi/v1/messages/stream` | 流式消息投递（SSE） |
| GET | `/openapi/v1/messages/{message_id}` | 查询消息结果 |
| GET | `/openapi/v1/sessions/{session_id}` | 查询会话 |
| GET | `/openapi/v1/sessions/{session_id}/messages` | 查询会话消息列表 |

### Group 1b — BCN 下行链路（bcn-downlink.yaml, 1 endpoint）

| Method | Path | Description |
|--------|------|-------------|
| POST | `/bcn/downlink` | BCN 下行统一入口 |

### Group 2 — Bot 生命周期与管理（bots.yaml, 15 endpoints）

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/bots` | 查询 Bot 列表 |
| POST | `/api/v1/bots` | 创建 Bot |
| GET | `/api/v1/bots/{bot_uuid}` | 查询单个 Bot |
| GET | `/api/v1/bots/{bot_uuid}/detail-by-uuid` | 查询 Bot 详情（含设备） |
| GET | `/api/v1/bots/{bot_id}/detail-by-id` | 按内部 ID 查询 Bot 详情 |
| POST | `/api/v1/bots/{bot_uuid}/update` | 更新 Bot |
| POST | `/api/v1/bots/{bot_uuid}/destroy` | 销毁 Bot |
| POST | `/api/v1/bots/{bot_uuid}/stop` | 停止 Bot |
| POST | `/api/v1/bots/{bot_uuid}/update-devices` | 指定设备定向更新 |
| POST | `/api/v1/bots/{bot_uuid}/scale` | 扩缩容 Bot |
| POST | `/api/v1/bots/{bot_uuid}/restart` | 重启 Bot |
| GET | `/api/v1/bots/{bot_uuid}/device-status` | 查询 Bot 设备聚合状态 |
| GET | `/api/v1/bots/{bot_uuid}/devices` | 查询 Bot 关联设备（按 UUID） |
| GET | `/api/v1/bots/{bot_id}/devices-by-id` | 查询 Bot 关联设备（按内部 ID） |

### Group 3 — 发布流水线（publishes.yaml, 11 endpoints）

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/publishes` | 创建发布 |
| GET | `/api/v1/publishes/{publish_id}` | 查询发布详情 |
| GET | `/api/v1/publishes/{publish_id}/start-progress` | 查询 Bot 设备启动进度 |
| GET | `/api/v1/publishes/{publish_id}/progress` | 查询发布进度 |
| POST | `/api/v1/publishes/{publish_id}/approve` | 批准当前阶段 |
| POST | `/api/v1/publishes/{publish_id}/reject` | 拒绝发布 |
| POST | `/api/v1/publishes/{publish_id}/revoke` | 撤销发布 |
| POST | `/api/v1/publishes/{publish_id}/execute` | 执行当前阶段 |
| POST | `/api/v1/publishes/{publish_id}/complete` | 完成发布 |
| POST | `/api/v1/publishes/{publish_id}/retry` | 重试失败的发布 |
| POST | `/api/v1/publish/device-callback` | 设备启动回调 |

### Group 4 — Bot 运行时（bot-runtime.yaml, 14 endpoints）

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/bots/{tenant}/{bot_uuid}/execute-command` | 在 Bot 设备上执行命令 |
| ANY | `/api/v1/bots/{tenant}/{bot_uuid}/invoke-http/{port}/{path}` | HTTP 代理到 Bot 设备 |
| GET | `/api/v1/bots/{bot_uuid}/http-info` | 获取 HTTP 连接信息 |
| GET | `/api/v1/bots/{bot_uuid}/ws-info` | 获取 WebSocket 连接信息 |
| POST | `/api/v1/bots/{tenant}/{bot_uuid}/open-folder` | 打开设备文件夹 |
| GET | `/api/v1/bots/{bot_uuid}/start-progress` | 查询 Bot 容器启动进度 |
| POST | `/api/v1/bots/{tenant}/{bot_uuid}/files/upload-url` | 获取预签名上传 URL |
| POST | `/api/v1/bots/{tenant}/{bot_uuid}/files/download-url` | 文件下载到 OSS |
| POST | `/api/v1/bots/{tenant}/{bot_uuid}/files/upload-url/{transfer_id}/complete` | 完成上传 |
| DELETE | `/api/v1/bots/{tenant}/{bot_uuid}/files/upload-url/{transfer_id}` | 取消上传 |
| GET | `/api/v1/bots/{tenant}/{bot_uuid}/files/staging` | 列出暂存区对象 |
| DELETE | `/api/v1/bots/{tenant}/{bot_uuid}/files/staging` | 删除暂存对象 |
| GET | `/api/v1/bots/{tenant}/{bot_uuid}/files/transfers/{transfer_id}` | 查询传输状态 |
| POST | `/api/v1/bots/{tenant}/{bot_uuid}/files/transfers/{transfer_id}/share-link` | 生成分享下载链接 |

### Group 5 — PaaS 设备管理（paas-devices.yaml, 12 endpoints）

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/devices/{device_uuid}` | 查询设备信息 |
| POST | `/api/v1/paas/devices` | 创建设备 |
| DELETE | `/api/v1/paas/devices/{paas_device_id}` | 销毁设备 |
| POST | `/api/v1/paas/devices/{paas_device_id}/commands` | 执行命令 |
| GET | `/api/v1/paas/devices/{paas_device_id}/ws-info` | 获取 WS 连接信息 |
| GET | `/api/v1/paas/devices/{paas_device_id}/info` | 获取设备详情 |
| PUT | `/api/v1/paas/devices/{paas_device_id}/outbound-rule` | 更新出站规则 |
| ANY | `/api/v1/paas/devices/{paas_device_id}/invoke-http/{port}/{path}` | HTTP 代理 |
| POST | `/api/v1/paas/devices/{paas_device_id}/open-folder` | 打开文件夹 |
| PUT | `/api/v1/paas/devices/{paas_device_id}/ttl` | 更新设备 TTL |
| GET | `/api/v1/paas/relay-sessions/{session_id}` | 查询中继会话路由 |
| PUT | `/api/v1/paas/relay-sessions/{session_id}` | 更新中继会话状态 |

### Group 6 — 本地 PaaS（local-paas.yaml, 3 endpoints）

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/local/machines/{machine_id}/info` | 查询机器信息 |
| GET | `/api/v1/local/machines/{machine_id}/res-dirs` | 查询资源目录 |
| GET | `/api/v1/local/users/{user_id}/machines` | 查询用户机器列表 |

### Group 7 — 配置管理（config.yaml, 25 endpoints）

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/tenants` | 查询租户列表 |
| POST | `/api/v1/tenants` | 创建租户 |
| GET | `/api/v1/tenants/{name}` | 查询单个租户 |
| PUT | `/api/v1/tenants/{name}` | 更新租户 |
| DELETE | `/api/v1/tenants/{name}` | 删除租户 |
| GET | `/api/v1/tenants/{name}/config` | 查询租户配置 |
| GET | `/api/v1/system-configs` | 查询系统配置列表 |
| POST | `/api/v1/system-configs` | 创建系统配置 |
| GET | `/api/v1/system-configs/{conf_key}` | 查询单个系统配置 |
| PUT | `/api/v1/system-configs/{conf_key}` | 更新系统配置 |
| DELETE | `/api/v1/system-configs/{conf_key}` | 删除系统配置 |
| GET | `/api/v1/device-templates` | 查询设备模板列表 |
| POST | `/api/v1/device-templates` | 创建设备模板 |
| GET | `/api/v1/device-templates/online` | 查询在线设备模板 |
| GET | `/api/v1/device-templates/by-template-id/{template_id}` | 按 template_id 查询 |
| GET | `/api/v1/device-templates/resolve` | 解析设备模板 |
| GET | `/api/v1/device-templates/{template_uuid}` | 按 UUID 查询 |
| PUT | `/api/v1/device-templates/{template_uuid}` | 更新设备模板 |
| POST | `/api/v1/device-templates/{template_uuid}/status-transitions` | 状态转换 |
| POST | `/api/v1/device-templates/{template_uuid}/delete` | 删除设备模板 |
| GET | `/api/v1/bot-qpm` | 查询 QPM 配置列表 |
| POST | `/api/v1/bot-qpm` | 创建/更新 QPM 配置 |
| GET | `/api/v1/bot-qpm/{bot_id}` | 查询单个 QPM 配置 |
| PUT | `/api/v1/bot-qpm/{bot_id}` | 更新 QPM 配置 |
| DELETE | `/api/v1/bot-qpm/{bot_id}` | 删除 QPM 配置 |

### Group 8 — Bot 健康检查（health-checker.yaml, 9 endpoints）

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/bot-health-checker/active_bots` | 查询活跃 Bot 设备列表 |
| GET | `/api/v1/bot-health-checker/devices` | 查询指定 Bot 的 PaaS 设备 |
| POST | `/api/v1/bot-health-checker/ttl/extend` | 延长 TTL |
| GET | `/api/v1/bot-health-checker/health` | 执行健康检查 |
| GET | `/api/v1/bot-health-checker/alive` | 检查存活 |
| GET | `/api/v1/bot-health-checker/sandbox` | 查询沙箱信息 |
| GET | `/api/v1/sandbox-device/active-sandboxes` | 分页查询激活沙箱 |
| POST | `/api/v1/sandbox-device/probe-and-warn` | 探活告警 |
| POST | `/api/v1/sandbox-device/renew-ttl` | 续期沙箱设备 |

### Group 9 — 管理后台（admin.yaml, 12 endpoints）

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/admin/api-keys` | 查询 API Key 列表 |
| POST | `/api/v1/admin/api-keys` | 创建 API Key |
| GET | `/api/v1/admin/api-keys/{api_key_prefix}` | 查询 API Key 详情 |
| PUT | `/api/v1/admin/api-keys/{api_key_prefix}/config` | 更新 API Key 配置 |
| PATCH | `/api/v1/admin/api-keys/{api_key_prefix}/status` | 更新 API Key 状态 |
| GET | `/api/v1/admin/api-keys/{api_key_prefix}/allowed-bots` | 查询允许的 Bot 列表 |
| POST | `/api/v1/admin/api-keys/{api_key_prefix}/allowed-bots/grant` | 授权 Bot 访问 |
| POST | `/api/v1/admin/api-keys/{api_key_prefix}/allowed-bots/revoke` | 撤销 Bot 访问 |
| POST | `/api/v1/admin/force-success` | 强制发布成功 |
| POST | `/api/v1/admin/devices/{device_uuid}/status` | 强制更新设备状态 |

### Group 10 — 内部服务（internal.yaml, 3 endpoints）

| Method | Path | Description |
|--------|------|-------------|
| POST | `/internal/v1/forward` | 跨实例请求转发 |
| GET | `/internal/bot-health-checker/alive` | 内部存活检查 |
| GET/POST | `/api/v1/cache/{key}` | 缓存读写 |