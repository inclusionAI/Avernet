# 桌面 Bot：旧 Web → 新前端迁移评审草案

状态：**用户已批准实施，改造进行中。下表保留改造前基线；最新结果见 implementation-status.md。**
核对日期：2026-09-23。本文仅报告本地源码证据，不代表线上部署或真实桌面验收结果。

## 1. 评审结论与基线

本次迁移不是只补创建表单。已确认的主要工作为：本地创建与授权、运行时 OpenAPI 对 desktop 的准入和设备路由、启动进度、资源链接、头像更新，以及新前端的桌面能力限制和旧链路兼容验证。

| 标识 | 仓库与基线 | 可信范围 |
| --- | --- | --- |
| B | 用户指定的当前 Avernet 后端；`dev_haoqian_desktop_newPre@365ecc65338d49b716d2008e2ec0ab8176ac255b` | 本次后端事实来源；初始工作树干净 |
| N | 用户指定的 `teamClawPre/teamclaw`；`sprint_teamclaw_S090011826218_20260806_haoqian_20260921@9778b4c29e7ecea3a02825d84df8f25dae7ef870` | 本次新版前端事实来源；初始工作树干净 |
| L | 用户确认的 `legancyTeamClawFrontend/open-claw`；`sprint_open-claw_S090011906562_20260915_haoqian_20260917@426aebb68024d060f6c80c0587e12b483d8f41c7` | **用户已确认作为旧 Web 功能基线**；初始工作树干净 |

未切分支、未拉远程。“当前”指以上本地 HEAD，不声称它们等于远端最新或生产部署。没有把 Avernet 内的 `frontend-nextgen` 当作本次新版前端。

用户已确认：范围为旧 Web 的桌面 Bot 全功能；实际开放引擎仅 **OpenClaw、Hermes**；聊天默认 **localhost 直连本地桌面容器**，预留 relay 配置开关，本期无需产品设置入口；桌面 App 仅作参考，**不修改 App 内部代码**。不扩展为通用管理后台或其他引擎迁移。

证据定位见 [evidence.md](./evidence.md)，连接专项方案见 [connection-design.md](./connection-design.md)。下文标签：

- **已有实现**：已读到 Router/调用链，不等于真实设备已验证。
- **明确缺口**：有直接代码证据证明不支持、被拒绝或前端未接入。
- **待验证**：接口或通用实现存在，但未获得桌面端完整运行证据；不编造成功结果。
- **待确认**：需要用户确认产品基线、范围或真实环境，不自行决定。

## 2. 不可改变的迁移边界

1. 完整迁移正式旧 Web 基线中桌面 Bot **实际开放的行为和限制**。不同引擎、用户权限、灰度配置分别列清楚；组件存在不等于入口开放。
2. 新前端业务 HTTP 调用使用 OpenAPI；不以回调 `/api/desktop/*`、旧代理或 legacy Controller 作为缺口补丁。
3. 旧 Router、请求/响应、授权流程、状态语义、桌面客户端协议继续保留。新增 OpenAPI adapter 可以复用 Service/Plugin；不能直接调用旧 HTTP handler 来复用逻辑。
4. 新旧共享同一 Bot 身份、owner、绑定、技能和会话事实源；不复制 Bot、不迁移重建用户运行目录、不让新前端双写两套接口。
5. 共享 Service 改动必须标出全部消费者，跑新旧契约与行为回归。新 OpenAPI 的校验、DTO 和错误封装不得泄漏到旧 Router 的 wire contract。
6. 用户已授权代码实施，并允许在缺少真机环境时使用 Mock 验证。Mock 测试不能证明真实桌面支持；不修改旧前端或桌面 App。
7. 控制与管理链路为新 Web → OpenAPI → Backend Service → BaaS → App/容器；聊天数据面默认新 Web → localhost 容器。relay 为预留配置，不改变旧调用默认行为；本期不安排 App 内部开发。
8. 方案遵循仓库架构规则、Service/Plugin 分离与 Skills 生命周期规则；内部依赖通过新版 internal overlay/capability 隔离。

## 3. 功能迁移清单

以下旧版列基于用户确认的 L；引擎范围固定为 OpenClaw、Hermes。所有“待验证”都是验收任务，不是已交付结论。

| ID | 旧 Web 行为证据 | OpenAPI / 新前端现状 | 改造与验收 |
| --- | --- | --- | --- |
| F01 | 首次使用/云端准入不足时可进入 DesktopBotGuide；创建本地 Bot 不占云端配额 | N 有本地创建入口，但 Service 主动报未接入；不能据此证明准入已兼容 | 前端：保留本地创建独立准入、无设备引导、帮助链接；测试云端无额度仍可创建本地 Bot。B 的既有 quota/admission 需实际联调 |
| F02 | 设备列表、刷新、自动选单设备或首个在线设备、离线显示 | local devices 已有；N 无对应调用 | 前端接设备发现、分页、选择和异常；不能将查询失败显示成“没有设备” |
| F03 | 设备目录接口返回 `absolute_path`；挂载路径自动拼为基础路径/名称，路径显示 JSX 已注释 | 目录 API 已有但响应是自由 dict；N 无机器/挂载字段 | 保留自动路径行为；冻结目录响应形状与校验。**不擅自新增手选目录产品需求** |
| F04 | 本地创建支持引擎选项过滤，并带 machine/mount/engine | `POST /bots/local` 已有；B 允许 openclaw/claude_code/hermes；N 本地创建明确 throw | 新 local Controller、DTO/Mapper/Service/Hook/表单；本期选择只开放 OpenClaw、Hermes；不全局删除 B 的其他引擎支持，以免影响旧链路 |
| F05 | AgentPass 授权后完成创建，保留提交参数 | 本地 201/202 + GET auth-status 已有；N 授权流程按云端 POST auth-status 编排 | 区分云端/本地 pending request；原样保存机器/路径/引擎等；取消、拒绝、过期、重复轮询和部分失败验收 |
| F06 | 检查/下载/启动/激活四步，百分比、明细、失败原因、重试 | 旧 `/api/desktop/bots/{bot_uuid}/start-progress` 有；B local OpenAPI 无对应路由；N 无界面 | 后端新增 bot-addressed OpenAPI，内部解析正确 runtime UUID/affinity；前端迁移真实进度，不造进度或固定完成时间 |
| F07 | 列表、切换 Bot、在线/离线/启动/失败 | inventory 已有；N 已映射本地列表，但 `local_offline → offline` | 保留本地语义与原始状态，展示机器和挂载目录；轮询/刷新/切换取消旧请求。核实后台状态同步已装配 |
| F08 | 离线时唤起桌面客户端；无设备时也可唤起 | 旧使用 `teamclaw://open`；N 未检索到相应实现 | 前端能力/slot 接入；保留失败提示和帮助入口；沿用旧唤起协议，做浏览器实际唤起验收 |
| F09 | 重启 Bot | local restart 已有，N 已接 | 保留确认、进行中、等待就绪、重连及错误；不能只收到 200 就宣称 Bot 可用 |
| F10 | “重启引擎”在桌面分支也调用 restartDesktopBot | B inventory 禁用 local engine_restart；N engine_restart 走不同的通用 daemon 接口 | 冻结桌面该按钮的历史语义；方案为路由到现有 local restart，并对齐后端 actions，不默认改成云端 daemon restart |
| F11 | 删除 Bot、清理当前选择/会话连接 | local delete 已有，N 已接 | 核实删除后列表、当前聊天、路由、缓存和旧页面一致；检查失败/离线删除语义，保留原有清理合同 |
| F12 | 修改名称、描述、头像；头像存在生成/上传内部入口 | B `BotUpdate` 只有 name/desc 且 extra forbid；N workshop update 无 avatar | 后端新增/扩展 OpenAPI metadata 契约，复用既有持久化与同步；前端补头像展示/编辑。生成与上传是否有合规 OpenAPI 通道列入剩余确认项 |
| F13 | 会话列表、新建、重命名、删除、清空、收藏、搜索/sessionKey 定位 | N 已调用通用 sessions OpenAPI；B runtime gate 拒绝 desktop | **后端 P0**：desktop draft runtime 准入、owner/权限及路由；前端对齐旧搜索/收藏行为。旧历史会话必须原地可见 |
| F14 | 聊天流式、历史、停止、重连及模型选择 | N 已调用 connection/models/sessions；B 统一门禁拒绝 desktop | **后端/前端 P0**：连接发现走 OpenAPI，聊天默认 localhost；保留 relay 配置入口但无产品开关 UI。修正统一 relay 和统一 IAM 取签；会话 HTTP 经 Backend/BaaS 寻址同一 Bot。详见连接专项方案 |
| F15 | OpenClaw 桌面允许 MD 文档读写 | identity OpenAPI 已有，复用 IdentityService；N 已接通用 identity | 不受 runtime gate 的结论不能外推到文件服务；验证 desktop dispatcher、实际路径、写入后读回、权限与不存在文件行为 |
| F16 | OpenClaw 桌面允许引擎配置抽屉；抽屉存在保存逻辑 | engine/config OpenAPI 已有，N 已接；独立于 runtime engine/status gate | 保留引擎类型限制，验证读取、编辑、保存及原有生效条件。不要为此开放明确不支持 desktop 的 config-manifest |
| F17 | 资源树、文件预览、1MB 桌面预览限制；打开根目录/指定目录；文件写操作受限 | resources 文件接口已实现；local open-folder 已有；N 无 open_folder 动词和调用 | 前端迁移只读规则、根/子目录打开、预览限制；后端验证 desktop filesystem 和路径安全。完整下载能力按旧入口逐个核对，不泛化 |
| F18 | 关联链接（语雀/Dima/AntCode）列表、批量添加、删除、预览、权限更新；桌面为允许写链接的例外 | 旧 `/api/resources/links` 有；当前 OpenAPI resources 明确不含 links；N 编辑器无等价 link 管理 | **后端明确缺口**：新增链接资源 OpenAPI，复用现有业务；前端增加链接区。冻结外部解析/授权失败和部分批次结果，见剩余确认项 |
| F19 | Skill/MCP 能力面板，技能集创建与管理、上传/替换/删除、启停、详情等通用入口 | B skills/skill-sets/MCP/market OpenAPI、N editor 已有部分调用 | 逐动作核对，不以“有能力页”算迁移完成；验证 desired-state、实际内容、Runtime 生效和旧端反向可见；参数/编辑等差异逐项补前端 |
| F20 | Skill 参数、市场选择/安装等具体入口受引擎/宿主影响 | B 参数 GET/PUT 已有；N workshop 未检索到同等参数调用；部分旧设置需继续追踪入口 | 参数 UI、错误保护与读回列待核对；市场动态配置以真实基线确认，不使用 mock 列表补齐 |
| F21 | OpenClaw 副屏配置与渲染 | render-screens OpenAPI 已有；N controller 已接 | 迁移列表/增改删和实际会话渲染；区分内部卡片 provider 与公共页面，不复制旧 CDN/私有配置 |
| F22 | 定时任务按引擎开放：旧 OC 可选，Hermes base 不开放 | routines OpenAPI 与 N 通用入口已有 | 验证本地执行、运行记录、离线错误及引擎差异；不把所有本地引擎都默认开放 |
| F23 | OC 桌面 BCN/群聊、Bot 身份/好友/主从模式；禁止好友无需确认 | N collaboration 体系已有，但未建立完整桌面限制闭环的证据 | 逐项回归注册、身份、群聊、好友审批、模式和离线；检查 `/openapi` 路由经网关到 BCS。未经实测不标完成 |
| F24 | 旧版明确关闭：图片/会话文件上传、资源文件写入、节点/渠道管理、健康检查、服务化/发布等（部分 OC 配置有例外） | N 通用 editor/runtime 未建立同等 desktop policy 的证据 | 新版单独 desktop capability policy，隐藏/禁用原因与旧版一致；关闭入口不是迁移缺失，不能因复用云端编辑器意外开放 |
| F25 | 管理后台可显示 desktop 类型，批量重启工具明确排除 desktop | 用户已限定旧前端桌面 Bot 功能，不扩展通用管理台项目 | 保持旧端兼容；不能将管理台批量重启作为桌面既有能力新增 |
| F26 | MemoryOS/Wiki/模板入口由 template_type/capabilities 控制，并非只由 desktop 决定 | 已确认仅 OpenClaw/Hermes；尚无这两种引擎历史桌面实例的动态开关证据 | 不迁其他引擎模板；若 OC/Hermes 存量桌面实例有此入口，纳入对应验收，缺证据项保留待确认 |

## 4. OpenAPI 分类清单

此表前缀除特别注明外均为 `/openapi/v1/bots`。是实现支持分类，**没有任何一项被宣称完成真实桌面验收**。

| 分类 | 接口 | 结论/需要做什么 |
| --- | --- | --- |
| 已有实现 | GET `/local/devices`；GET `/local/devices/{machine_id}/files` | 接前端；目录响应 typed schema、机器归属链路需补核验 |
| 已有实现 | POST `/local`；GET `/{id}/local/auth-status` | 接前端本地事务。当前分支已支持用户级 delegation 创建及创建后 app grant，**不能沿用旧结论称仅人类可创建** |
| 已有实现 | GET `/all`、`/local`、`/{id}/local` | 接/复用列表详情。LocalBot DTO 缺 `bot_type`，不能直接喂 N 默认 cloud 的 Mapper |
| 已有实现 | POST `/{id}/local/restart`；DELETE `/{id}/local` | N 已有调用，需完整状态和兼容验收 |
| 已有实现 | POST `/{id}/local/open-folder` | N 未接；保留根目录/子目录 |
| 明确缺失 | local 启动进度 OpenAPI | 仅旧桌面 Router 有。建议新增 GET `/{id}/local/start-progress`，**此路径是提案，不是现有 API** |
| 明确不支持 desktop | `/{id}/connection` 普通 operator 分支；`/{id}/sessions`、`models`、`engine/status`、`engine/capabilities`、`engine/available`、`approvals/*` 等统一 gate 分支 | 只允许 personal/service。先设计 desktop draft 的授权和路由，不能仅给前端加重试。friend-chat 特殊分支需单独核验 |
| 已有但语义不同 | POST `/{id}/engine/restart` | daemon restart 不是旧桌面按钮对应语义；用 local restart 保持行为，不能混同 |
| 部分支持 | PUT `/{id}` | 名称、描述支持，头像不支持；扩展合同或新增明确的头像子资源，两种方案评审后择一 |
| 已有实现待桌面验证 | `/{id}/identity`、`/{id}/engine/config`、`/{id}/resources` | 分别复用既有文件/配置服务；不能把 gate 的阻断结论误套到这些独立 Router |
| 明确缺失等价功能 | 关联链接资源 CRUD/权限/预览 | OpenAPI resources 仅文件；旧 `/api/resources/links` 不可当作新链路兜底 |
| 已有通用实现待逐项比对 | skills、skill-sets、MCP/market、routines、render-screens、collaboration | 按 F19–F23 列动作和引擎验收；底层内容交付/插件配置不是存在 Router 就已保证 |
| 明确不支持但不是自动新增需求 | config-manifest / apply 的 desktop 能力 | 保持边界，旧配置页通过已有专用 API 迁移；不把本次变成 manifest 扩容 |

特别注意：旧前端有 `/api/desktop/bots/status-check` 调用，但 B 当前 desktop Router 未找到对应 handler，同时 B 已有后台 DesktopBotLifecycle 扫描。不能据旧调用机械新增同名 OpenAPI；先核对实际部署/旧行为与状态同步装配。

## 5. 后端改造任务

| ID | 内容 | 完成条件 |
| --- | --- | --- |
| B01 | desktop runtime OpenAPI 支持：准入、draft-only、owner/permission、设备绑定；HTTP 经 BaaS；WS 默认 direct/localhost，配置预留 relay | 桌面 sessions/models/connection 正向完成，local 不误走 gateway、不强制云端 IAM；未授权/错误 owner/verify/online 被拒；personal/service 结果不变 |
| B02 | 启动进度 OpenAPI，复用 BaaS Service Protocol | 不暴露任意设备 UUID 查询权限；从 Bot 解析目标设备，保留步骤/百分比/明细/失败/affinity；schema、admission、authorization、网关路由、文档和契约齐全 |
| B03 | 头像更新 OpenAPI 和必要的头像交付通道 | 名称/描述旧请求不变；头像保存后旧新端一致；外部上传依赖方案确认，未实现时不能绕回旧接口 |
| B04 | 链接资源 OpenAPI | 提供旧版实际使用的 list/create/update/delete/preview/权限同步能力，明确 owner/bot 寻址和错误；保留旧 handler 的输入输出 |
| B05 | 本地元数据与能力合同补齐 | 明确 LocalBot 类型来源、机器/路径、目录 schema；actions 与桌面重启语义一致；不改变已有字段含义 |
| B06 | 创建/目录归属与失败恢复核验 | 目录链当前下传 machine_id 而不传 owner；需确认下游归属保护。验证申请/创建/授权关系重试，基于既有幂等实现补实际缺口，不先声称无幂等 |
| B07 | 文件/identity/config/Skill/MCP/routines/副屏/协作逐项桌面验收 | 失败定位到具体服务或插件后再改；真实部署/客户端版本缺证据时保持待确认，不直接规划“全部重写” |
| B08 | 兼容与上线门禁 | 旧 Router 契约不变、新旧共享状态一致、云端回归、访问控制、网关/企业 DI、真实桌面测试及回退验证 |

## 6. 前端改造任务

| ID | 内容 | 依赖 |
| --- | --- | --- |
| N01 | Local DTO / dedicated mapper / controller / service / hook；不要 import 旧仓库 | 现有 local API + B05 |
| N02 | 首次引导、设备选择/刷新、自动挂载路径、本地引擎过滤、独立配额策略 | N01；OpenClaw/Hermes 范围已确认 |
| N03 | 本地授权状态机、提交参数保存、取消/失败/重试、创建后等待就绪 | N01；B06 |
| N04 | 真启动进度、列表状态/详情、离线唤起、根/子目录打开 | B02；local open-folder；既有唤起协议 |
| N05 | 重启/重启引擎/删除统一 desktop 动作分发、完成后的连接和缓存清理 | B05；现有 restart/delete |
| N06 | 单聊会话与模型/连接、停止/重连、历史/搜索/收藏；local/relay 模式及凭证分流，默认 local，无产品开关 UI | B01；真实 WS 契约 |
| N07 | 头像、MD、引擎配置、只读资源树和预览；关联链接 UI | B03/B04/B07 |
| N08 | Skill/MCP/技能集/参数/市场、定时任务、副屏/BCN 逐动作等价迁移 | B07；正式旧能力矩阵 |
| N09 | desktop capability policy，所有相关入口统一消费；保留旧禁用行为 | N01；F24；OC/Hermes 能力矩阵 |
| N10 | 全链路 OpenAPI 请求审计与新旧交叉验收 | 所有功能包；头像/外部服务边界确认 |

## 7. 分阶段实施计划

阶段用于控制依赖和 PR 范围，**不是缩减“完整迁移”要求**。每一阶段都有独立测试，最终 F01–F26 的适用项全部完成才算迁移完成。真实联调环境、开发资源和剩余验证缺口明确前，不提供没有依据的精确工期。

| 阶段 | 工作包 | 退出标准 |
| --- | --- | --- |
| M0 范围冻结 | 已确认三仓和产品范围；冻结连接契约、两引擎能力矩阵、外部服务边界及联调环境；批准 Spec | 每个旧入口都有“迁移/保持不开放/明确不适用”的证据和结论；没有未归属功能 |
| M1 后端运行主链 | B01/B02/B05/B06，优先连接/会话和进度；保留 legacy adapter | 新旧 contract 并行通过；真实桌面能取得进度、连接、模型和历史 |
| M2 新前端基础闭环 | N01–N06/N09；设备→授权→启动→会话→重启/删除 | 在指定设备/引擎完成闭环及失败恢复；不能以 mock 成功代替 |
| M3 完整功能对齐 | B03/B04/B07 + N07/N08；按头像、链接、能力/配置等责任拆 PR | 旧开放功能逐项有真实结果；旧限制均保留；所有业务 HTTP 走 OpenAPI |
| M4 兼容验收与发布 | B08/N10；OC/Hermes 存量动态能力如适用一并验收 | 新旧交叉操作、旧客户端、云端回归、灰度/回退完成；确认后才切主入口 |

建议 PR 拆分：运行时支持；启动进度；local 前端创建/授权；状态与操作；头像；链接资源；能力/配置/协作补齐；兼容验收。每个 PR 自带文档与相应测试，不把全部测试压到最后。

## 8. 兼容与验收清单

- **旧端回归**：旧入口设备查询、创建授权、进度、聊天、重启/删除、配置、资源、能力等实际开放项全跑；legacy envelope/error/http status 不变。
- **交叉兼容**：旧创建→新管理/聊天；新创建→旧管理/聊天；两端轮流改名称/头像/MD/链接/能力，读取同一状态；历史会话不复制不丢失。
- **身份和隔离**：user、delegated-app、无授权、错误 owner、他人机器、失效授权、Bot A/B 切换、不同设备和个人/团队空间；明确错误与不存在的掩蔽规则。
- **生命周期**：无设备、单/多设备、离线、授权取消/拒绝/过期、创建部分失败、下载失败、重试、页面刷新、重启中断、删除后迟到响应和设备重连。
- **能力矩阵**：OpenClaw/Hermes × 实际客户端版本 × Bot 状态；openclaw 特有 MD/配置、副屏/BCN 与其他引擎区分；旧关闭的上传/发布等保持关闭。
- **请求审计**：浏览器网络与服务端日志证明迁移范围内无旧业务 HTTP 回退；审计 SDK、内部 overlay、头像上传与链接预览，不能只搜 controller 文件名。
- **真实设备证据**：消息流/停止/重连、模型切换、文件读回、配置保存、技能实际激活、定时任务结果；配置已保存或任务已入队不等于生效。
- **存量客户端**：本期不改 App；验证现有版本的 direct、进度、文件/能力交付。若发现必须修改 App 才能补齐的能力，记录为范围冲突并请用户确认，不自行扩大实施范围。relay 是否具备实际部署条件单列，不以配置已存在宣称可用。
- **回退**：先增量部署后端，再灰度新入口；关新入口可返回旧入口，已有 Bot/身份/目录/会话继续可用；不回滚已共享的用户配置，不设计不可逆数据迁移。
- **工程检查**：后端按实际变更跑 local/runtime/desktop/DI/权限/契约及相关架构测试；新前端 typecheck/lint/test/build/open-core 边界；增加真实集成证据。本轮未执行这些测试，不宣称通过。

## 9. 已确认决策与剩余输入

已确认：L 为正式旧 Web；迁移范围为该 Web 桌面 Bot 功能；只开放 OpenClaw/Hermes；默认 localhost，保留 relay 配置且本期无产品 UI；不修改桌面 App。

仍缺少证据的项目不做成功假设，实施前按以下方式关闭：

| 项目 | 需要的输入/确认 | 对计划的影响 |
| --- | --- | --- |
| 头像生成/上传和关联链接外部服务 | 是否要求头像生成/上传 SDK、外部链接解析也全部由 Backend OpenAPI 代理，或存在已批准的新前端外部服务通道？ | 当前不假定第三方直连例外；需确认后冻结 B03/B04 合同 |
| 真机兼容基线 | 可联调的 App、BaaS、两种引擎版本与测试账号/设备，以及 OC/Hermes 是否存在额外动态能力入口 | 决定真实验收矩阵，不影响已经确认的源码缺口；不自行升级或改 App |
| relay 部署状态 | 当前联调环境是否具备既有 relay 插件/代理和客户端支持 | 本期保留配置及模式合同；真实 relay 验收若受环境阻塞，明确标记，不伪造通过 |

以上为实施前评审基线，保留用于追踪。用户随后已批准代码实施、外部 SDK 和 Mock 验证，并确认动态入口不使用；当前改动与验证结果见 [implementation-status.md](./implementation-status.md)。真实设备交叉验收仍未完成。
