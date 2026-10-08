---
agent: tc-review
status: completed
created: 2026-09-21T16:00:00+08:00
iteration: 1
---

# 系分 Spec：服务 Bot 运行目录递归文件计数

## 需求概述

为已获管理权限的用户提供 `GET /api/service-bot/publish/ops/file-count`，读取指定服务 Bot 当前 draft、verify 或 online 实例实际运行目录中的普通文件数量。各实例独立返回结果，失败数量为 null；不累计多个副本、不读取发布物、不应用 publish/build ignore。用户已批准此前草案并要求实施，本 Spec 固化已授权需求，无须重复确认。开发基线为 `github/REL20260922`，SHA `b2014be448`；本任务不部署。

## 编码 Spec

### 功能点

- [ ] Query 必填 `bot_id`、`entity_id`、`stage`、`path`；stage 仅 draft/verify/online；不接受 version 定址。
- [ ] 现有登录身份、超级管理员或 Bot ADMIN 管理权限、Bot/entity 归属及 service 类型检查通过后才能读取运行实例。
- [ ] 使用 `RuntimeBindingResolutionService.resolve`，显式 `RuntimeBindingTarget.CALLER_SERVICE`；draft 复用 Bot 当前主绑定；verify/online 复用当前阶段绑定规则。
- [ ] BaaS 获取当前设备快照并逐个固定 device_uuid；ARCA 使用当前绑定；无实例为错误，不以空列表 all() 返回成功。
- [ ] 递归统计普通文件：隐藏文件纳入，ZIP 算一个，硬链接按目录项计数；目录、符号链接和特殊文件不计数，不跟随链接。
- [ ] 路径限定当前引擎授权根：支持相对根路径和根内绝对路径；禁止越界及任意链接穿越。扫描途中任何不可确定的 I/O 失败不返回部分计数为成功。
- [ ] 有限并发、有限等待及可终止扫描；请求取消和超时后回收执行单元与文件描述符。

### 技术方案

Backend 使用独立 Service API、kernel 值类型、运行时 Plugin API 和 DI 装配。现有 `PublishIgnoreService._resolve_targets` 仅作为行为参考，不调用其私有方法，也不借用 publish-ignore DTO。复用其已有权限服务和设备基础设施，避免增加新认证体系、Shell 入口、数据库字段或部署依赖。

业务调用顺序：HTTP 参数解析与登录认证 → FileCountService 管理权限与当前绑定解析 → FileCountRuntime 当前设备快照与固定实例调用 → Engine `/api/file/count` → `EngineManager.file` → `FileService.count_files` → 当前引擎文件插件 → 有截止时间的安全扫描。

Engine 新增 `GET /api/file/count?path=...&request_id=...`，成功 `ApiResponse(success=true, data={path, file_count, elapsed_ms})`。path 为实际采用的根内路径，file_count 为严格非负整数（bool 不是合法整数），elapsed_ms 为非负耗时。不返回文件清单、大小或内容，不借用 `list_dir` 生成清单再计数。沿用现有 FILE_LIST 能力和运行时连接认证；不支持文件能力的引擎明确返回 unsupported。

OpenClaw 路径根取现有运行时根配置（workspace root 的父目录）；返回 path 保留原始请求值，不做 trim 或绝对化。Claude Code 当前多 file roots 没有规范主根，因此明确返回 501/unsupported，不猜测 cwd 或首个根；LocalOpenClawPluginImpl 是无物理授权根的内存测试 double，也明确 unsupported。真实本地文件系统合同使用生产 OpenClawPluginImpl 加临时根验证。Backend 不硬编码 `.openclaw` 等物理布局。现有 `_convert_path` 只做历史地址翻译，不能作为本接口安全边界。

扫描器从可信根目录 fd 开始，逐段 `open(..., dir_fd=..., O_DIRECTORY|O_NOFOLLOW)`，目录项 `stat(..., follow_symlinks=False)`；遍历时通过父 fd 打开子目录，不在校验后重新使用拼接绝对路径。根路径组件和最终目标也不得经过未核验链接。拒绝 NUL、空路径、父级越界及根外绝对地址；`.` 代表根。无需读取普通文件内容。并发删除、权限失败和目录替换导致的不可确定扫描返回失败，不吞错返回少计结果。计数仅是扫描期间观测值，不提供原子快照保证。

扫描必须在可取消且可回收的执行单元内完成。优先独立子进程并在取消/硬截止时 terminate/kill、等待退出和关闭 IPC；若使用合作取消，必须证明阻塞文件系统调用不会导致无限后台占用。单纯 `wait_for(to_thread(scan))` 不满足要求。并发槽应在实际扫描结束后释放，排队时间也有限；超时必须覆盖整个请求的排队与扫描阶段。超时和并发值由实现定义具名常量或已有配置加载机制提供，写入契约文档并测试，不能在 core 读取环境变量。

### 接口与错误契约

Backend 外层沿用 service_bot `ApiResponse`（`success/message/error_code/data`）。正常及部分实例失败均返回 results，外层 success 仅当所有实例 success 才为 true；失败外层 error_code 不得保留默认成功值 200。data 包含 `request_id`、`scope="current_instances"`、`results`，可含与外层一致的 success。不得添加跨实例总数。

Engine 失败应给出稳定错误码并使用以下 HTTP 分类；Backend 不把上游 detail、完整响应体或原始异常文本暴露给前端。旧 Engine 没有新端点时返回失败结果，禁止降级到 list_dir。

| 场景 | Engine HTTP / 错误码 | Backend 可观察结果 |
| --- | --- | --- |
| 非法参数、目标非目录 | 400 / invalid_path、not_directory；框架校验 422 | failed，file_count=null |
| 根外路径、链接路径、不可读 | 403 / path_forbidden、permission_denied | failed，file_count=null |
| 目标不存在 | 404 / path_not_found | failed，file_count=null |
| 扫描截止 | 408 / scan_timeout | timeout，file_count=null |
| 不支持文件计数 | 501 / unsupported | failed，file_count=null |
| 扫描 I/O 异常 | 500 / scan_failed | failed，file_count=null |
| 扫描期间目录发生不可确定变化 | 409 / directory_changed | failed，file_count=null |
| 并发槽不足且排队预算耗尽 | 503 / busy | failed，file_count=null |
| 调用超时、连接异常、非法上游结果 | transport / timeout、engine_call_failed、invalid_engine_response | timeout 或 failed，file_count=null |
| Backend 管理权限不足 | 无 Engine 调用 | success=false、业务 error_code=403 |
| Bot 不存在/类型不符、阶段未绑定、无实例、绑定冲突 | 无扫描 | success=false、稳定业务错误，业务 error_code=409（沿用运维接口约定） |

前端消费说明：本次不开发页面。调用方必须检查 ApiResponse.success 与每个 result.status，HTTP 200 不代表全部成功；仅 success 的数值可展示为数量，失败展示错误与未知数量，不能用 `file_count || 0`。超时可由用户主动重试，不在后台无限自动重试。

### 外部系统边界日志

| 系统/方向/操作 | 请求事件 | 成功与失败事件 | 必需非敏感字段 |
| --- | --- | --- | --- |
| Client → Backend，file-count | backend.file_count.request | backend.file_count.response / failure | request_id、operator_id、bot_id、entity_id、stage、path、method、route、direction、elapsed_ms；响应含完整结果与状态 |
| Backend → BaaS/ARCA → Engine，count | backend.file_count.engine_request | backend.file_count.engine_response / failure | 上述业务字段、engine_request_id、provider、binding_id、instance_id、状态、file_count/null、error_code、elapsed_ms |
| Engine → 文件插件/扫描器 | engine.file_count.request | engine.file_count.response / failure | request_id、path、根内目标、状态、file_count/null、elapsed_ms、安全错误类型；取消/截止记录 cleanup 结果 |

日志均为结构化字段（logger extra 或独立结构化 dict），保留完整非敏感请求与响应，不打印文件内容或清单。统一递归脱敏 token、Authorization/认证头、Cookie、password、secret、key、credential、session，包括嵌套列表和大小写变体；禁止直接日志化 conn_info、headers、上游异常字符串。测试同时检查最终渲染日志与结构化字段，避免只检查后续被修改的 record.args。请求 ID 必须跨 Backend 与 Engine 可关联。

### 关键方法抽象

| 抽象/方法 | 所在层或模块 | 职责与边界 | 输入与输出 | 协作对象与副作用 |
| --- | --- | --- | --- | --- |
| FileCountServiceProtocol.query | Backend Service API/core service_bot | 管理权限、当前阶段定址、实例编排；不处理 HTTP 或物理路径 | FileCountQuery、operator_id、is_admin → 各实例结果；领域错误 | Bot/权限/RuntimeBinding、binding 仓储、FileCountRuntime；只读外部调用 |
| FileCountRuntime.targets / count | Backend Plugin API | 设备快照、固定实例连接和协议校验 | binding → 唯一实例 ID；query+instance → success/failed/timeout 结果 | BaaS、device context resolver、transport、HTTP client；网络只读 |
| FileService.count_files | Engine Service API | 当前引擎文件计数契约；不返回文件枚举 | path、可选现有 AuthContext → CountFilesResult；标准/领域错误 | 当前插件文件端口 |
| OpenClaw count_files / Claude Code file_count_files | Engine Plugin API | 从可信配置取得授权根，调用安全扫描 | path → 基础结果；不支持时明确报错 | 当前引擎配置、共享安全扫描实现；只读目录元数据 |
| 安全扫描及其 async 生命周期包装 | Engine filesystem infrastructure | fd 边界、普通文件计数、截止/取消与资源回收 | trusted root、path、deadline → count 或稳定失败 | OS 目录 fd、独立执行单元；禁止文件写入、Shell 和链接跟随 |

这些抽象分别对应权限编排、provider 传输、引擎可替换能力和有资源生命周期的系统边界。调用方须传可信绑定与截止预算，不能通过 path 或 entity_id 绕过权限；实现方须保持结果不变量、传播取消、拒绝非法上游响应。Service/Plugin 共用值类型放 kernel 或既有中性模型层，禁止 plugin_api 反向 import delivery/api。

### 关键领域模型设计

#### FileCountQuery

请求级只读查询，无持久化；归属当前认证用户的一次运维操作。

| 字段 | 类型/格式 | 必填 | 默认值或约束 | 来源/所有者 | 字段说明与兼容性影响 |
| --- | --- | --- | --- | --- | --- |
| bot_id | str | 是 | 非空、有长度上限 | Client，Backend 验证 | 服务 Bot 唯一目标 |
| entity_id | str | 是 | 非空、有长度上限 | Client，Backend 验证 | 归属查询键，不等同于授权 |
| stage | draft/verify/online | 是 | 无隐式默认 | Client | 当前阶段，不支持版本 |
| path | str | 是 | 非空、无 NUL、有长度上限 | Client，Engine 验证 | 根相对或根内绝对目录 |
| request_id | str | 是 | Backend 生成 | Backend | 关联请求，不作为身份凭据 |

关系与不变量：bot/entity 必须匹配；权限校验后才能访问绑定；stage 不回退到其他阶段；无数据库迁移。

#### FileCountInstanceResult / CountFilesResult

每个实例一次扫描的瞬时观测值；provider/binding/instance 由可信绑定设施产生，不由用户指定。

| 字段 | 类型/格式 | 必填 | 默认值或约束 | 来源/所有者 | 字段说明与兼容性影响 |
| --- | --- | --- | --- | --- | --- |
| provider | baas/arca | Backend 是 | 绑定 provider | Backend | 实例提供方 |
| binding_id | str | Backend 是 | 已解析绑定 | Backend | 运行绑定标识 |
| instance_id | str | Backend 是 | BaaS device_uuid 或 ARCA device_id | Backend | 固定实例，不使用随机路由 |
| path | str | 是 | 请求目录或成功规范目录 | Engine/Backend | 每个结果都可关联查询路径 |
| status | success/failed/timeout | Backend 是 | 必须显式赋值 | Backend | 前端显示语义 |
| file_count | int/null | 是 | success ≥0；非 success 必为 null | Engine/Backend | bool/负数/字符串一律非法 |
| elapsed_ms | int | 是 | ≥0 | 每层 monotonic 计时 | Backend 计实例调用耗时，Engine 计本地统计耗时 |
| error_code | str | 否 | 失败时稳定分类 | Engine/Backend | 不含异常原文或凭据 |

Engine 成功 wire 仅 `path/file_count/elapsed_ms`。Backend 增补实例身份与状态；一个副本失败不得污染另一个副本的有效值，也不能累加副本。无持久化实体或既有状态机变化。

### 文件改动范围

所有路径相对仓库根，具体命名可按实现微调，但职责和契约不得改变。

| 文件路径 | 改动类型 | 改动说明 |
| --- | --- | --- |
| src/backend/src/agentclaw/community/kernel/file_count.py | 新增 | 中性查询、绑定、结果/错误契约 |
| src/backend/src/agentclaw/community/api/file_count_service.py | 新增 | Service API |
| src/backend/src/agentclaw/community/plugin_api/file_count_runtime.py | 新增 | provider Plugin API |
| src/backend/src/agentclaw/community/core/service_bot/services/file_count_service.py | 新增 | 权限、阶段、实例编排 |
| src/backend/src/agentclaw/community/plugins/community/file_count_runtime.py | 新增 | BaaS/ARCA 固定实例调用 |
| src/backend/src/agentclaw/community/adapters/http/service_bot/router_file_count.py | 新增 | 独立只读运维职责，prefix `/api/service-bot/publish/ops`，新增 `/file-count` |
| src/backend/src/agentclaw/community/adapters/http/app.py | 修改 | 根装配中并列注册 ops router，不创建 child router |
| src/backend/src/agentclaw/community/di/modules/service_bot_module.py | 修改 | Service/Plugin DI 绑定 |
| 相关 Backend README.md | 修改 | Context Boundary 与变更影响 |
| src/engine/src/engine/community/api/file/router.py | 修改 | GET /count、稳定错误映射与边界日志 |
| src/engine/src/engine/community/core/file/{protocol.py,models.py} | 修改 | FileService.count_files 与 CountFilesResult |
| src/engine/src/engine/community/plugin_api/{openclaw,claude_code}/file.py | 修改 | 引擎端口契约 |
| src/engine/src/engine/community/core/adapters/{openclaw,claude_code}/file.py | 修改 | Service API 到插件转换 |
| src/engine/src/engine/community/plugins/{openclaw,claude_code}/_file.py | 修改 | 当前可信根调用共享扫描 |
| src/engine/src/engine/community/plugins/file_count.py（或同责独立模块） | 新增 | fd 安全扫描与受限执行单元 |
| 对应 local 引擎插件、契约文档、测试与 HTTP endpoint 清单 | 修改 | 本地/生产实现一致、成功与失败合同验证 |

文件大小限制需按改动后逐文件实测。既有 router_publish 已有 2188 行，本次不修改该文件：新建 router_file_count 并在 app 根并列注册，独立承载只读文件统计职责，避免扩大旧文件，也避免为本功能拆分无关发布逻辑。不新增 allowlist 或削弱门禁。

### 验收标准

- [ ] 所有约定计数、路径、权限、三阶段、双 provider 与多副本用例通过。
- [ ] 不发生跨根访问；符号链接交换竞态测试证明无根外目录进入。
- [ ] 超时/取消执行单元终止，资源关闭，并发槽恢复，无无限后台线程或孤儿进程。
- [ ] 新 API/Plugin 合同文档与 conformance 测试同步；新 endpoint 纳入既有统一覆盖清单。
- [ ] 新增独立源文件单测行覆盖率 >90%；所有改动文件用 `pytest --cov --cov-report=term-missing` 实测并展示，改动行覆盖按仓库 CI 执行。既有大文件整体未达 >90% 不得伪称达标，评审须明确报告。
- [ ] 无新增 unused import/变量、边界违规或凭据日志；所有必需验证绑定最终提交。

## Review Spec

### 关注点与检查项

- [ ] 权限校验先于 targets/连接/扫描；ADMIN 与超级管理员符合现有服务 Bot 管理策略。
- [ ] draft Bot 绑定与发布阶段绑定测试使用不同 ID；CALLER_SERVICE 不回退 Caller 个人实例。
- [ ] BaaS 固定 device_uuid、ARCA 连接可信，provider 响应类型严格校验。
- [ ] 关键方法职责、输入输出、错误与副作用符合上表；core 不 import HTTP/concrete plugin，不新增运行期环境变量读取。
- [ ] 领域字段、null/0 区别、多副本不求和、请求关联与错误分类一致。
- [ ] 根 fd 与子目录 fd 防竞态，最终目标和祖先链接同样受限；关闭每个 fd。
- [ ] 超时并发测试证明执行单元实际退出，而非只结束 await。
- [ ] 新运维 router 在 app 根注册；旧运维 URL、依赖注入、认证、OpenAPI 与测试引用不变，不引入嵌套 router。
- [ ] 运行相关单测、契约、架构、lint/SAST 和仓库要求的 Singlebox；覆盖率证据准确，未达门槛明确 REJECT 或交由主 agent 处理，禁止降低门槛。

### 不可接受的模式

- list_dir 全量枚举后 len、find/shell 命令拼接、使用 publish-ignore 过滤实际目录。
- resolve/realpath 校验后按普通绝对路径重新打开；默认跟随链接；吞掉无法读取的子树。
- 把失败/超时/无实例计为 0；把 BaaS 多副本求和；返回 bool 作为计数。
- wait_for(to_thread(scan)) 超时后继续无限扫描，或取消时提前释放并发槽。
- 新增私钥/签名协议、硬编码 Backend 文件布局、调用 publish-ignore 私有业务方法。
- 原始异常、连接配置、凭据或完整文件清单落日志。
- 新增 unused import/变量、孤儿代码；冒号前空格；block comment 未以 `# ` 开头。

## QA Spec

### 前置条件

使用临时目录、隔离的 Engine/Backend 测试实例与 fake provider；已有基线主 agent 实测 Backend 123、Engine 53 通过，仅作为变更前基线。远端线上环境不做写入、不部署。最终结果以实现后报告为准。

### 测试用例

| 编号 | 用例名称 | 操作步骤 | 预期结果 |
| --- | --- | --- | --- |
| TC-01 | 精确计数 | 多层目录加入普通、隐藏、ZIP、硬链接、空目录、FIFO | 仅普通文件目录项各计 1 |
| TC-02 | 链接与环 | 加文件链接、目录链接、悬空链接、循环链接 | 均不计不跟随，有限时间完成 |
| TC-03 | 相对/绝对根 | 以 `.`、子目录、同目录绝对路径查询 | 数量一致且路径在当前引擎根 |
| TC-04 | 路径拒绝 | 空/NUL/越界/根外/目标或祖先软链接 | 稳定失败且无根外读取 |
| TC-05 | fd 竞态 | 在检查与打开之间把目录替换为根外链接 | 拒绝或跳过明确链接，不计根外文件 |
| TC-06 | 目录错误 | 不存在、普通文件、不可读目录、扫描中删除 | 明确失败，不返回成功 0 或部分计数 |
| TC-07 | 超时取消 | 慢扫描触发截止和请求取消 | 执行单元退出、fd 关闭、无孤儿、下一请求成功 |
| TC-08 | 并发限制 | 同时发超过上限请求、制造排队超时 | 活跃扫描不超上限，排队有限、槽恢复 |
| TC-09 | 管理权限 | anonymous、普通协作者、ADMIN、super_admin | 前两者拒绝且无 provider 调用，后两者成功 |
| TC-10 | 阶段绑定 | 各阶段配置不同 binding，另加 Caller 个人绑定 | 仅当前服务阶段绑定被调用 |
| TC-11 | provider | BaaS 两副本返回不同计数；ARCA 一副本 | 固定设备、分别返回、无求和 |
| TC-12 | 部分失败 | 一个副本成功 0，另一个超时/失败 | 成功 0 保留，失败 null，外层 false |
| TC-13 | 上游畸形 | bool/负数/字符串数量、缺字段、旧 Engine 404 | invalid_engine_response 或稳定失败，无 list fallback |
| TC-14 | 无运行实例 | 空设备快照、重复/空 device UUID、未绑定 | 稳定业务错误，无 vacuous success |
| TC-15 | 日志脱敏 | 嵌套 credential/token/header/session 注入异常和连接数据 | 非敏感业务字段完整、最终日志无原始凭据 |
| TC-16 | ignore 独立 | 同目录文件命中 publish/build ignore | 文件仍计入 |
| TC-17 | 合同与路由 | 经真实 HTTP/DI 走新端点并回归旧 ops URL | Service/Plugin 均实际调用，旧行为不变 |
| TC-18 | 引擎一致性 | OpenClaw、Claude Code、本地插件运行同套合同；无 FILE_LIST 引擎 | 支持者一致，不支持者 501 |

## Ship Spec

### 部署目标环境

- [ ] 线下环境（本次不部署）
- [ ] 预发环境（本次不部署）

### 分支策略

- 开发 worktree：`/Users/helloworld/Desktop/codes/teamclaw_worktrees/Avernet_worktrees/service-bot-file-count-rel20260922`，复用主 agent 已创建分支。
- 基线/目标：`github/REL20260922`，开发起点 `b2014be448`；不得使用默认 origin/dev 推断目标。
- 本子任务只编写此 Spec；实现、验证和任务 log/经验更新由主 agent 统筹。无部署授权，不发布、不重启真实 Bot。

### 兼容性与回滚

新增查询接口，无持久化数据迁移。Backend 与 Engine 合同需一起交付；旧 Engine 返回明确不可用结果，不降级到全量文件清单。若后续批准部署，优先部署支持新能力的 Engine，再启用 Backend 调用。回滚代码提交并恢复旧 router 注册即可；无需回滚业务数据。扫描执行单元必须在正常关闭和异常退出中回收，停止接口流量后确认不存在残留扫描。
