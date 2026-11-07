---
agent: tc-review
status: approved
created: 2026-10-29
iteration: 1
task: service-bot-upgrade-draft-restart
---

# 系分 Spec：升级服务 Bot 发布单时触发草稿重启

## 需求概述

`POST /api/service-bot/publish/upgrade` 目前仅从成功发布单创建下一版本草稿并返回。新增行为：首次创建下一版本草稿后，对该发布谱系的源 Bot 发起一次普通草稿重启，使后续草稿编辑使用重启后的运行环境。接口返回不代表重启完成或健康检查通过。

用户已确认实现并授权新建 Avernet worktree、以 GitHub dev 为底 rebase 和提交 PR；不更新 OCB gitlink。实现和测试不调用真实升级或重启接口。

## 现状与证据

维护源为 Avernet `apps/backend/src/agentclaw/community`。OCB 的 `openocb/src/backend` 为旧副本，不参与本次实现。Avernet 当前 checkout 为 detached HEAD；GitHub remote 为 `inclusionAI/Avernet`，`origin` 为内部 fetch 镜像。

以下行号均相对于 Avernet 根目录，记录本轮现场源码：

| 位置 | 当前行为与含义 |
| --- | --- |
| `apps/backend/src/agentclaw/community/adapters/http/service_bot/router_publish.py:512` | 既有 `/upgrade` endpoint，使用 `CollaboratorPermissionInterceptor`，默认要求 ADMIN，并沿用编辑锁检查 |
| 同文件 `:163` | 从发布单解析 `source_bot_id`、`owner_id` 和 `source_bot_pk`，供权限拦截器定位 |
| 同文件 `:554` | 同步调用 `publish_service.upgrade_publish`，直接序列化发布单，没有重启调用 |
| `apps/backend/src/agentclaw/community/core/service_bot/services/bot_publish_service.py:904` | `upgrade_publish` 支持原单 SUCCESS 或已有后继单的幂等返回；创建新发布单后直接返回 |
| 同文件 `:973` | 后继单复用 `source_bot_pk/source_bot_id/publish_bot_id`，因此新增的是草稿发布记录，未创建新的 Bot |
| 同文件 `:195` | `create_publish` 按源 Bot 主键解析身份，持久化 `owner_id` 使用资源实体地址；不可把该字段直接作为真实负责人执行重启 |
| `apps/backend/src/agentclaw/community/core/bot_management/services/bot_service.py:4722` | 普通 `restart_bot` 校验真实 owner、生命周期和当前设备，并使用既有重启锁；teclaw 明确拒绝普通重启 |
| 同文件 `:5156` 附近 | 普通 service Bot 重启使用 DRAFT stage，且不使用发布态构建产物目录 |
| `apps/backend/src/agentclaw/community/core/service_bot/services/publish_flow/stage_status_mixin.py:19` | 发布态重启只映射 VERIFY/ONLINE，不支持 DRAFT |
| `apps/backend/src/agentclaw/community/core/service_bot/services/bot_publish_service.py:1454` | 个人转服务的旧链路跳过 teclaw：其草稿容器不依赖 bot_type，盲目销毁重建可能损失文件。该链路的 daemon thread 不作为本次可靠提交凭据 |
| `apps/backend/src/agentclaw/community/core/service_bot/services/service_publication_facade.py:507` | OpenAPI 发布升级同样调用 `upgrade_publish`，共享服务行为会一并变化，需要兼容性验证 |

## 编码 Spec

### 推荐行为与明确权衡

1. 仅在本次 `upgrade_publish` 真正创建了后继草稿时触发重启。两个既有幂等返回分支保持只返回已存在记录，重复点击不再次重启。
2. 重启对象是 `source_bot_pk` 定位的当前源 Bot。使用该 Bot 当前 `owner_id` 调用普通 `BotService.restart_bot`，不能使用调用者 ID、发布单资源实体地址或 `publish_bot_id + version` 猜目标。
3. 重启只作用于草稿运行态。禁止调用 `PublishFlowService.restart_bot`，禁止重启上一版 ONLINE 或 VERIFY 容器，禁止恢复/安装历史发布制品。
4. teclaw 跳过普通重启并记录明确原因，保留升级草稿成功行为。不得为本次需求增加 teclaw 重启能力。
5. 新草稿创建成功后，同步调用既有普通重启方法直到其返回提交结果，不额外等待容器健康终态。ARCA 分支后续分配由既有异步机制继续，BaaS 分支提交升级并由既有持久任务轮询。此处的“同步”不代表瞬时返回：既有 coding 备份/设备准备可能耗时；本次不增加等待循环或 daemon thread。
6. 提交调用失败时保留已创建的草稿，向 HTTP 调用者显式报告“草稿已创建，重启触发失败”，返回 `success=false`、业务 `error_code=500`，并在 `data` 中保留草稿发布单信息供恢复。保持现有 ApiResponse 的 HTTP/业务状态包装方式，不擅自改变 HTTP status 契约。
7. 失败后再次调用 upgrade 仍按既有幂等规则返回同一草稿，不自动补发重启；恢复使用现有源 Bot 手动重启入口。此权衡避免引入新的操作状态机及跨进程“恰好一次”承诺。接口失败不回滚或删除草稿，更不能因超时不明而自动销毁设备重试。
8. 重启方法返回 PENDING 或既有“正在处理”结果时表示已受理/复用进行中操作；不能写“重启成功”。返回值若包含显式失败，按触发失败处理，不以无异常替代业务判断。

### 既有链路、允许新增点、禁止触碰点

- 既有链路：HTTP 鉴权与编辑锁 → 发布服务的成功版本/后继单判断 → 创建 DRAFT → 返回发布记录。
- 允许新增点：发布服务新建草稿分支的普通重启接线、一个局部辅助方法、明确的部分成功异常及 endpoint 映射、必要契约文档和测试；OpenAPI 共享调用兼容性只做最小接线。
- 禁止触碰：普通重启内部设备策略、重启锁/TTL、任务队列通用去重、Relay、BCS、Bootloader、镜像安装、历史制品恢复、发布阶段枚举、数据库 schema。不得在 router 编排身份解析或设备选择。

### 关键方法抽象

| 方法 | 所在层 | 职责与边界 | 输入与输出 | 副作用 |
| --- | --- | --- | --- | --- |
| `BotPublishService.upgrade_publish` | 发布应用服务 | 保持后继草稿幂等；仅新建成功后触发草稿重启 | 原 publish_id、已授权 operator；返回 BotPublishRecord；提交失败抛局部领域异常 | 创建发布单、调用普通 Bot 生命周期 |
| `_restart_upgraded_draft`（名称可按现有风格调整） | 同一发布服务 | 解析源 Bot 当前身份，跳过 teclaw，调用既有 restart；不重新实现设备编排 | 新草稿记录与 operator；无新增公开返回 DTO | 读取 Bot；发起现有草稿重启；记录业务日志 |
| `UpgradeDraftRestartError`（建议名） | `publish_exceptions.py` | 表达已创建草稿但重启提交失败；携带草稿记录 | draft_record、可安全展示的失败说明 | 无网络或持久化 |
| `upgrade_publish` endpoint | HTTP adapter | 保留现有鉴权和 ApiResponse，最先捕获上述异常映射部分成功失败 | 请求 publish_id；ApiResponse | 入站边界日志 |

`BotService.restart_bot` 复用不修改；它负责自身状态/设备/引擎策略。发布服务必须在调用前使用真实当前 owner。普通生命周期的内部身份参数并不是授权证明；新增调用仍只能从已有 ADMIN/owner 鉴权和锁检查通过的入口执行。当前服务 docstring 暗示 owner 检查、实现却依赖 adapter/facade；本次应澄清该契约，不复制或扩大一套权限策略。

### 关键领域模型设计及字段说明

不新增持久化实体或字段。沿用下表明确身份与状态不变量；新增异常仅为部分成功结果载体。

| 模型/字段 | 类型/格式 | 必填 | 来源/约束 | 业务含义与兼容性 |
| --- | --- | --- | --- | --- |
| BotPublishRecord.id | int | 是 | 仓储分配 | 后继草稿稳定 ID，失败响应也可携带 |
| BotPublishRecord.last_pub_id | int | 是 | 原发布单 ID | 幂等后继关联；重试不能创建第二个草稿 |
| BotPublishRecord.source_bot_pk | int | 是 | 原单沿用 | 不可变源身份，主键解析必须与 source_bot_id 对齐 |
| BotPublishRecord.source_bot_id | str | 是 | 原单沿用 | 草稿普通重启目标 |
| BotPublishRecord.owner_id | str | 是 | create_publish 从源 Bot 资源实体地址规范化 | 保持现有序列化；不得误当当前负责人 |
| BotPublishRecord.status | str enum | 是 | 新单 DRAFT | 发起/失败的重启不把发布单改成 SUCCESS/FAILED |
| 当前 Bot.owner_id | str | 是 | 主键定位的当前 Bot | 传入 restart_bot.user_id，支持 entity_id 与 owner_id 不同及负责人迁移 |
| 当前 Bot.active_engine | str | 是 | 当前 Bot | teclaw 跳过，其余沿用生命周期原有能力 |
| UpgradeDraftRestartError.draft_record | BotPublishRecord | 是 | 已成功创建的后继单 | 非持久化，adapter 可生成既有 data；不包含凭据 |

关系与不变量：后继发布单仍属于同一源 Bot/环境；原版线上实例不受影响；发布状态与源 Bot 的 PENDING/ACTIVE/FAILED 是两个不同状态机；不把接口受理当健康成功。`config_artifact` 及镜像策略仍严格遵循 `create_publish` 既有规则，禁止继承旧版 binding/publish 引用。

### 外部系统边界日志

复用既有 logger，不创建递归脱敏器或全局过滤器。

| 事件 | 边界/阶段 | 非敏感字段 |
| --- | --- | --- |
| `service_bot.upgrade.request` | backend inbound POST `/api/service-bot/publish/upgrade` | publish_id、operator、现有 trace/request ID、route、method |
| `service_bot.upgrade.draft_restart.request` | 发布服务 → 既有生命周期 | original_publish_id、draft_publish_id、source_bot_pk/source_bot_id、operator、current_owner_id、engine、stage=draft |
| `service_bot.upgrade.draft_restart.response` | 普通重启方法返回 | 同上关联字段、受理/复用结果、Bot status、binding_id、耗时；仅完整枚举安全业务字段，不输出含凭据的整个 Bot 字典 |
| `service_bot.upgrade.draft_restart.skipped` | 幂等返回/teclaw | 关联字段、稳定 reason（existing_successor/teclaw_unsupported） |
| `service_bot.upgrade.draft_restart.failure` | 提交异常或业务失败 | 关联字段、异常类型、安全错误信息、状态码（存在时）、耗时、draft_retained=true |
| `service_bot.upgrade.response` / `service_bot.upgrade.failure` | HTTP 出口 | 新草稿 id/version/status/last_pub_id、安全响应业务字段、success/error_code、耗时、现有 trace |

实际 ARCA/BaaS 出站仍由已有生命周期服务负责，先核对其既有请求/成功/失败日志；若发现本次必经路径缺日志，仅在相应操作处补最小日志并说明不可替代性，不改通用传输层。不得直接记录 `str(request)`、完整 URL query、headers、完整 Bot/ext 字典。token、Authorization、Cookie、密码、secret/key/credential 不落日志；业务 sessionKey 如出现仍可明文定位。

### 文件改动范围

| 文件（Avernet 相对路径） | 类型 | 目的 |
| --- | --- | --- |
| `apps/backend/src/agentclaw/community/core/service_bot/services/bot_publish_service.py` | 修改 | 新建分支重启接线、当前身份解析、日志 |
| `apps/backend/src/agentclaw/community/core/service_bot/services/publish_exceptions.py` | 修改 | 部分成功异常 |
| `apps/backend/src/agentclaw/community/adapters/http/service_bot/router_publish.py` | 修改 | 失败响应与边界日志、接口说明 |
| `apps/backend/src/agentclaw/community/core/service_bot/services/service_publication_facade.py` | 仅必要时最小修改 | 共享升级调用的部分成功错误映射，禁止异常误映射权限失败 |
| 对应 service/router/facade 单测及 API 契约文档 | 修改/新增 | 见下方验收用例，文档明确副作用及恢复方式 |

### 验收标准

- 新建后继草稿时，支持普通重启的源 Bot 被触发一次；返回原有成功 data 结构，未等待健康终态。
- 两类已有后继分支均不触发重启；原单错误状态、未找到、权限/编辑锁拒绝均无重启副作用。
- entity_id 与 owner_id 不同时仍传真实 owner；owner 迁移后使用当前 owner，不重启他人的同名 default Bot。
- teclaw 返回草稿且明确跳过；不会 destroy、allocate 或调用普通重启。
- 首次重启提交异常/显式失败返回部分成功说明及草稿 data；草稿留存；升级重试不再重启，可用手动草稿重启恢复。
- 成功、失败、跳过日志可关联且不包含原始凭据；不修改线上阶段 binding。
- 相关单测全部通过；改动行覆盖率 >=90%、测试通过率100%；总行覆盖率门禁 >=70%。不通过排除/降低阈值绕过。

## Review Spec

- 核实每个改动文件都服务于上述允许新增点，业务编排不进入 router。
- 检查原有鉴权拦截器、ADMIN 与编辑锁行为保持；真实 owner 仅作为授权通过后的生命周期执行身份。
- 核实成功返回、已有草稿、部分成功失败、teclaw 四类语义；不得声称跨进程恰好一次或提交即健康。
- 禁止把发布单 owner_id 当真实 owner，禁止调用发布态 restart，禁止对幂等重试无条件重启，禁止吞异常仍报告重启成功。
- 检查 OpenAPI 共享调用影响，禁止把重启失败统一报权限403；最小映射或沿用其上游失败契约。
- 检查日志字段、敏感值与测试断言；无新增 unused imports/变量/孤儿代码，Python 切片 `:` 前无多余空格，block comment 使用 `# `。
- 编码后独立 reviewer 与本地回归均通过，才进入人工代码审核。真实远端 ACI 未运行时标未验证，不能沿用本地 coverage 冒充远端 PASS。

## QA Spec

| 编号 | 用例 | 步骤 | 预期 |
| --- | --- | --- | --- |
| TC01 | 新建升级 | 成功旧单、有效草稿源 Bot，调用 upgrade | 创建一条下一版 DRAFT，调用普通草稿重启一次；旧线上 binding 不变 |
| TC02 | 两类幂等返回 | 原单 SUCCESS+已有后继，以及 UPGRADED+已有后继 | 返回原后继；没有 restart 调用 |
| TC03 | 所有权/资源地址不同 | entity_id 与 owner_id 不同并模拟负责人迁移 | 按 source_bot_pk 定位当前 Bot，使用其当前 owner 重启 |
| TC04 | teclaw | 使用 teclaw 成功发布单升级 | 创建草稿，日志 skipped，无普通 restart/destroy |
| TC05 | 首次提交失败 | 新建后模拟 restart 抛错或显式失败 | success=false、业务500、草稿data留存，日志 failure；非403 |
| TC06 | 失败后恢复 | TC05 后再次 upgrade，再调用现有手动草稿 restart | upgrade 返回同一草稿且不重启，手动入口正常提交 |
| TC07 | 鉴权与锁 | 匿名、无权用户、他人持锁、合法ADMIN | 前三者无创建/重启；合法ADMIN通过后由真实owner身份执行 |
| TC08 | 无效原单 | 不存在、非SUCCESS且无合法幂等后继 | 原有业务错误，无重启 |
| TC09 | 提交与完成分离 | restart 返回PENDING/既有进行中结果 | 接口完成不等待health；发布单仍DRAFT |
| TC10 | 日志及凭据 | request/response/failure 各路径传入测试凭据哨兵 | 关联ID及业务字段存在；原token/Cookie/Authorization不出现 |
| TC11 | OpenAPI兼容 | 经 facade 的升级成功/失败 | 同样触发草稿重启，结构兼容；失败不冒充健康成功或权限失败 |
| TC12 | 历史数据边界 | 源Bot缺失、default历史主键缺失、源ID不匹配 | 使用现有地址规则或明确失败，不猜同名目标，不误重启 |

本地先跑发布服务/router/facade及已有普通restart相关单测，结合临时 SQLite/边界替身验证持久草稿留存。预发黑盒需指定可重启的测试 Bot、已成功版本和正式实例基线，真实重启在用户代码审核并授权部署后执行；不能拿用户 URL 中凭据调用当前实例验证。

## Ship Spec

- 编码前创建独立 Avernet worktree，路径必须位于 `/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees`，从核验后的 GitHub dev 基线创建；现有 checkout 不编码。
- 分支建议 `fix/service-bot-upgrade-draft-restart`，目标分支由主流程后续确认。
- 代码审核通过且用户确认后部署 Backend 预发，不要求修改 Engine、Relay、Bootloader。
- Avernet 源码 push/PR 只去 GitHub inclusionAI/Avernet；镜像确认同步精确SHA后再单独制作 OCB gitlink PR；两套证据分开。
- 回滚 Backend 对应代码即可取消升级后的自动触发；已创建草稿不删除，已提交重启不能通过代码回滚撤回，需要核查当前Bot状态。
- 无 PR/job 时 ACI 标未运行；有实际 job 才记录通过率100%、总行>=70%、变更行>=90%的分子分母及证据。

## 待人工确认

推荐确认任务名 `service-bot-upgrade-draft-restart` 及上述最小方案，尤其：只新建时触发；teclaw跳过；提交失败保留草稿并显式报错，后续通过手动草稿重启恢复。若要求“进程退出也自动补发”或“失败后upgrade自动重试”，需另行批准持久化操作状态及重试范围，不能以一个 daemon thread 或现有短期重启锁声称满足。
