# 副屏预览与主屏建群验证记录

## 当前交付：Draft 入口固定类型、自动展开（2026-10-23）

本次仅局部修改 frontend 的 CreateGroupModal 与 BCS 内文档。带 `initialDraft` 时隐藏群类型和自由编辑/模板切换，原文初始化后复用既有 YAML 校验函数；成功直接进入角色绑定与逻辑流程预览，失败停留编辑器。保留“重新编辑”和普通无 Draft 入口行为。

实际 localhost 历史 2174 字节卡片已验证：

- 从副屏点击入口后，没有手动点击校验，直接显示“已解析 7 个节点、3 个角色”、角色绑定和逻辑 Loop 图；选择 CEO 后其候选列表正常显示。
- “重新编辑”可打开 YAML 编辑器，群类型控件及模板按钮均为 0；注入无效 YAML 后显示 YAML_PARSE 并禁用创建，恢复原文再校验可展开。
- 普通“拉起协作”仍显示三种群类型；切到自定义协作仍显示自由编辑和模板入口。
- 截图 `/tmp/bcs-draft-auto-expanded-modal.png` 记录自动展开状态。没有创建群或 Run，验收后已关闭浮层。

frontend 全量 16 套件 / 98 项、lint、生产构建及 `git diff --check` 通过。新版 TypeScript 对照仍为工作区/HEAD 各 164 项既有诊断，逐文件/编号/数量一致；未宣称完整类型检查通过。既有 CreateGroupModal 为 4070 行，未拆分或调整门禁。本次没有修改 Panel 源码或 Rust，未重复相关门禁；实际建群和线上验收仍暂缓。

同日发起方选择交互修订：改为原生 select/options，删除自有 Bot 搜索和平铺按钮，少量 Bot 隐藏分页；保留现有超过 20 条时的分页能力和当前选中项。实际页面下拉包含 6 个自有 Bot，选择 CEO、绑定发表角色后改选研发，旧绑定清空且候选切换正确；没有发起建群。截图 `/tmp/bcs-originator-select.png`。全量前端 98 项测试、lint、生产构建和 whitespace 检查通过。仅新增修改 GroupCreationControls（54 行）与 useGroupCreation（117 行），未新增依赖或更改服务 API。

以下为前序行为的验证历史；带 Draft 可切群类型/模板的旧行为已被本次修订替代。


## 历史：通用建群入口与可编辑初始 Draft

日期：2026-10-22。根据用户进一步要求，将 Draft 专用入口改为通用 `onOpenCreateGroup(options?)`，通过独立 `selectOriginator` / `initialDraft` 参数复用现有 CreateGroupModal。删除上一轮未提交的 Draft wrapper，hook、Controller 与提交逻辑使用通用建群名称。仅修改 frontend、BCS Panel 及 BCS 内文档，既有服务端和 Singlebox/profile 工作区改动保持原状；没有提交或推送。

### 真实页面验证

继续使用 localhost:8000 原现场群的 `e9e72635` 历史会话与 2174 字节 revision 1。通过实际 UI 操作，没有发建群请求。

- 固定版卡片打开副屏，再点击“用我的 Bot 发起协作”，打开原 CreateGroupModal。原文自动进入自由编辑器，三种群类型、模板和校验入口可用。
- 选择 CEO 并填写目标，在原文后追加回归注释；校验得到 7 个节点、3 个角色，配置发表=CEO、审稿=验证、写稿=产品经理，逻辑 Loop 图联动，提交按钮启用。
- “重新编辑”后再追加注释，旧校验失效，提交禁用。取消再打开后，通过编辑器全选复制核对完整缓冲区，2238 字节修改内容与关闭前完全一致；CodeMirror 的可见 DOM 存在虚拟化，未将仅可见行当作完整源码。
- 切到自由聊天，显示当前群主与所选发起方的候选，创建按钮可用；切到任务协作，选择 CEO 为 Manager 后创建按钮可用；回到自定义协作，完整编辑文本仍不变。
- 取消后从普通主屏“拉起协作”打开，仍使用当前协作助手 Bot、原有好友/可协作筛选和三种群类型，没有出现自有 Bot 选择要求。
- 再从副屏“复制源码”，得到的原 Draft 仍为 2174 字节，不含两次表单编辑。原 Draft 未被写回；关闭浮层，保留原聊天与预览。
- 截图保存在本机 `/tmp/bcs-editable-create-group-modal.png`，显示自有 Bot、初始版本提示、三种类型、自由编辑器与编辑后禁用的提交按钮。

### 自动化与限制

| 检查 | 结果 |
| --- | --- |
| Panel `npm run verify` | 类型、构建、行为、视觉和 public scan 全部通过；UMD 226040 / 256000 字节 |
| frontend `npm test -- --runInBand` | 全量 16 套件 / 98 项通过；其中 GroupChat 12 套件 / 85 项 |
| frontend `npm run ci` | lint 通过 |
| frontend `npm run build:oss` | 生产构建通过 |
| `git diff --check` | 通过 |
| 源码行数 | 本轮新增源码最大 293 行；既有 CreateGroupModal 从 HEAD 3993 行、上一轮 4052 行变为 4069 行，按用户要求局部修改，未拆分或放宽门禁 |

提交测试覆盖无 Draft 的自由聊天/任务协作、自定义协作当前文本、提交后表单突变不改变已捕获快照、2174 字节原文、编辑后的中文/CRLF 字节一致性、失败/新角色/身份变化阻断、一次 POST、未知结果防重及导航。初始读取独立覆盖撤销、长度和 hash 异常。Panel 测试验证回调只含 `{draft}` 且仅显式点击触发；通用空参数通过入口解码测试，实际 UI 验证带 Draft 入口及三种群类型，尚无另一个产品按钮调用空参数入口。

独立类型检查仍受既有问题影响：项目 TypeScript 4.x 与现有 d3 类型语法不兼容。使用 Panel 已安装新版 TypeScript，HEAD 基线与工作区同为 164 项诊断，逐文件/错误编号/数量相同；新增通用建群文件无诊断。没有以 lint 或构建通过代替完整类型检查。未修改 Rust，因此未重跑 Cargo/Singlebox coverage；没有运行 GitHub CI 或线上验证。

读取成本：带 Draft 初始化一次固定版读取；提交改为当前 Human 复核 + 当前文本校验（仅自定义协作）+ 一次建群 POST + 成功后 Human 复核。不再提交前重读 Draft，不新增数据库或逐角色请求。候选分页、身份轮询与既有服务数据访问边界不变。

真实建群、进入新群、首个 Run、私聊及部署宿主继续保留待办。以下为前序版本的历史验证，固定只读浮层与独立副屏表单均不再是当前实现。


## 历史：副屏预览 + 固定原文主屏浮层

日期：2026-10-22。本轮根据用户新增授权，修改 BCS Panel、BCS 内文档和 `apps/frontend/`，复用现有 CreateGroupModal；没有修改 Backend、Gateway、Runtime、frontend-nextgen 或 Singlebox/profile 源码。工作区原有服务端及 Singlebox 改动保持原状，不把它们算作本轮新增。没有提交或推送。

### 真实页面验证

宿主 `localhost:8000`，原现场群 `bcs_grp_45ec83b2340643f2bfc7b81b6d232c5b` 的 `e9e72635` 历史会话，使用 2174 字节的固定 revision 1 卡片。

- 从卡片打开副屏，显示固定版、校验通过、预览/源码及复制入口。点击“用我的 Bot 发起协作”在主屏打开原有建群浮层；副屏未再切换到独立配置表单。
- 当前 Human 为 admin，真实接口显示 6 个自有 Bot。选择 CEO 后，候选来自其视角；配置写稿=产品经理、审稿=验证、发表=CEO，三个角色及逻辑 Loop 图同步更新，目标与角色齐备时按钮启用。没有点击提交。
- 关闭重开浮层保留发起 Bot、目标与绑定。更换发起 Bot 为研发后旧绑定清空，重新查询候选，提交恢复禁用。取消返回原预览，顶部聊天仍是协作助手。
- 普通主屏“拉起协作”保留自由聊天/任务协作/自定义协作类型；切到自定义协作仍有模板和 YAML 编辑器，未混入固定 Draft 配置。
- 高级设置、完整流程图与角色选择已检查。截图保存在本机 `/tmp/bcs-main-create-group-modal.png`，只记录配置状态。
- 构建后的 Panel UMD 同步到忽略的本地运行资产目录；没有修改 Singlebox 源文件或重启服务。

### 自动化与边界

| 检查 | 本轮结果 |
| --- | --- |
| Panel `npm run verify` | 全部通过；UMD 226059 / 256000 字节 |
| frontend `npm test -- --runInBand src/pages/GroupChat` | 12 套件 / 78 项通过 |
| frontend `npm run ci` | lint 通过 |
| frontend `npm run build:oss` | 生产构建通过 |
| `git diff --check` | 通过 |
| 源码行数 | 新文件全部小于 1000 行；既有 CreateGroupModal 从 3993 增至 4052 行，按用户要求局部修改，未拆分或调整门禁 |

Panel 测试验证仅显式点击才调用宿主、严格四字段引用、未接入降级、读取/完整性失败阻止打开及无 Human/建群请求。frontend 测试使用现场原文，覆盖 2174 字节不变、独立角色绑定、去重、分页参数、账号变化/撤销/字节数/hash/固定执行身份阻断、重复调用一次 POST、超时/不确定失败/不完整响应及创建成功后身份复核失败的未知状态、目标和高级设置、群/会话导航。未知提交结果采用受控请求测试；真实浏览器只验证了编辑状态关闭重开，未注入真实写失败。

独立类型检查没有通过：项目安装的 TypeScript 4.x 无法解析现有 `@types/d3-dispatch` 的新版语法；使用 Panel 已安装的新版 TypeScript 检查后，工作区与内存读取 HEAD 源码的基线均为 164 项诊断，逐文件/错误编号/数量一致。两条候选类型错误仅顶层联合类型文案变长，底层仍是原有 `dynamic_status.status: string` 不符合 `DynamicStatus`。其余诊断相同，新增文件没有诊断。未以构建或 lint 通过代替完整类型检查。

本轮没有 Rust 改动，未重复全工作区 Cargo、Singlebox coverage、GitHub CI 或线上宿主验证。实际建群、进入新群、首个 Run、私聊页面继续按用户决定保留待办，frontend-nextgen 仅写入 roadmap。以下为前序实现的验证历史，不表示原副屏独立表单仍在使用。

## 历史：原副屏配置实现

日期：2026-10-22。实现限定在 `apps/bcs/`，没有修改 Backend、Gateway、Engine Runtime、frontend 或 Singlebox/profile。工作区原有的 Singlebox、助手目录及上一迭代根目录文档改动不属于本次交付，未处理、未提交。

## 已实现与现场证据

- schema 2 固定版本预览增加显式配置入口；用户选择自有发起 Bot、角色成员、群名和任务说明。所有槽位明确绑定，固定运行身份拒绝；原文与角色绑定独立提交。
- 原生 `/collaboration/user` 复用现有 BCS Human 认证和 V1 Bot/Group handler。V1 的 Gateway 身份验证独立保留，没有 401 后降级；分享凭据不进入用户请求。
- Draft V1 只读 GET/validate 复用同一个应用服务实例，增加 envelope，不增加管理入口或持久化。
- 提交前复核同一固定版本，单个在途 POST；成功保存响应标识，未知结果禁止自动重试。返回预览再进入保留本次状态。刷新/关闭不提供持久操作恢复。

现场宿主为 `localhost:8000`，群 `bcs_grp_45ec83b2340643f2bfc7b81b6d232c5b`、会话后缀 `e9e72635`。使用上一迭代已保存的 2174 字节样例和 revision 1：真实页面显示逻辑 Loop 预览、校验成功，以及新的配置入口。配置识别 `admin (001)`；初次检查返回 1 个自有 Bot、0 个可协作候选，角色未选择时提交禁用。收尾时服务列表已显示 6 个自有 Bot，新页面要求明确选择发起方，仍未提交。本轮没有调用注册、好友或建群写入口。窄副屏的 HSL 主题变量处理已修正，并在重新打开的页面确认边框和背景生效；既有标签页仍可能保留旧 UMD 内存模块。

本地 `/openapi/v1/...` 的 404 来自宿主没有该代理路径；直连 BCS V1 的 401 来自缺失 Gateway 签名身份。已接通 `/bcnproxy/collaboration/user/me`、`/bots/mine` 和 `/bots/{id}/candidates`。本地沿用既有配置的 local Human；真实 OAuth cookie 路径由随机端口集成测试覆盖，不能把 local Human 结果当成线上登录验收。

## 自动化验证

命令从 `apps/bcs/` 运行，另有说明除外。测试产物保存在本机 `/tmp/bcs-launch-*.log`，不提交日志、凭据或运行数据库。

| 检查 | 结果 |
| --- | --- |
| Panel `npm run verify` | 通过：类型、构建、UMD、行为、视觉及公共资源检查；UMD 255860 / 256000 字节（含下方界面对齐增量） |
| `cargo test -p bcs-api-http` | 238 通过，包含新增 Draft 投影测试 |
| `cargo test -p bcs-app-group` | 121 通过，回归既有所有权、参与者、绑定及 Group 编排 |
| `cargo test -p bcs-http --test collaboration_drafts_contract` | 2 通过，原生 Draft 行为保持 |
| `cargo test -p bcs --test integration_auth_user` | 8 通过，覆盖 OAuth cookie、local Human、匿名/混淆凭据、用户比较和跨站写拒绝 |
| `bash scripts/ci/check-config-validation.sh` | 138 个配置相关测试通过；测试监听端口需脱离本机沙箱运行 |
| `cargo check -p bcs-api-http -p bcs --lib` / `cargo build -p bcs --bin bcs` | 通过；本地 BCS 已更新到新构建 |
| 新增 `test_collaboration_launch_contract.py` | 2 通过；Human 和 capability 认证分离、共享 DTO 和源文 schema |
| 契约加载及 validator | public 74、internal 27、原生 Human 4 个操作，引用、安全元数据和 envelope 检查均通过 |
| 全部 `tests/openapi` | 167 通过、6 失败；HEAD 临时副本为 165 通过、相同 6 失败，见下文 |
| 变更范围、源码行数、`git diff --check` | 已检查；本次新源码均小于 1000 行 |

Panel 行为测试直接使用构建后的 UMD 和现场样例，覆盖 2174 字节原文提交、独立绑定、去重、分页保留、视角重验、账户变化、撤销/完整性失败阻止 POST、连点、未知结果、返回预览保留结果、可信宿主参数和部署路由格式。它们使用 fetch fixture，不代替真实建群验收。

### 既有失败与未运行门禁

全契约测试失败集合在工作区与 HEAD 完全一致：

1. `test_bot_self_is_internal_and_authenticates_bearer_in_bcs`：旧断言未包含 Actor header。
2. `test_dump_contract_writes_a_deterministic_self_contained_document`：既有 `Group Context` tag 与清单不一致。
3. `test_dump_internal_contract_writes_only_internal_paths`：既有 `agents/me` 使内部操作数比旧断言多 1；本次仅增加 2 个 Draft 操作的期望增量。
4. `test_group_detail_uses_human_or_owned_bot_resource_access`：既有权限文案断言过期。
5. `test_contract_contains_exactly_the_approved_internal_operations`：既有 `agents/me` 未入旧清单；本次 Draft 操作已加入。
6. `test_update_session_participant_accepts_bot_and_human_modes`：既有权限文案断言过期。

架构总检查没有通过：未改动的 `check-deps.sh` 在本机报 unbound variable；导入、trait 命名和 conformance 检查报告未改动文件中的问题，已核对代表文件与 HEAD 一致。配置测试最初因沙箱禁止绑定端口失败，单独放行重跑后通过。总检查中的全工作区 `--list` 编译在已有失败后停止，没有宣称完成整个架构门禁，也没有修改脚本或降低要求。

没有运行统一 Singlebox coverage、完整 GitHub CI 或线上宿主端到端。唯一修改的既有超长源码为 `bootstrap/bcs/src/server.rs`，7083 → 7089 行，仅增加必要装配；按用户范围要求未拆分，也未放宽行数门禁。

## 请求与数据访问成本

只读预览仍是固定版读取和校验。进入配置增加当前用户、我的 Bot、当前视角候选查询；每个列表最多同时有一个活动请求，搜索防抖 250 ms，返回页大小 20。身份确认仅在打开、聚焦/重新可见以及可见页面每 30 秒触发，不循环扫描 Bot 目录。提交增加一次 Human 复核、固定版读取与校验、一个建群 POST 和成功后的 Human 复核；失败不自动重发写请求。

数据库访问复用既有应用和存储，没有新增表、缓存或按角色查询：

- 候选主查询使用 SQL `LIMIT/OFFSET` + `COUNT(*)` 两次查询，物理行最多 20；另沿用视角记录、好友集合和运行状态读取。Provider 补全包含至多两次批量查询，启用 Provider metadata 时仍有每个返回 Bot 一次 lookup（既有实现，最多 20）。
- “我的 Bot”底层当前先读所有匹配的自有行，再由应用分页；Provider metadata 同样存在按匹配 Bot 的 lookup。UI 每页 20 不等于数据库只读 20。本版未改动该查询行为，也未因 UI 新增自动翻页。大账号的分页下推/批量补全需单独处理，不以本地 1 个 Bot 的结果证明规模性能。
- Draft 读取/校验仍受原有单版 64 KiB 上限和分享权限约束；不扫版本历史。建群沿用现有业务读写和错误传播，没有新事务包裹网络请求。

## 保留的真实验收待办

2026-10-22 用户明确答复：**“暂不创建，保留真实验收待办。”** 因此没有创建测试 Bot、好友关系、测试群或 Run，也没有保存新的测试凭据。

后续有已授权的测试 Bot 和可协作关系后，完成：

1. 从现场固定版进入配置，选择发起 Bot 和协作 Bot，填写任务说明并确认一次。
2. 检查 POST 原文与 2174 字节样例一致，readToken 未进入用户请求；参与者去重、driver 和绑定正确。
3. 用响应中的真实群/会话进入新群，确认当前 Human 可见，观察首个状态机运行，核对只产生一个群及初始 Run，没有额外启动请求。
4. 在实际部署宿主验证 V1 Draft、`/me`、用户认证、header 透传和导航。本次没有更新 Gateway schema 快照，不能将 Panel 的部署默认值视为发布完成。

L-AC08、L-AC09、L-AC12 保持真实验收未完成；私聊页面验收继续遵循此前暂缓决定。


## 建群界面对齐增量验证

本次增量仅修改 BCS Panel 的配置、请求组装、流程图及其测试和 BCS 内文档，没有改动 HTTP/Rust、frontend 或其他服务源码。

- 与 frontend 建群对齐协作群名称、必填协作目标、发起方、角色页签与 Bot 列表；群类型和固定版 YAML 不可更换。Webhook / 开场白映射到既有 Group 字段，保留原文及独立角色绑定。
- `npm run verify` 全部通过，日志 `/tmp/bcs-launch-ui-verify.log`。新增回归包含角色切换/移除、跨页保留、键盘角色导航、逻辑节点选择角色、目标必填、自动群名、Webhook 格式、模板变量与 UTF-8 长度、可选字段组装；原有身份隔离、完整性、单次 POST 和未知结果回归保持通过。
- 真实 `localhost:8000` 中读取现场固定 revision 1，选择现有 CEO 为发起方、产品经理为写稿角色；仅进行表单操作，没有提交建群。点击审稿节点可定位审稿角色。
- 窄副屏约 295 px 时字段纵向排列、流程默认折叠，标题及操作区固定；宽副屏约 901 px 时表单与逻辑 Loop 并列且独立滚动，操作区保持可见。宽视图截图 `/tmp/bcs-launch-unified-ui.png`。
- 本地宿主使用 Singlebox 运行配置目录中的复制产物。将新 UMD 同步到该忽略目录后刷新页面；未修改 Singlebox 源码或配置契约，也没有重启服务。
- 新界面没有增加按角色查询、自动翻页或新的目录扫描；原有请求/数据库访问成本保持不变。可选 Webhook 仅在用户实际提交建群时使用既有订阅创建路径，本轮未进行真实投递验收。
- `git diff --check` 通过；本次修改的源码均小于 1000 行。真实建群、首个 Run 及线上宿主验收继续保留待办，不以页面或 mock 验证替代。
