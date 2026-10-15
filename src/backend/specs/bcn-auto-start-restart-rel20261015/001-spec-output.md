---
agent: tc-review
status: completed
created: 2026-10-15T12:09:36+08:00
iteration: 1
---

# 系分 Spec: Bot 启动与重启自动接入 BCN

## 需求概述

基于 GitHub `inclusionAI/Avernet` 的远端 `REL20261015`（初始基线 `1ce85adc6`）创建隔离 worktree，实现所有进入合法 `start_bot` / `restart_bot` 执行路径的 Bot 自动尝试接入 BCN。仅 `active_engine == "openclaw"` 且 `bot_type == "personal"` 使用 `connection_mode="plugin"` 的插件上行方式，其他组合均使用既有 Provider 下行默认方式。参考历史 session `019f133e-86de-7bb1-a7ec-a81899979a41` 的开发背景，当前需求取代历史 normalCC 限制及不修改 restart 的决定。完成本地验证、独立评审、以最新远端 REL20261015 为底 rebase、创建同分支 PR 并修复可复现 CI 单测错误；本任务未指定部署环境，不执行生产或预发部署。

## 编码 Spec

### 功能点

- [ ] `start_bot` 不按 engine、bot_type、template_type、模板 capabilities 或历史 `bcn_registered` 标记筛选入网资格。
- [ ] 重建设备的 restart 通过 `start_bot` 完成一次入网尝试；BaaS 原地 restart 及编码策略的 BaaS 准备路径同样尝试入网。
- [ ] start/restart 自动入网不受旧 `ClaudeCodeBcnRegister.enabled` 禁用值限制；create 保持原有资格与 DRM 语义。
- [ ] OpenClaw personal 使用 plugin，其余组合保留省略 `connection_mode` 的既有 gateway 下行契约。
- [ ] BCN 注册失败、环境跳过、成功标记写库失败均不阻断生命周期；注册成功（含409幂等成功）仅写 `ext.bcn_registered=True`，不落 token。
- [ ] 诊断日志覆盖模式选择、请求、成功、失败、环境跳过及 ext 更新失败。

### 技术方案

#### 既有链路与缺口

1. `BotService.start_bot` 读取 owner Bot 和模板后调用 `_should_register_bcn_provider`，通过时才调用 `_register_bot_to_bcn_as_provider`，随后异步分配设备。
2. `_should_register_bcn_provider` 同时供 create/start/restart 使用。显式模板 capabilities 是唯一资格源；旧数据按 engine/template 白名单判断，因此 architect、applicationCoding、显式 capability=false 等组合可能被排除。
3. `_register_bot_to_bcn_as_provider` 内部仍检查旧 DRM。`_is_claude_code_bcn_register_enabled` 实际默认开启，显式关闭值才禁用；旧注释“默认关”已与实现不符。
4. replacement restart 最终调用 start；`_restart_bot_baas` 不经过 start；`engines/aicoding/restart_request.py::AicodingBaasPreparation.prepare` 又存在一套实际使用的编码引擎准备逻辑，必须同时覆盖。
5. `_resolve_bcn_provider_connection_mode` 已精确实现 openclaw+personal→plugin，其他→None。`BcnService.register_provider_bot` 将 None 序列化为省略字段，BCN 采用 gateway 默认值。
6. `bcn_registered` 当前是成功诊断标记，不是可靠的实时入网凭据；已有 true 不应抑制幂等调用。

#### 最小改动与边界

直接在既有 start 与两条 BaaS restart 准备路径移除旧资格筛选，统一调用现有注册 helper。为 helper 增加有明确默认值的内部 keyword 参数（例如 `respect_drm: bool = True`）；create 不传参数，生命周期自动入网显式传 False。无需新增通用管理器、接口或持久化模型，不修改共享能力解析规则。

入网先于设备分配或 BaaS 请求提交；失败沿用 best effort 策略。保留 `BcnService` 环境和 provider 凭据配置边界：pre/prod 依既有配置调用，dev 仍需显式 provider_registration_enabled；缺配置返回 skipped，不能伪报已入网。此处“所有”指资格组合均可尝试，不承诺外部服务不可用时完成注册。

保留现有 owner 校验、生命周期状态校验、重启锁、设备 provider 选择、恢复任务及 durable recovery intent 的幂等早返。已进行中的重复请求不新增重复发布/设备分配；不为了入网放开 teclaw/desktop 等已有禁止的 restart 操作。不修改服务 Bot 发布实例重启接口、BaaS/BCS协议、Relay、Engine 或插件运行实现。OpenClaw plugin 仍使用原有 Provider 注册端点的 plugin 模式，不另造 onboard 流程。

### 外部系统边界日志

沿用现有 logger，仅在受影响的 `BcnService.register_provider_bot` 边界补充必要字段。事件可沿用稳定方法前缀并增加固定 event 字段，不引入通用日志模块。

| 系统/方向/操作 | 事件 | 非敏感字段与结果 |
|---|---|---|
| BotService→BCN/模式选择 | lifecycle registration requested | bot_id、owner、active_engine、bot_type、template_type、connection_mode、操作来源、DRM是否适用 |
| BCN/出站 POST `/providers/{provider_id}/bots` | request | provider_id、provider_bot_ref、method/path、name、summary、owners、connection_mode、已有trace/request ID（存在时） |
| BCN/成功，含409幂等 | success | 同一ref/provider、status_code、elapsed_ms、bot_uuid、idempotent_replay及响应非敏感业务字段 |
| BCN/异常 | failure | 同一ref/provider、elapsed_ms、error_type、status_code（存在时）、安全的错误信息及非敏感错误响应 |
| 本地跳过/回填失败 | skipped / ext update failed | bot_id、owner、connection_mode、reason；不得将skip写成成功 |

认证 token、Authorization、Cookie、密码与密钥不明文记录；响应中的 bot_runtime_token 只记录是否存在。sessionKey/session_key 是业务定位字段，可明文。遵守 tc-main 2026-10-11 约定，按实际字段使用简单既有遮蔽，不扩张为专用递归脱敏或全局过滤基础设施；不输出未经处理的 headers、响应原文或异常对象而泄露凭据。

### 关键方法抽象

| 抽象/方法 | 所在层或模块 | 职责与边界 | 输入与输出 | 协作对象与副作用 |
|---|---|---|---|---|
| `BotService.start_bot` | bot_management 应用服务 | 合法启动时尝试入网再触发原设备分配，不增加资格白名单 | 既有bot_id/user_id等→既有Bot结果或原业务错误 | repository/template、BCN helper、异步分配 |
| `BotService._restart_bot_baas` | bot_management 应用服务 | 原地重启准备时尝试入网，保留durable intent早返 | 既有Bot/binding→既有重启结果 | BCN helper、BaaS、设备/任务持久化 |
| `AicodingBaasPreparation.prepare` | coding 引擎策略准备层 | 编码策略BaaS准备同样执行自动入网，不重复发布 | 既有Bot/binding→PreparedBaasRestart或BaasRestartInProgress | 复用service helper、模板、BaaS请求构造 |
| `BotService._register_bot_to_bcn_as_provider` | bot_management 应用服务 | 按调用方契约选择是否保留旧DRM；注册及best effort成功回填 | 既有业务参数、内部bool策略→None | BcnService、update_bot_ext |
| `BcnService.register_provider_bot` | 既有BCN外部边界 | 原协议请求、409幂等及可观测性，不决定Bot资格 | provider ref/name/summary/mode→既有dict或BcnServiceError | HttpClient；POST BCN |

start/restart 调用方必须明确选择自动入网语义；create 默认调用契约保持兼容。注册 helper 承担失败隔离，调用方不复制try/catch和持久化逻辑。模式选择沿用 `_resolve_bcn_provider_connection_mode`；如不修改其实现，只在测试中锁定原契约，不增加无行为抽象。

### 关键领域模型设计

不新增实体、数据库列或API字段。现有Bot聚合和BCN注册命令足以表达需求；变化是生命周期资格策略，以下字段契约必须保持：

| 字段 | 类型/格式 | 必填 | 默认值或约束 | 来源/所有者 | 字段说明与兼容性影响 |
|---|---|---|---|---|---|
| Bot.active_engine | str | 沿用现有 | start默认值沿用原实现 | Bot配置 | 只用于选择plugin特例，不作为入网资格白名单 |
| Bot.bot_type | str | 沿用现有 | 缺失时personal，沿用原行为 | Bot配置 | 与openclaw组合决定plugin |
| Bot.template_type / capabilities | 既有类型 | 否 | 原样保留 | 模板 | 不影响start/restart自动入网；create仍按原能力规则 |
| Bot.owner_id | str | 是（已有caller兜底） | owner_id或user_id | Bot所有者 | provider identity使用owner，不把operator当其他owner |
| Bot.ext.bcn_registered | bool | 否 | 无新增默认值 | 注册helper | 仅成功回填True，环境skip/失败不新增True；不是跳过后续尝试的依据 |
| 注册命令.connection_mode | Literal["plugin"] 或 None | 否 | None省略JSON字段 | 生命周期模式选择 | 只有openclaw+personal为plugin，其他保留下行默认协议 |
| 注册响应.bot_runtime_token | str | 取决于既有响应 | 原样返回既有service调用方 | BCN | 不落Bot.ext，不明文日志 |

**关系与不变量**：`provider_bot_ref == bot_id + ":" + owner_workno`；每条实际启动/重启路径按对应模式尝试；无凭据不调用真实服务且不写成功；409是幂等成功；BCN失败不改变生命周期成功/失败语义；已有重启幂等恢复不触发第二次发布。

### 文件改动范围

以下均相对于本worktree；实际仅修改需要的测试文件，不为目录列表扩张改动。

| 文件路径 | 改动类型 | 改动说明 |
|---|---|---|
| apps/backend/src/agentclaw/community/core/bot_management/services/bot_service.py | 修改 | start/原地restart取消资格门控；helper明确保留create DRM策略；诊断日志/注释 |
| apps/backend/src/agentclaw/community/core/bot_management/engines/aicoding/restart_request.py | 修改 | coding准备路径取消资格门控，显式自动注册 |
| apps/backend/src/agentclaw/community/core/bot_management/services/bcn_service.py | 修改（必要时） | 仅注册方法边界日志补齐；原HTTP契约保留 |
| apps/backend/tests/community/core/bot_management/services/test_bot_service_stop_start.py | 修改 | start全组合、失败与旧DRM边界 |
| apps/backend/tests/community/core/bot_management/services/test_bot_service_restart_baas_envs.py | 修改 | 原地restart模式及兼容回归 |
| apps/backend/tests/community/core/bot_management/services/test_bot_service_create_bcn_register.py | 修改（必要时） | create未扩大且DRM仍有效 |
| apps/backend/tests/community/core/bot_management/services/test_bcn_service_register_provider.py | 修改 | HTTP载荷/幂等/失败日志凭据保护 |
| apps/backend/tests/community/core/bot_management/aicoding/ 下相关restart测试 | 修改/新增 | coding准备路径及恢复早返验证 |
| src/backend/specs/bcn-auto-start-restart-rel20261015/ | 新增 | 本任务spec、编码/评审/回归/PR报告 |
| /Users/helloworld/Desktop/codes/teamclaw/log.md | 追加 | 根agent记录本次改动与交付结果 |

### 验收标准

- [ ] start矩阵覆盖openclaw personal/service/其他、claude_code、aicoding、teclaw、未知引擎，均按模式尝试；模板缺失/false能力不再阻止。
- [ ] replacement restart、普通BaaS restart、coding策略BaaS prepare均覆盖，保留owner与幂等语义。
- [ ] start/restart在旧DRM显式关闭时仍尝试；create不受本次扩张影响。
- [ ] BCN成功/409回填True；异常和skip不阻止原分配/重启，回填失败也不阻断。
- [ ] 请求/成功/失败日志与凭据保护有行为断言。
- [ ] 受影响单测全部通过；用pytest --cov --cov-report=term-missing实测并报告改动文件覆盖率及变更行覆盖率，新增/变更行为覆盖率>90%；既有大型文件整体覆盖不足须如实记录，不排除生产代码或降低仓库CI阈值。
- [ ] 无新增未使用import/变量，无Python冒号前空格/block comment格式问题，git diff --check通过。
- [ ] 新增源码文件≤1000行；既有超长文件不得因本任务大规模拆分，按仓库现有门禁如实验证。
- [ ] 最新远端REL20261015在底、本任务提交在上；只向inclusionAI/Avernet创建源码PR并处理实际CI失败，不向内部镜像发源码PR。

## Review Spec

### 关注点

- 新策略仅作用于start/restart，create能力规则与旧DRM行为保持。
- coding异步准备与同步BaaS路径均已覆盖，没有凭目录名漏掉共享claude_code/aicoding策略。
- plugin特例精确；None仍按既有协议省略字段，不发明downlink枚举。
- 入网外部失败隔离、owner identity、成功回填、请求幂等不受破坏。

### 检查项

- [ ] 关键方法边界、输入输出、错误和副作用符合上表。
- [ ] 既有模型不变量及默认值/序列化兼容保持。
- [ ] start/restart不受template/capability/旧DRM及bcn_registered阻断。
- [ ] create未被共享helper变更扩大资格。
- [ ] 现有状态/权限/重启锁/durable intent早返及禁止操作保留。
- [ ] 变更行为覆盖率>90%；远端实际casePassRate=100%、总行覆盖率≥70%、变更行覆盖率≥90%，按平台真实数据记录分子分母，pending不写PASS。

### 不可接受的模式

- 为本需求放开teclaw/desktop原本拒绝的restart，或扩张到发布实例接口、Relay/Engine/BCS等无关范围。
- 改共享资格helper使create也无条件注册；硬改capabilities实现绕过所有业务消费方。
- 因BCN超时失败中止正常启动；以bcn_registered=True跳过幂等入网；把环境skip标记成功。
- 新建通用日志脱敏框架、明文打印token/headers、无依据修改下行协议。
- 未使用import/局部变量或本次残留孤儿代码；Python `:` 前空格或block comment未以`# `开头。
- 通过删除行为断言、降低阈值、coverage排除或pragma规避CI失败。

## QA Spec

### 测试用例

| 编号 | 用例名称 | 操作步骤 | 预期结果 |
|---|---|---|---|
| TC-01 | plugin选择 | openclaw+personal执行start及可用BaaS restart | 入网先于分配/更新且connection_mode=plugin |
| TC-02 | 全组合下行 | 参数化其他engine/bot_type/template组合执行start | 均注册，connection_mode不传，owner/ref正确 |
| TC-03 | 能力与模板兜底 | capabilities=false/缺失、模板读取异常、非normalCC | start仍尝试，保持原设备配置行为 |
| TC-04 | 旧DRM隔离 | 显式关闭DRM分别执行start/restart/create | 前两者仍注册；create保留不注册 |
| TC-05 | replacement restart | 合法ARCA/重建路径执行restart | 经start尝试一次，provider/锁/分配沿用原语义 |
| TC-06 | BaaS原地重启 | 普通BaaS与coding prepare各执行一次 | 对应模式注册，不漏coding策略；不额外destroy/release |
| TC-07 | 已注册重试 | ext.bcn_registered=True，BCN返回409 | 不跳过调用，按幂等成功回填，不重复创建Bot |
| TC-08 | 外部失败 | 注册抛BcnServiceError/一般异常，分别start/restart | 记录安全失败，原生命周期继续 |
| TC-09 | 回填失败 | 注册成功但update_bot_ext失败 | 记录警告，原生命周期继续 |
| TC-10 | 环境skip | 缺provider配置/不允许环境 | 不发HTTP、不写成功标记，生命周期继续 |
| TC-11 | 重复/拒绝操作 | durable intent/锁竞争/REACTIVATING及不合法操作 | 保持原早返/拒绝，无第二次发布或设备分配 |
| TC-12 | HTTP及日志 | 注册成功、409、4xx/5xx、超时；响应含测试token | 载荷契约正确，请求/成功/失败有ref/status/耗时及非敏感字段，原token不出日志 |
| TC-13 | create回归 | 跑原create资格矩阵及DRM测试 | 原限制保留；本次仅生命周期扩张 |

### 前置条件

隔离worktree及可用Python依赖；使用项目既有fake/mocks隔离HTTP、DRM、repository和设备/BaaS。测试不依赖真实用户、生产凭据或生产注册。无指定部署Bot，本任务不宣称线上入网验证。

## Ship Spec

### 部署目标环境

- [x] 本地隔离测试与PR验证
- [ ] 线下真实服务部署（不在本次授权目标内）
- [ ] 预发/生产部署（未指定）

### 分支策略

- 开发worktree：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/bcn-auto-start-restart-rel20261015`。
- 源码仓库：GitHub `inclusionAI/Avernet`；remote名称由主agent实测。
- 底分支：最新远端`REL20261015`；topic为本次开发提交。rebase前fetch并记录SHA，核对base..HEAD只有本任务增量。
- PR目标：`REL20261015`，英文语义标题及Problem/Solution/Validation说明；不自动合并。
- 读取实际head checks/jobs并修复可复现test/build/lint错误；基础设施不可用或pending单独报告。

### 回滚方案

合并前可关闭/撤回PR但不自动执行；合并后通过正常reviewed revert恢复本次生命周期资格与日志改动，无schema迁移。已发生的BCN注册不由代码回滚自动注销，避免删除业务身份；如需清理必须另行明确对象和授权。
