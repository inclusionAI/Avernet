# 数字员工接入

Status: approved for implementation by the user's 2026-09-15 request.
Owners: Backend community contracts/domain, OCB enterprise adapters, legacy frontend.

## Scope

仅服务 Bot 可绑定。绑定关联 `ac_bots.id`，不依赖发布状态；需有可运行实例。
平台站点完成绑定，注册消息触发详情校验、执行身份重签与运行实例 token 更新。
owner、管理授权、租户、存储和容器寻址不改变。

企业端点、SOFAMQ、平台编码和密钥配置属于 OCB。公共默认不启用。
平台编码、Consumer Group、密钥引用暂留空；启用时必须校验，不猜测配置。

## Acceptance

- 草稿或线上服务 Bot 都可出现在可绑定列表；个人 Bot 不可绑定。
- 重复事件不会重复完成绑定；外部失败、持久化失败和注入失败不能报告成功。
- 列表数字员工标识来自绑定事实，不从发布状态推断。
- 非公开 MCP 依据 MCP Center 的 `accessLevel` 申请权限并显示实际审批状态。
  公开 MCP 不调用申请接口；未知公开性不视为公开。
- 草稿删除 MCP 不缩减线上共用 Passport；新增保留线上范围。
- 上线审批绑定发布版本和能力快照。通过才能继续已有发布流程；拒绝、撤销、
  重复、过期及不匹配消息不能上线；最终更新 Passport 能力集。
- 旧接口、OpenAPI、任务恢复和发布重试必须执行同一后端审批约束。
- 仅指定老前端增加相关交互；未绑定 Bot 的现有行为不改变。

## Validation

前置：锁文件独立 ARM Python 环境，执行身份与 Passport 现有测试 23 passed。
后续要求：领域/持久化/HTTP 协议/消息幂等/发布门禁/MCP scope/DI 回归，老前端
单测、类型检查及页面验证。真实平台联调需配置补齐，不能以测试替身代替验收。

## Boundary

遵循 `docs/arch/arch.rules.md`、`docs/arch/context-boundary-format.md`。
Service API 与平台 Plugin API 分离。关联和审批状态持久化，进程内状态不能
作为幂等、任务恢复或授权事实。配置关闭不得绕过已绑定 Bot 的审批约束。

## Progress

Implementation in progress. No deployment or real platform verification yet.

## 已补齐的实现

- 平台目录入口：`GET /openapi/v1/bots/metadata/digital-employees?creatorNo=...`、
  `GET /openapi/v1/bots/metadata/digital-employees/{agent_id}?versionStatus=draft|published|applying`。
  复用 OpenAPI principal 鉴权，Admission 为 OPEN；OPEN 不代表匿名访问。
  `agent_id` 为 ac_bots 主键，版本不影响绑定资格。保留平台文档的响应字段。
- 历史能力基线从原发布记录、portable config_artifact、文件产物与已有 Engine
  layout 读取；CLI 编码来自共享 passport 实际授权，不用可执行文件名冒充。
- 本地/Repo Skill 从完整内容制包，发布版本从冻结存储坐标或原产物读取；Center
  使用精确版本包。记录内容摘要，重签扫描 URL 不改变版本身份。
- 构建结束比对实际产物与草稿能力；审批固定该产物。原始产物已丢失、路径不可
  确认或内容读取失败时明确失败，不能用当前草稿冒充旧发布内容。
- 身份重签前崩溃恢复：已有 pending 时只激活；无 pending 时通过现有行锁创建
  pending 并重签，避免盲目重复外部调用。
- 回滚在修改线上发布记录前检查目标版本对当前员工的审批；既有未绑定 Bot
  沿用原流程。旧版本未经员工审批时应走正常构建/审批流程。

## 验证与待联调

- OpenAPI 鉴权、契约和文档/准入清单：79 passed。
- 数字员工领域与 DI 契约：56 passed；发布与架构问题修复定向组合：373 passed。
  各组合存在重叠，不相加为唯一测试数。
- 默认关闭功能的完整 HTTP 应用流程：1 passed。该流程不代表企业 MQ/平台联调。
- Gateway 生成器可生成新契约；相对 HEAD 无新增不兼容。已保存的 Gateway schema
  相对 HEAD 原有 7 处不兼容（run-template、CallerContext、Connection/Template
  schema、三个 summary 默认值），发布门禁拒绝更新，未使用 allow-breaking。
- 老前端已安装依赖并启动开发构建；完整类型检查存在原有错误。本地 auth/user
  代理目标 127.0.0.1:21000 拒绝连接，未完成登录后的服务 Bot 页面验收。
- platform_code、consumer_group、token_secret_name 仍按用户要求留空，默认关闭。
  SOFAMQ、平台签发与实际容器注入端到端仍须配置补齐后联调。
- 尚未提交、推送或部署。实现测试不能替代真实平台验收。

### 收尾回归补充

企业 HTTP/对象存储：57 passed。老前端 API：2 passed；同一依赖环境对比 HEAD
与工作区的 TypeScript 诊断，规范化路径/行号后没有新增源码错误（976→974），
但整体类型检查仍未通过，不能视为完整前端验收。
新增/修改 Python 文件 pre-push 同类 flake8 阻断规则检查通过（61 个文件）。
扩大组合包含 1,863 项通过；MCP 旧测试绕过构造器导致新增依赖未初始化，已修正
测试夹具并定向复测。`execution_identity` 已登记真实 HTTP 边界流程，没有添加豁免
或降低门禁。5 个新增 HTTP 端点均登记 happy/error endpoint case；数字员工默认关闭
应用流程已登记。
