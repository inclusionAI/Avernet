---
agent: tc-review
status: completed
created: 2026-09-06T00:00:00+08:00
iteration: 1
---

# 通用 Token 换签与 BaaS 注入 Spec

## 1. 需求与范围

在不修改现有 Caller 业务实现和调用方 HTTP 契约的前提下，为 `/api/v1/token/iam` 增加可插拔 Token 插件编排。本期仅支持 `personal` Bot 的 JIT Token：命中上层传入且可信的用户名单与 Bot 类型后，准备 JIT 请求参数，调用 JIT HTTP 接口，再通过 BaaS append 注入 `x-jit-token`。

现有 Caller 必须先执行；无论 Caller 成功、失败或返回错误 Outcome，插件流水线均继续执行。插件之间相互独立，任一插件失败不得改变 Caller 结果、异常语义或阻断后续插件。

## 2. 既有链路

`/api/v1/token/iam` → `CallerIamTokenServiceProtocol.get_iam_token()` → 现有 Caller 匹配/换签 → BaaS `outbound-rule` 注入 `x-caller-token` → 返回既有 `CallerIamTokenOutcome`。

现有 `ac_entity_user_list`/`UserListServiceProtocol` 可提供环境隔离名单查询；现有 `RuntimeBindingResolutionService` 与 BaaS 服务提供 runtime binding、provider device 与 outbound append 能力；企业 Passport 插件通过 tcauthmng Facade 可查询 `agentCode`。

## 3. 目标流程

```text
先执行既有 Caller
  → 保存 Caller outcome 或 exception
  → TokenPluginPipeline 按注册顺序执行每个插件
      1. match
      2. prepare_exchange_token_request
      3. exchange_token
      4. append_to_outbound
  → 原样返回 Caller outcome 或重新抛出 Caller exception
```

单插件异常在插件边界捕获并转换为 `FAILED`，不向 Caller 传播。

## 4. 中立协议与模型

新增中立 `core/token_exchange`：

- `TokenExchangeContext`：只承载上层提供的可信 bot/user/entity/stage/env/binding/request 参数。
- `PreparedExchangeTokenRequest`：承载 endpoint key、HTTP method、headers、body、超时、响应 parser、BaaS target/header。
- `ExchangedToken`：仅进程内传递 token value；附带 expiry/fingerprint/非敏感 metadata。
- `TokenPluginResult`：plugin code、status、failed stage、稳定 error code，不携带 token。
- `TokenExchangePlugin`：四个阶段方法。
- `TokenPluginPipeline`：串行执行插件，隔离单插件失败。
- `CallerTokenExchangeOrchestrator`：Caller first，再运行 pipeline；保持既有 Caller 协议和返回语义。

本模块不得导入 corp plugins；具体 JIT 实现通过 DI 注入。

## 5. JIT Plugin

### match

仅当以下条件同时满足时命中：

- `context.bot_type == "personal"`；
- `UserListServiceProtocol.is_in_user_list(entity_id=context.user_list_entity_id, user_list_type="token_exchange_jit", env=context.env)` 返回 true。

用户/实体标识由上层传入，插件不重新解析 Cookie/Header，也不新增 YAML user id 名单。

### prepare_exchange_token_request

通过一个统一的 `prepare_exchange_token_request` 方法准备：

- `source`：受管配置；
- `acraId`：当前 personal Bot runtime 对应的裸 ARCA sandbox ID；
- `agentCode`：tcauthmng `AgentPassportFacade.queryAgentPassport`；
- `bizTraceId`：上层 request id；
- BaaS `paas_device_id`；
- outbound header `x-jit-token`、action `set`；
- outbound domains 直接复用后端固定的 `CALLER_TOKEN_DOMAINS`，不维护第二份名单；
- response parser。

本期不获取 AgentToken，不调用 `queryToken`，不自行增加 Authorization。

### exchange_token

调用配置中的 JIT endpoint：

`POST /api/tbac/token/issue_or_renew.json`。

严格校验 HTTP 状态、`success == true`、成功 errorCode、非空 jwt、未来 expiredTime、`ISSUED|REUSED|RENEWED` tokenAction。Token 仅内存传递。

### append_to_outbound

调用既有 BaaS append：

`PUT /api/v1/paas/devices/{paas_device_id}/outbound-rule?mode=append`

请求规则：`header_name=x-jit-token`, `action=set`, `value=<jwt>`, `domains=CALLER_TOKEN_DOMAINS`。应用层不读取旧规则、不合并/去重、不回滚，依赖 BaaS 幂等。

## 6. 允许与禁止修改边界

允许：

- OCB `src/backend/src/agentclaw/community/core/token_exchange/*`（中立协议/编排，如代码位于社区子模块则保持其独立修改边界）；
- OCB `src/backend/src/agentclaw/corp/plugins/prod/token_exchange_jit/*`；
- OCB corp DI 的最小绑定；
- BaaS append 的通用内部抽象，但必须保持 `append_caller_outbound_rule` 兼容；
- 对应 unit/integration/contract tests；
- 本任务 spec、编码/Review/回归报告与根目录 `log.md`。

禁止：

- 修改现有 Caller 核心业务语义；
- 修改现有 `/api/v1/token/iam` HTTP 响应契约；
- 修改 Relay、OpenClaw、Engine runtime、前端；
- 修改 tcauthmng Facade 契约；
- 新增 AgentToken 获取链路；
- 将名单、URL、Header 注入参数暴露给用户请求；
- 记录 token、Authorization、Cookie 或完整 HTTP body。

## 7. 日志与安全

外部 JIT HTTP 和 BaaS append 必须分别记录 start/success/failure，字段包含系统、操作、method/path、request id、非敏感入参、状态码、耗时、结果码。Token 只记录 presence、fingerprint、expiry seconds/action；递归脱敏所有凭据字段。测试必须断言成功/失败日志与原始 token 不出现在日志中。

## 8. 测试与验收

- Caller first 的顺序测试；Caller success/failure/exception 与 JIT success/failure 组合测试；
- 四阶段顺序和阶段失败短路测试；插件失败不影响其他插件；
- personal 命中，service/其他类型跳过；名单按 entity/type/env 精确隔离；名单错误只影响插件；
- JIT 请求字段、严格响应解析、超时/5xx/4xx/空 token/过期 token/未知 action；
- BaaS append path/query/body/header/action；不覆盖 x-caller-token；
- queryToken/AgentToken 未调用；
- 日志脱敏；import/ruff/architecture checks；既有 Caller 定向回归。

## 9. Ship/QA

仅在 Review 与本地回归均通过且用户确认后部署。远端发布需验证真实运行路径、BaaS append 与 personal Bot 黑盒行为；JIT 外部服务不可用时应验证 Caller 仍保持原行为并产生独立 JIT failure evidence。
