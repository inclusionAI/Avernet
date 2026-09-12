# 任务 Singlebox 集成链路同构化

## Summary

以生产任务依赖实现为唯一行为基线，将任务模块的 Singlebox 链路升级为可信预演环境。任务内核与执行流程在所有部署中保持相同；受校验的配置和可插拔 integration plugin 仅选择当前环境部署的应用服务、凭据来源和测试租户。若 singlebox 引擎尚未具备生产契约所需能力，仅在任务模块的依赖注入点使用 production-contract mock，不影响引擎的其它正常 singlebox 链路。

**最终结果：**任务模块在 singlebox 完成功能和生产同构验证后，发布到生产只可替换部署配置、endpoint、credential reference 与 tenant reference；不得再为上线修改任务业务逻辑、执行流程、状态机、派发策略、权限适配、重试或 callback 分支。

## Motivation

当前任务 Singlebox 已覆盖单 Bot、协作群和 BBS，但其单 Bot、BCS 鉴权、Bot 入网/可见性及协作群收敛存在独立实现、跳过或 double。生产部署时才暴露 BaaS API-key、授权名单、人类会话、BCS caller 身份、可达性及回调权限差异，造成重复适配与上线风险。Avernet 是共享真内核；OCB 仅承载公司中间件的扩展实现，且 `ocb-public` 是 Avernet 的只读镜像。

## User Stories

- As a 任务开发者, I want Singlebox 和生产使用相同的集成行为契约, so that 本地通过能代表真实集成可用。
- As a 平台运维者, I want 启动时就知道哪些外部能力和身份链路已配置, so that 不会在任务执行中才发现权限缺失。
- As a 测试维护者, I want 每个替身与生产能力的差异可见且被限制, so that double 不能伪装成端到端验证。

## Acceptance Criteria

- [ ] 单 Bot、协作、回调和候选发现通过环境无关的对外集成接口；环境差异仅在组合根和已验证配置中选择。
- [ ] singlebox 只调用 singlebox 部署的 Engine、BCS、Backend 等应用服务；生产只调用生产部署的对应应用服务；任务模块不得跨环境寻址或以 localhost/生产 URL 作为隐式回退。
- [ ] Singlebox 不再以专用直连实现替代生产单 Bot 调用、授权检查或运行结果协议；能力缺失须在前置检查明确失败。
- [ ] 单 Bot 的投递、查询、取消、授权检查及失败分类在两端使用同一行为契约并有双环境契约测试。
- [ ] 协作群创建、caller 身份、成员可达性、会话/状态机查询和任务回调使用同一请求、鉴权和响应契约。
- [ ] 所有任务接口声明身份类型、凭据来源、作用域、过期及预期 401/403 语义。
- [ ] 模拟替身只用于标识为模拟的单元/契约测试，不能使真实 Singlebox E2E 或覆盖门禁通过。
- [ ] E2E 输出脱敏集成证据；配置对未知、缺失或不兼容能力快速失败。
- [ ] 任务执行主流程、任务图状态迁移、poller、超时、重试及 callback ingress 不因 plugin 类型而改变；mock 仅模拟外部服务的生产契约结果。
- [ ] OCB 只能为 Avernet 定义的任务 Port 提供 corp plugin 与配置覆盖，不能复制或改变任务核心语义。
- [ ] 生产发现的任何任务业务或权限问题，都能在相同 Port contract 的 singlebox 或受管集成测试中复现；不能以生产专用任务逻辑分支修复。
- [ ] 发布生产前，所有关键依赖都已标记为 `auth_mode=enforced`；无权限、错误 scope、过期凭据、错误签名及不可达 Bot 的负向测试均已通过。

## In Scope

- 收敛任务的单 Bot、协作群、BCS 回调、候选发现和授权依赖。
- 定义环境无关的集成、身份和能力配置契约。
- 调整 Singlebox 装配、启动预检、测试夹具、契约/集成/E2E 验证与证据输出。

## Out of Scope

- 改变任务图、规划、派发或验收的产品语义。
- 重建 BaaS、BCS、BCN 或 Bot 引擎本身。
- 将真实生产凭据、内部端点或员工身份写入仓库或 CI。

## Open Questions

- 是否存在可隔离的 BaaS OpenAPI 测试租户？
- 本地 BCS 能否启用与生产等价的 HMAC、Bot bearer 与 Principal 校验？
- live E2E 的最小权限 fixture 由哪一个受管凭据发行方创建与轮换？
