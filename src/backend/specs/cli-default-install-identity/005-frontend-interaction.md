# Default 能力集 CLI：前端交互开发说明

## 1. 目标与一期边界

在 Bot 的**默认能力集**中展示 AgentPass 已授权的 CLI，并允许符合条件的 Bot Owner 将单个 CLI 的授权身份切换为 `owner` 或 `caller`。

一期支持的默认安装 profile 仅为：

- `openclaw`
- `claude_code` 且 `template_type=generalCC`

初始受管 CLI 为 `dataphin`（展示名 `dataphin-cli`）和 `deepinsight-cli`。历史 AgentPass CLI 也应展示，但前端不能伪造 YAML 中不存在或尚未由 AgentPass 授权的 CLI。

本功能的 UI 语义是**AgentPass 授权身份配置**。`caller` 表示后端已经把 CLI 的 `identity_mode` 写入 AgentPass；OpenClaw 和 Claude Code 是否在真实 CLI 执行时消费该身份仍待容器 E2E 验收。因此 UI 不得写“已按当前聊天用户实际执行”。

### 本期不做

- 不在非默认能力集中展示、添加、移动或配置 CLI。
- 不提供新增 CLI 的表单、命令输入框、安装命令输入框或 YAML 编辑器。
- 不提供 CLI 删除入口。受管默认项会在后续 Bot Bootstrap 时再次由 Backend 收敛，删除会造成误导。
- 不展示“已安装”“安装中”“安装失败”等状态。容器侧 probe/install 状态没有对前端暴露的 API。
- 不在 CLI 行展示或编辑 Bot aggregate / IAM caller token；CLI `caller` 会参与 Bot aggregate，具体状态由 Caller Context 的 `bot_call_type` 只读展示。

## 2. 页面位置与信息架构

前端在 Bot 详情页的**能力集**区域新增/扩展“默认能力集”详情：

```text
Bot 详情
└── 能力集
    ├── 默认能力集
    │   ├── MCP 列表（既有）
    │   └── CLI 列表（本期新增）
    │       ├── dataphin-cli
    │       └── deepinsight-cli
    └── 非默认能力集
        └── 不展示 CLI 区块
```

仓库当前没有消费 `/api/skillsets/resources` 或 CLI call-type API 的前端页面/客户端实现。本期前端可在现有 Bot 能力集页面落地具体组件路径；本文定义数据和交互契约，不强制组件文件名。

## 3. 页面展示

### 3.1 获取 CLI 列表

复用能力集资源聚合接口：

```http
GET /api/skillsets/resources?entity_id={ownerId}&entity_type={entityType}&bot_id={botId}
X-User-Id: {currentUserId}
```

沿用现有能力集请求客户端的登录态与请求头；`ownerId`、`entityType`、`botId` 均从当前 Bot 上下文读取，不能由用户输入或 URL 自由拼接。

前端从 `data` 中找到 `is_default=true` 的能力集，只渲染其 `clis`：

```json
{
  "success": true,
  "data": [
    {
      "id": "1",
      "name": "Default",
      "is_default": true,
      "clis": [
        {
          "cli_code": "dataphin",
          "cli_name": "dataphin-cli",
          "cli_desc": "Dataphin 命令行工具"
        }
      ]
    }
  ],
  "count": 1
}
```

字段约束：

| 字段 | 前端用途 | 规则 |
|---|---|---|
| `cli_code` | 行稳定 key、PATCH 路径参数 | 不展示为可编辑命令；不可自行替换为 executable。 |
| `cli_name` | 主标题 | 缺失时回退展示 `cli_code`。 |
| `cli_desc` | 副标题 | 缺失时省略，不显示空占位。 |

资源接口即使返回 `identity_mode`，也只作后端资源兼容字段，**不得**用它决定选择器状态。

### 3.2 获取统一 Caller 状态

与资源请求同时读取 Caller Context：

```http
GET /api/bots/{botId}/caller-context?ctoken={opaqueCtoken}&stage=draft&entity_id={entityId}
```

预发环境的完整 URL 形态如下，实际值均从当前老平台页面上下文取得：

```text
https://agentclaw-pre.alipay.com/api/bots/{botId}/caller-context?ctoken={opaqueCtoken}&stage=draft&entity_id={entityId}
```

使用老平台既有登录态和请求客户端。`ctoken` 是网关追加的兼容参数，前端不得持久化、打印或上报其值；用户身份仍以后端解析到的登录态为准。`entity_id` 从当前 Bot 页面上下文读取，不能由用户自由输入。

该老平台接口直接返回 Caller Context 对象，不使用 OpenAPI envelope。响应中 `mcp_call_types` 与 `cli_call_types` 是同一语义的稀疏 map：键为资源 code，缺少的 key 表示 `owner`。因此每一个已列出的 CLI 均按以下规则得到唯一身份状态：

```ts
const identityMode = callerContext.cli_call_types[cli.cli_code] ?? 'owner';
```

示例：

```json
{
  "capability": "caller_identity.v1",
  "stage": "draft",
  "publish_id": null,
  "bot_call_type": "owner",
  "mcp_call_types": { "mcp.calendar": "caller" },
  "cli_call_types": { "dataphin": "caller" },
  "editable": true
}
```

`bot_call_type` 是 MCP 与 CLI 的 Bot aggregate，任一有效 caller 都会令其为 `caller`。CLI 选择器仍只使用 `cli_call_types`；MCP 页面同理只使用 `mcp_call_types`。`editable` 是 Caller Context 给出的最终可编辑提示，仍须处理 PATCH 返回的权限、锁和状态错误。

### 3.3 行样式与文案

每个 CLI 一行，建议字段顺序：

```text
[CLI 名称]                       [身份选择：Owner | Caller]
说明文字                         当前：Bot Owner / 调用者（授权配置）
```

文案固定为：

| 值 | 选择项文案 | 辅助文案 |
|---|---|---|
| `owner` | `Bot Owner` | 使用 Bot Owner 的授权身份。 |
| `caller` | `调用者` | 将调用者身份写入 AgentPass 授权配置；实际引擎执行消费以容器 E2E 验收为准。 |

不要将 `caller` 写成“当前用户 token 已注入”或“CLI 已使用调用者凭据执行”。

### 3.4 展示状态

| 状态 | 触发条件 | UI 行为 |
|---|---|---|
| 加载中 | 首次并发请求资源接口和 Caller Context | 默认能力集 CLI 区展示骨架屏；不显示猜测的默认 CLI。 |
| 空态 | 默认能力集不存在，或其 `clis=[]` | 显示“暂未从 AgentPass 获取到 CLI 授权。Bot 完成启动认证后请刷新。”不显示添加按钮。 |
| 正常 | 获取到合法 CLI 项 | 展示行及当前 identity。 |
| Caller Context 请求失败 | 资源列表成功但 Caller Context 非成功、响应字段缺失或 `cli_call_types` 含未知值 | 行保留名称与说明，禁用身份选择，显示“身份信息不可用，请刷新后重试”。重试必须同时读取资源列表和 Caller Context。 |
| 列表请求失败 | `GET /api/skillsets/resources` 非成功 | 显示错误态与“重试”按钮；重试必须同时读取资源列表和 Caller Context。 |

## 4. 身份切换交互

### 4.1 可编辑条件

前端以 Caller Context 的 `editable=true` 作为可操作的必要条件，并可依据现有 Bot 上下文预判以下条件：

1. 当前登录用户是 Bot Owner；
2. Bot 为 `service` 且状态为 `ACTIVE`；
3. profile 为 `openclaw`，或 `claude_code/generalCC`；
4. 当前页面没有被其他协作者占用编辑锁。

不满足时仍展示 CLI 与当前身份，但身份选择器只读，并给出简短原因。后端才是最终裁决：前端预判和 `editable` 均不能代替 PATCH 的错误处理。

当前接口不要求前端提交 `lock_epoch`。Router 读取服务端的当前协作锁；如已有他人持锁，接口会返回 `423`。

### 4.2 变更流程

1. 用户在某行选择另一个身份。
2. 打开确认弹窗，不做乐观更新。
3. 弹窗确认后锁定**该行**的两个选项并展示提交中状态；其他行可维持可读。
4. PATCH 成功后，用响应中的 `call_type` 更新该行，再并发重新请求资源接口与 Caller Context 完成最终一致性校验。
5. PATCH 失败后恢复原选择，关闭提交状态并按错误映射提示；对于 scope 同步失败或网络失败，再获取一次两类读取。

确认弹窗建议：

```text
标题：确认切换 CLI 授权身份？
正文：将 {cli_name} 切换为“{Bot Owner / 调用者}”。该操作会更新 AgentPass 授权配置。
主按钮：确认切换
次按钮：取消
```

### 4.3 写接口（老平台兼容路由）

老平台前端期望使用与 MCP caller 配置一致的兼容路径：

```http
PATCH /api/bots/{botId}/clis/{cliCode}/call-type?ctoken={opaqueCtoken}&entity_id={entityId}
Content-Type: application/json

{
  "call_type": "caller",
  "lock_epoch": 123
}
```

使用老平台既有登录态和请求客户端。`ctoken`、`entity_id` 和可选 `lock_epoch` 的来源及安全约束与现有 MCP call-type 接口一致：当前 Bot 存在协作编辑锁时必须传入前端已持有的锁版本；没有锁时可省略。后端只从已认证登录态解析操作者；前端不得向请求 body 添加 `actor_id`、AgentPass token、IAM token 或 CLI 安装参数。

期望成功响应采用老平台直接对象结构：

```json
{
  "cli_code": "dataphin",
  "call_type": "caller",
  "bot_call_type": "caller"
}
```

切回 `owner` 时，发送同一路径和 `{ "call_type": "owner" }`。后端会删除该 CLI 的 sparse caller 覆盖并重新收敛 AgentPass scope；前端不需要处理数据库语义。

### 4.4 错误映射

| HTTP 状态 | 典型语义 | 前端处理 |
|---|---|---|
| `401` | 未登录或登录态失效 | 走全局登录恢复，不保留待提交选择。 |
| `404` | Bot 不可见、非 Owner，或 CLI 已不在当前 AgentPass scope | 提示“该 CLI 配置已不可用”，刷新资源列表与 Caller Context；不显示内部鉴权原因。 |
| `409` | Bot 非 Active/service、profile 不支持、引擎已切换、配置只读，或最后一个 caller 不能降为 owner | 对 `CALLER_TO_OWNER_UNSUPPORTED` 提示“该 CLI 是 Bot 的最后一个调用者身份，当前不能切回 Bot Owner”；其它情况提示“当前 Bot 不支持修改 CLI 授权身份”。随后刷新 Bot、资源列表和 Caller Context。 |
| `423` | 他人持有编辑锁 | 展示既有协作锁提示，保留原选择，不自动重试。 |
| `422` | 请求 body 校验失败 | 记录前端诊断事件，不向用户暴露请求细节；恢复原选择。 |
| `5xx` / 网络错误 | AgentPass 收敛失败或网络失败；后端会补偿本地 sparse 覆盖 | 提示“保存失败，请稍后重试”，刷新资源列表和 Caller Context 后允许用户手动重试。 |

错误响应若包含 `request_id`，可展示它供排查；不能展示完整响应、HTTP 请求头、`ctoken`、其他 token 或用户身份凭据。

## 5. 关键前端状态模型

建议将资源读取状态和单行提交状态分开，避免一个 CLI 的请求阻塞整个能力集：

```ts
type CliIdentityMode = 'owner' | 'caller';

type DefaultCliRow = {
  cliCode: string;
  name: string;
  description?: string;
  identityMode: CliIdentityMode;
  editable: boolean;
  saving: boolean;
};
```

- `editable` 来自 Caller Context，不代表绕过后端校验。
- `saving` 仅作用于当前 `cliCode`。
- `identityMode` 只由 `cli_call_types[cliCode] ?? 'owner'` 推导；不能读取资源接口的 `identity_mode`。
- 资源列表和 Caller Context 均以服务端返回为准，不能仅以 PATCH 成功响应长期维护本地缓存。

## 6. 前端验收清单

1. Default 能力集包含 CLI 时，正确展示 `cli_name`、`cli_desc`，并由 Caller Context 的 `cli_call_types` 显示身份；非 Default 能力集不显示 CLI 区块。
2. `dataphin` 和 `deepinsight-cli` 在 AgentPass 已注册时可展示；页面不硬编码或客户端补造它们。
3. 后端发布老平台 CLI PATCH 兼容路由后，Owner 在 Active 的 `openclaw` 或 `claude_code/generalCC` Bot 中可将 CLI 切为 caller，Bot aggregate 随之变为 caller；最后一个 caller 切回 owner 返回既有 `409 CALLER_TO_OWNER_UNSUPPORTED`，若仍有其它 caller 则允许切回 owner。每次成功后同时刷新资源列表和 Caller Context。
4. `claude_code/normalCC`、其他引擎、非 service、非 Active 或非 Owner 状态下，UI 只读；即便前端预判遗漏，收到 `409/404` 也能安全回退。
5. 他人持锁时收到 `423` 后不改本地身份、不自动重试。
6. Caller Context 读取失败、`cli_call_types` 缺失或值未知时不允许切换；资源接口的 `identity_mode` 不影响选择器。
7. 不出现添加、删除、安装、终端命令、PATH、token 或安装状态 UI。
8. 文案不宣称 `caller` 已在真实引擎 CLI 执行链路中生效。

## 7. 后端对接定位

| 目的 | 后端入口 |
|---|---|
| Default 能力集资源列表 | `adapters/http/skill_center/skillsets.py:list_skill_set_resources` |
| 老平台 MCP / CLI 统一 Caller 状态 | `adapters/http/caller_identity/router.py:get_caller_context`，对应 `GET /api/bots/{bot_id}/caller-context` |
| 老平台 CLI 身份切换 HTTP 入口 | `adapters/http/caller_identity/router.py:update_cli_call_type`，对应 `PATCH /api/bots/{bot_id}/clis/{cli_code}/call-type` |
| 现有 CLI 写实现（老平台前端不直连） | `adapters/http/openapi_v1/caller_identity/router.py:update_cli_call_type` |
| 身份切换领域逻辑和补偿 | `core/caller_identity/service.py:CallerIdentityService.update_cli_call_type` |
| Default CLI/历史 scope 收敛 | `core/mcp/services/cli_passport_scope.py:CliPassportScopeReconciler` |
| UI 返回的 CLI 数据结构 | `adapters/http/skill_center/schemas.py:CLIInSetResponse` |

相关需求与运行时边界见 [001-spec-output.md](001-spec-output.md)。
