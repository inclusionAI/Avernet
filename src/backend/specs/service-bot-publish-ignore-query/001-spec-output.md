---
agent: tc-review
status: approved
created: 2026-09-17T21:23:12+08:00
iteration: 1
---

# 系分 Spec：查询服务 Bot 的 publish-ignore 路径列表

## 需求概述

在已有增删 ignore 接口旁增加只读查询接口。用户传入 bot_id、entity_id、stage，Backend 复用现有权限与运行时绑定解析，查询当前实例的 `/home/admin/.service_bot_publish_ignore`。逐实例返回规则，不修改文件，不把不同实例的规则合并成一个列表。

## 编码 Spec

### 功能点

- [ ] Backend 新增 `GET /api/service-bot/publish/ops/publish-ignore`，必填查询参数 `bot_id`、`entity_id`、`stage`（draft/verify/online）；不增加 version、operation、path 入参。
- [ ] 管理员可查询全部服务 Bot；其他用户沿用现有 ADMIN/OWNER 管理权限。继续复用 `RuntimeBindingResolutionService.resolve`，不重新实现 draft、verify、online binding 选择。
- [ ] BaaS 查询当前设备快照中的每个固定设备；ARCA 查询当前绑定目标。复用连接解析与认证，不增加签名密钥。
- [ ] Engine 新增 `GET /api/bot/publish-ignore`，查询参数包含 expected target 的 bot_id/entity_id/stage 和关联 request_id。通过当前凭据验证目标身份后读取固定文件。
- [ ] 文件不存在返回空列表；规则按现有归一化语义返回，保留顺序和重复行，忽略空行与 `#` 开头的注释。非法规则明确报错，不能静默丢弃。
- [ ] 查询不创建 ignore 文件、锁文件、请求去重 journal，不写任何运行时状态。

### 技术方案

既有链路：`router_publish.change_publish_ignore` → `PublishIgnoreService.change/_change` → `HttpPublishIgnoreRuntime.targets/change` → Engine `update_publish_ignore` → `FilePublishIgnoreService.change`。

允许新增点：在这些现有模块的协议/实现增加 `query` 方法与 GET endpoint；在 `PublishIgnoreService` 内将现有鉴权和 binding 解析提取成私有共享方法，供 change/query 复用。其他已有变更（取消签名、原地重启）保持原样，不在本任务重写。

禁止触碰点：不改 daas 复制逻辑、原地重启、发布单状态机、通用运行时绑定解析器、通用 HTTP 客户端、BaaS SDK、前端、数据库 schema，不引入 child router。

Engine 查询复用 `verify_identity`、`_read`、`normalize_path`。`_read` 已有固定路径、NOFOLLOW、NONBLOCK、普通文件与大小限制；原有写入采用原子 replace，因此查询可读取一次文件快照，无需创建 mutation lock。沿用 LF 分行、CRLF 去尾规则；不使用会改变 Unicode 分行语义的 splitlines。非法 UTF-8、非普通文件或超大文件返回明确失败，不以空列表掩盖。

Backend 保留既有响应封装 `ApiResponse` 与 scope/result 结构：

```json
{
  "success": true,
  "data": {
    "success": true,
    "scope": "current_instances",
    "request_id": "generated-request-id",
    "results": [
      {
        "provider": "arca",
        "binding_id": 123,
        "target_id": "bound-target",
        "status": "success",
        "paths": ["workspace/bin"],
        "entry_count": 1,
        "revision": "sha256-of-original-file-bytes"
      }
    ]
  }
}
```

每个目标失败沿用 `failed/unknown`、`error_code/error_type` 表达；总体 success 仅在全部目标查询成功时为 true，成功目标结果仍保留。失败结果不伪造 paths=[]。没有当前实例沿用 `no_current_instances`；不把“没有实例”当作“实例的文件为空”。不同实例列表不一致并非查询失败，原样逐项呈现。

### 外部系统边界日志

| 边界 | 事件 | 必须记录的非敏感字段 |
|---|---|---|
| 用户 → Backend | `backend.publish_ignore.query_request/query_response/query_failure` | system、direction、GET、route、bot_id、entity_id、stage、operator_id、request_id、结果/安全错误码、elapsed_ms |
| Backend → Engine | `backend.publish_ignore.engine_query_request/engine_query_response/engine_query_failure` | 上述业务字段、provider、binding_id、target_id、engine_request_id、status、paths、entry_count、revision、elapsed_ms |
| Engine 入站 | `engine.publish_ignore.query_request/query_success/query_failure` | system、direction、GET、route、expected target、request_id、结果/安全错误码、elapsed_ms |

查询返回的路径列表属于业务出参，应完整记录；超过项目既有大内容阈值则记录长度与摘要。认证头、Cookie、token、密码、secret、key、credential、session 等可复用凭据递归脱敏；不输出原始 connection、headers 或异常 response/body。保持现有安全错误映射，不直接记录可能携带凭据的异常字符串。测试须覆盖请求、成功与失败事件和敏感哨兵值不落日志。

### 关键方法抽象

| 抽象/方法 | 所在层或模块 | 职责与边界 | 输入与输出 | 协作对象与副作用 |
|---|---|---|---|---|
| `PublishIgnoreService.query` | Backend core | 鉴权、解析当前 stage、逐目标查询汇总；不接触文件或 HTTP | query、operator_id、is_admin → aggregate/error | 复用权限/绑定 repository 和 runtime；只读 |
| `PublishIgnoreService` 私有共享目标解析方法 | Backend core | 从现有 `_change` 提取不变的权限与 binding 规则；不扩张校验 | query 或 command 的共同目标字段 → runtime binding/targets | 复用 RuntimeBindingResolutionService 与 runtime.targets |
| `HttpPublishIgnoreRuntime.query` | Backend plugin | 固定目标连接、GET 调用、结果验证及边界日志；不做权限决策 | binding、target、query、operator_id → 单目标结果 | BaaS adapter transport 或 ARCA HTTP，只读网络请求 |
| `FilePublishIgnoreService.query` | Engine plugin | 验证身份、读固定文件、解析规则、计算 revision；不接受文件路径 | expected target / query request → paths/count/revision | 复用 verify_identity/_read/normalize_path；零文件写入 |

这些方法与现有 change 链路对应，不引入通用操作分发框架。query 与 change 共用鉴权和解析，避免新增 GET 绕过管理权限；Engine 用独立只读入口，避免误执行 `_consume_request`。

### 关键领域模型设计

#### `PublishIgnoreQuery`

单次只读查询值对象，不持久化。Backend 生成关联 ID，运行时查询仍携带独立 Engine request ID，以便多目标追踪。

| 字段 | 类型 | 必填 | 默认/约束 | 来源 | 说明 |
|---|---|---|---|---|---|
| bot_id | str | 是 | 沿用增删接口非空/长度限制 | HTTP 查询参数 | Bot 标识，不代表权限 |
| entity_id | str | 是 | 沿用增删接口约束 | HTTP 查询参数 | 主体限定 |
| stage | Literal[draft,verify,online] | 是 | 无默认 | HTTP 查询参数 | 解析当前阶段，不支持指定版本 |
| request_id | str | 是 | Backend 生成 | 应用边界 | 关联标识，不参与 mutation journal |

Engine 沿用 `ExpectedTarget` 表示身份，若需查询 DTO 只组合 expected_target/request_id，不复用含必填 operation/path 的变更 DTO。

#### 单目标查询快照

| 字段 | 类型 | 必填 | 约束 | 来源 | 说明 |
|---|---|---|---|---|---|
| provider/binding_id/target_id | str/int/str | 是 | 当前解析目标 | Backend | 与现有变更结果一致 |
| status | str | 是 | success/failed/unknown | Backend | 成功与失败区别 |
| paths | list[str] | 成功必填 | 保留顺序与重复，均归一化 | Engine | 当前有效规则列表 |
| entry_count | int | 成功必填 | 等于 len(paths) | Engine | 与既有非注释规则计数一致 |
| revision | str | 成功必填 | 64 位小写 SHA-256 hex | Engine | 原始文件字节摘要；缺失文件按空字节 |
| error_code/error_type | str | 失败必填 | 沿用安全错误表达 | Backend | 不泄漏凭据或原始上游响应 |

不变量：paths/count/revision 来自同一次读取；查询不改变实体或文件状态。响应 DTO/协议的扩展不改变现有 POST 契约。

### 文件改动范围

下表省略共同前缀 `src/backend/src/agentclaw/community/` 或 `src/engine/src/engine/community/`。

| 文件 | 改动类型 | 内容 |
|---|---|---|
| Backend `adapters/http/service_bot/router_publish.py`、必要的 `schemas_publish.py` | 修改 | 平放 GET 与参数校验/响应映射 |
| Backend `kernel/publish_ignore.py`、`api/publish_ignore_service.py`、`plugin_api/publish_ignore_runtime.py` | 修改 | 查询值对象和协议 |
| Backend `core/service_bot/services/publish_ignore_service.py` | 修改 | query 和最小权限/目标解析复用 |
| Backend `plugins/community/publish_ignore_runtime.py` | 修改 | 双 provider 查询传输 |
| Engine `api/bot/router.py`、`core/publish_ignore/models.py`、`core/publish_ignore/protocol.py`、`plugins/publish_ignore.py` | 修改 | GET、查询契约与只读解析 |
| 现有 publish_ignore factory、contract、service、runtime、router、endpoint tests | 修改 | 查询行为和旧变更回归 |
| 相关既有契约文档/端点注册清单及本任务报告 | 修改/新增 | 同步新增 GET，不扩张框架 |

### 验收标准

- [ ] draft/verify/online、BaaS/ARCA 和现有管理权限均正确。
- [ ] 多实例逐目标返回；失败不伪装为空成功；缺失文件是成功空列表。
- [ ] 查询零状态写入，原有增删、原地重启相关测试不回退。
- [ ] 本次变更单测行覆盖率 >90%，用 pytest --cov --cov-report=term-missing 实测；报告区分改动文件和变更行范围，不编造 ACI。
- [ ] 边界日志及凭据脱敏有测试证据，静态检查和 git diff --check 通过。

## Review Spec

- [ ] HTTP 层无 binding、文件或目标编排逻辑；现有协议同步扩展。
- [ ] query 与 change 权限一致；管理员身份仍来自可信认证上下文。
- [ ] 原文件解析语义、重复计数和 SHA-256 快照一致，文件不存在与查询失败明确区分。
- [ ] 无 lock/journal/ignore 文件创建，无错误调用 change 实现读取，无“先写空文件再读”。
- [ ] 关键方法职责、模型字段/不变量、日志与非敏感业务字段齐全。
- [ ] 单测实测 >90%，不降低门禁、不加全局忽略。
- 不可接受：把多实例合并去重后冒充统一配置；静默跳过非法行；重新引入 version/signing；修改通用 resolver；残留未使用 import/变量/孤儿代码；Python `:` 前空格或不以 `# ` 开头的注释。

## QA Spec

| 编号 | 用例 | 操作 | 预期 |
|---|---|---|---|
| Q01 | 正常查询 | 管理员和有 ADMIN/OWNER 权限用户查询各 stage | 当前正确 binding，规则和原文件一致 |
| Q02 | 权限拒绝 | 匿名/无管理权限/其他主体 | 拒绝且不调用 Engine |
| Q03 | 参数校验 | 缺参数、非法 stage、空标识 | HTTP 校验失败，无新增 version 支持 |
| Q04 | BaaS 多实例 | 两设备分别配置不同列表 | 固定 uuid 查询，两组独立结果，不合并 |
| Q05 | ARCA | 配置绑定 URL/headers | 复用可信连接，GET 正确转发，凭据不落日志 |
| Q06 | 不存在/空文件 | 临时目录无文件或空文件 | paths=[]、count=0、空字节 hash；目录内容不新增 |
| Q07 | 解析 | CRLF/注释/空行/./前缀/末尾斜杠/重复规则/无末尾LF | 与 change 归一化一致，保留顺序与重复，count正确 |
| Q08 | 文件异常 | 非法规则、UTF-8、符号链接、FIFO、超限 | 明确失败且不阻塞/不改文件 |
| Q09 | 身份异常 | Engine credentials 与预期不符 | RUNTIME_IDENTITY_MISMATCH，无读其他目标 |
| Q10 | 传输失败 | 部分超时、上游拒绝、结构不合法 | 保留其他成功结果，总体 false，失败不返回伪造空列表 |
| Q11 | 无绑定/实例 | resolver 失败、空设备快照 | 沿用 stage_not_bound/no_current_instances |
| Q12 | 日志 | 请求/成功/失败带凭据哨兵 | 非敏感字段完整、耗时/关联ID存在，凭据未输出 |
| Q13 | 旧功能回归 | 运行原 POST 增删与协议/端点测试 | 原行为不变，新增协议 fake 全部适配 |

前置条件：本地使用临时文件和 fake runtime/credentials，不访问真实 Bot 或 /home/admin 文件。实际预发验证等待用户确认代码及部署目标，不能以单测冒充线上验证。

## Ship Spec

- 用户已确认：先生成三接口前端对接文档，再编码；文档已写入 frontend-api.md。
- 开发 worktree 沿用 `service-bot-publish-ignore-ops-rel20260917`，保留全部已有未提交改动。
- 部署目标：待确认；本任务不授权部署、重启容器、推送或更新 PR。
- 开发分支沿用 `feat/service-bot-publish-ignore-ops-rel20260917`；目标分支进入发布流程前重新确认。
- Backend/Engine 配套交付；先升级 Engine 再开放 Backend 查询。旧 Engine 不支持 GET 应明确失败，不回退到变更接口。
- 回滚只撤销本任务新增 GET/查询协议与实现，保留现有 POST、取消签名和原地重启改动；无数据库和运行时文件迁移。
