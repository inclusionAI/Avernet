> **2026-10-31 范围修订**：本文早期的跨消费者 pending 改造和永久关闭写入口方案已废弃。当前实施合同见 `../public/modules/workflow/docs/pack-version-write.md`。本次只修改 ClawWeb 公共模块与内部宿主；TaskGuard/ClawMind 不改代码，正式历史不引入 pending。以下历史分析中的实现状态不作为当前验收证据。

# ClawWeb 工作流脚本 Git 管理：详细设计与开发计划

状态：**设计与实施中；不得视为已经上线或端到端完成**。日期：2026-10-30。

## 1. 需求与目标

在现有工作流编辑器中，把脚本文件作为当前 workflow 所属 Pack 的版本化内容管理。保留现有编排体验；在编辑器顶部增加同级「工作流编排」「脚本文件」页签。用户能够查看、新建、上传、编辑、删除和对比脚本；保存和部署应保证工作流 YAML、manifest、脚本与 Pack 内其他资源的版本一致。脚本源在配置的 clawflows Git 仓库，非浏览器、数据库的脚本副本或其他工作区。

非目标：浏览器或服务端运行脚本；直接修改既有不可变 tag；连接生产 Git/数据库进行写入或触发发布；重建工作流 schema；以静态目录证明不存在间接引用；把文件正文写入 `workflow_specs.spec_json`；引入私有地址、凭据或新依赖。此文提出 Service API，不新增 Plugin API；本地测试使用合成数据及临时本机 bare Git 仓库。

## 2. 现状与关键差异（本机代码，非线上版本证明）

1. `web/pages/Editor.tsx` 的一级编辑状态已包含 workflow spec、未应用 YAML/JSON、会话号、撤销及部署保护。保存当前先通过 `useSaveWorkflowToDb` 完成 DB/facade，再调用 `/api/auto-heal/save` 写 Git；DB 成功而 Git 失败是明确存在的部分成功。
2. `AutoHealSaveService` 从 `saved/<workflowId>`，缺失时从 `wf/<workflowId>` 继承 Pack，然后写 YAML 和 manifest；不提供任意脚本编辑。`AutoHealDeployService` 从 DB 重新读取 spec，以 `wf/<workflowId>` 为基底，生成部署 commit 和 `deploy/<workflowId>/#N`。因此只将脚本写入 `saved/` **不能**保证它出现在部署 tag 中。
3. 本机 ClawMind `src/git/candidates.ts` 的新保存写 `wf/<workflowId>`，其双 ref 兼容规则还考虑提交元数据。该规则与 ClawWeb 不完全一致。对于两个不同且分叉的 ref，首期必须停止并报告冲突，不可盲目选一个覆盖另一个；即使仅是祖先关系，也必须记录所选完整 commit 和目标 ref 当前 SHA。
4. ClawMind 精确候选可能以 commit、Pack Git tree object ID 与 spec 字节 digest 表示身份。**Git tree ID 不等同于 SHA-256 内容 digest**。版本化运行目录从不可变 snapshot 物化；不能通过读可编辑目录保证运行版本正确。
5. 本机 Git client 允许根级 `<packId>/` 与历史 `packs/<packId>/` 两种布局。任何一个 commit 内同一 workflow 对应多个 Pack manifest 时拒绝操作，不自动删除用户资料；未知、多 workflow 或共享 Pack 的影响需要审阅。
6. 本地代码涉及 workflow 定义、部署历史与权限存储；具体运行环境的事务边界和部署版本仍需独立验证。未使用生产业务数据作为测试 fixture。

## 3. 用户行为与验收标准

- **P01 页面集成**：编辑器当前标题下显示「工作流编排 / 脚本文件」，编排页继续提供画布/对照/YAML、节点属性和流程设置；脚本页显示对应工作流身份、Pack、选定快照或不可用原因。两个页签切换不卸载、重置或静默丢弃任何草稿。支持嵌入与独立两种宿主。
- **P02 浏览**：服务端按已认证用户和 workflow 查看权限解析 Pack，列出安全相对路径及文本内容、文件模式与精确基线 commit。缺 Pack、无访问权限、Git 不可用、冲突、不可编辑文件等均为显式状态，不能假装空目录或成功。大文件/二进制/链接必须显式拒绝或标记，不当文本读取。
- **P03 编辑**：新建、上传、编辑、删除 `scripts/` 下允许的 UTF-8 文本脚本；空文件与删除不同；目录层级和相同文件名不同路径不混淆。上传默认不覆盖已有文件；覆盖须显示差异并明确确认。大小、路径和类型的浏览器检查是便利校验，服务端是最终权威。原有文件的可执行位不因编辑而改变。
- **P04 草稿**：单独追踪脚本 dirty 与 YAML/JSON pending，不将脚本文本塞进 workflow spec。切换文件、页签以及异步操作保留用户输入；工作流切换、页面离开和刷新均提示。旧 workflow/session 的迟到请求不得覆盖新会话。保存请求在途时产生的新修改仍为 dirty。
- **P05 保存**：确认对话展示 YAML 与脚本增、改、删的完整变更。请求冻结 `workflowId`、Pack 身份、`expectedBaseCommit`、spec 和文件操作，服务端重新验证身份/权限和当前 head，在同一 Git commit 中应用 spec 和脚本，保留其他未变文件。目标 ref/head 不符合前提返回 409，拒绝覆盖；不得自动切换基线或 force push 未观察到的他人变更。
- **P06 部署**：未经保存、存在未应用配置、保存尚未完成、Git/DB 部分失败或基线未知时不能部署。确认冻结完整 candidate commit/Pack tree/spec 身份，服务端在执行时再次核验，发布的 tag 指向经确认的完整快照或其经明确验证的同 tree 发布 commit。不同快照、DB spec 不一致、活跃发布版本变化或 ref 竞态均 fail closed。确认期间新编辑不能跟着已确认版本悄悄部署。
- **P07 版本与差异**：文件与 Pack 差异以实际对象计算，区分 candidate、工作区和只读 release；不会将未发布脚本冒充已发布。历史 tag 不可编辑。预览截断、无法解析的动态命令或跨 workflow 共享引用必须标识为未知/待人工审阅。
- **P08 权限/安全**：所有读写与发布使用服务端现有认证 actor（不能以 `unknown`、header 伪造或浏览器传入 packId 作为授权），检查 workflow 查看/编辑/执行权限；不得读取没有权限的其他 workflow 版本。拒绝绝对路径、`..`、NUL、反斜杠、符号链接、gitlink 和特殊文件；封顶单文件、文件数、总字节数；Git 执行参数化、禁用不可信 hooks/filters，不暴露凭据，不执行脚本。共享 Pack 修改/删除必须提示全部可发现的影响，无法完整识别时不能宣称安全。

## 4. 目标技术设计

### 4.1 状态与 UI 边界

- `Editor` 增加与 `viewMode` 正交的 `workspaceTab: workflow | scripts`；后者仍仅表示画布/对照/YAML，编排区保持挂载。
- 脚本状态包含 `workflowId`、编辑会话 ID、`baseRef`、完整 `baseCommit`、Pack 位置及逐文件 `{path, content, savedContent, mode, change}`。不存在/删除用显式状态，不用空字符串代表。文件选择与输入只修改草稿；没有授权或读取失败不得初始化为空数组后允许写入。
- 防丢判定聚合 `isDirty || pendingEdits || scriptDirty`，但不把 scriptDirty 混入 `pendingEdits`（后者会影响已有 JSON/YAML 操作）。对所有可触达导航路径核对使用；并在 `beforeunload` 检查。若无法提供 SPA 顶层导航拦截，明确列为未完成并保留会话内草稿，不宣称全路径防丢。
- 仅在已冻结且已持久化的保存结果与返回的 workflow/session/base 匹配后更新各自 baseline；请求期间新输入或更换 workflow 不可被旧响应清理。统一保存期间阻止并发保存与部署。
- 脚本组件本身只通过受控 props 接收数据、回报动作，不在组件中直接访问 Git。错误和权限提示可键盘访问，复用现有 semantic theme。

### 4.2 版本化 Service API 草案（新增、未声称已上线）

由 workflow server 提供，UI 调用；控制面服务和 Git 存储实现之间通过明确服务边界连接。名称、错误码和真实请求体实施时以代码及契约测试为准：

| 操作 | 输入 | 结果 / 约束 |
| --- | --- | --- |
| `GET /api/workflows/:workflowId/pack-scripts` | 身份来自可信认证上下文；workflowId 经服务端校验，packId 由授权数据解析 | `{workflowId, packId, baseRef, baseCommit, files:[{path,content,mode}]}`；409 双 ref 分叉/Pack 不唯一；403 无权限；404 不存在；上限错误/不可读不可伪装为空 |
| `POST /api/workflows/:workflowId/pack-candidate` | `baseCommit`, `spec`, `changes:[{path, content: string|null}]`，并发身份、标题/facade/重命名语义经现有保存合同明确 | 检查 edit、base/head、Pack、路径、大小、YAML 与文件引用，统一 commit；返回精确 `commit`、ref、Pack tree ID、DB/Git 状态和冲突原因。新增路径不默认覆盖，删除必须存在 |
| `POST /api/workflows/:workflowId/pack-deploy` | 经授权保存的 `candidateCommit`、预期 active revision、幂等操作 ID | 在同一候选身份上确认并发布；返回 tag、commit、deploy number 和激活状态；Git/DB 不确定时返回可对账状态，不盲目重试 |

不接受浏览器提供任意 ref、文件系统绝对路径、远端 URL 或 Git token。API 必须有请求/响应类型、错误码及真实 HTTP 与 service 契约测试；当前只读策略要求 edit 权限，已确认可读取所属 Pack 的共享和遗留脚本；读取范围是该 workflow 的版本快照，不能使用客户端选择的 ref 或其他 workflow 版本。未来若开放 view，须先定义相应授权和共享影响策略。写和发布使用各自权限且所有 caller 必须参与同样的校验。

### 4.3 Git 操作与身份

- 在服务端配置中取得仓库和凭据，使用隔离临时 clone 与受控 Git 参数；在 Git commit/tree/blob 中读取，不信任 checkout 文件或 soft link。确定 Pack 唯一性及 workflow/manifest 归属，识别根级和历史 `packs/` 布局并完整保留未改动文件与 mode。对新建文件应用明确默认 mode，删除/覆盖需显式请求。禁止接受/改写 Pack 外路径。
- 保存采用从用户已读的 **expected** base 生成新 tree/commit；Git ref 更新用针对具体 ref 与原 head 的显式 lease。即便进程内部已有锁，跨进程 / Bot 写入仍需 Git CAS；分支创建、两个 ref 分歧、远端不可达及 CAS 失败都不能回退到默认分支。
- 必须保持既有 `wf/` / `saved/` 及发布记录合同，并做消费者兼容性验证；不要求修改 ClawMind；不能将保守拒绝策略误称 ClawMind 的完整解析规则。任何试图前进不同 ref 的动作，先证明不覆盖已有工作，且明确非 fast-forward 情况。
- tag 内容绑定审阅 commit / tree / spec；发布号、活动状态与 Git 不是单个原子事务。预留、推送、激活、确认需要幂等身份和可恢复记录；Git 成功/DB 失败、超时响应丢失、DB 已更新但 Git 失败分别给出可对账状态。**未实现对账及全部发布入口并发保护前，禁止把新脚本写入路径连接到现有 deploy 并声称完整可发布。**

### 4.4 安全与数据保护

允许编辑授权工作流版本中的完整 Pack 常规文件；拒绝特殊 Git mode、子模块、无效 UTF-8、过多文件及非规范路径；读取大小先界定，避免进程输出无界。限制上传后缀和 1 MiB/文件、示例上限按实际协议固化，不可仅依赖前端。已确认 workflow 编辑者可读取及修改该版本快照中其他 workflow 使用的共享脚本；这不表示脚本写入或发布功能已经实现，仍须落实影响提示、并发保护和发布审批。不要执行代码、重写任意命令字符串或假装已解析动态引用。日志/错误不得输出文件内容、凭据或远端敏感地址。原子 Git CAS 和浏览器输入验证不替代审计与发布审批。

## 5. 实施阶段与交付边界

| 阶段 | 实施任务 | 验收 / 发布门禁 |
| --- | --- | --- |
| A | 本设计、细化现有状态与失败矩阵 | 本文与当前本地代码对照，保留用户已有改动 |
| B | Git snapshot 只读基础层 + 服务端授权读 API | 临时 bare repo 覆盖 ref 缺失/同 SHA/祖先/分叉、唯一 Pack、模式/路径/UTF-8/资源限额；权限 401/403 与不泄漏 |
| C | 编排/脚本 tab、受控文件面板与独立脚本草稿 | 只连接真实授权读接口；切页保稿；读取失败明确；未完成真实保存前禁止写入和误导性成功 |
| D | 服务端冻结完整 candidate 保存、脚本文件操作、DB/Git 协调和状态对账 | 添加本地 Git + DB 契约与失败注入测试，防跨用户覆盖及部分成功伪装；写接口缺完整保障不得打开 |
| E | 部署指定完整 candidate，涵盖所有部署入口的并发/CAS及恢复 | 验证 tag 的实际 tree/YAML/脚本和 mode，双写/崩溃/并发/ABA/tag 冲突；未完成不得把脚本编辑接到现有 deploy |
| F | 组件、交互、TypeScript、服务/路由/合同、真实本地 Git、浏览器 E2E、独立审查 | 各阶段用实际结果记录；部署前另行预发授权与验证，不用 Mock 证明真实线上发布 |

A/B/C 可先交付可读能力；D/E 不是只改前端按钮或直接沿用 `/auto-heal/deploy` 就完成。只有 P01–P08 和所有门禁实际通过才能宣称端到端完成；如果停在任意阶段，最终报告必须明确能做什么、不能做什么。

## 6. 测试矩阵

- **Git 隔离和完整性**：空值/删除/新增、嵌套、Unicode、文件 mode、其他资产保留；根/legacy Pack、多个 manifest、关联 workflow 不存在；路径 traversal/绝对路径/控制字符、软链接/submodule、无效 UTF-8、大小与总量上限。
- **版本与并发**：两个 ref 同一 SHA、各自存在、先祖/后代、分叉；运行中他人推进 head 或发布；删除不存在文件、重复输入路径、跨 workflow；Git push `--force-with-lease=<exact-ref>:<observed>` 不得覆盖新 head。
- **API 授权**：未认证、无 view、仅 view、无 edit/execute、篡改 packId/ref、多个用户的跨 Pack 访问；不读取 secret 字段到日志。服务端负面响应不能由 UI 填充空文件。
- **工作区**：YAML/JSON 未应用与脚本 dirty 在 tab/文件/工作流切换、浏览器离开及并发读写中保持；异步旧会话不生效。保存成功只清除快照中的修改；失败与部分成功保持可恢复状态。
- **端到端**：本地构建的真实 React editor + 受控 mock 权限/真实本地 Git；脚本上传→编辑→联合保存→确定 commit→发布→从实际 tag 读取 YAML 和脚本，检查其他 Pack 内容未变。网络白名单禁止真实 PROD/PRE 和意外写请求。
- **兼容**：现有 workflow save/deploy、Bot/auto-heal 和两种宿主回归；源文件每个不超过 1,000 行。逐项记录未运行项、环境与失败，不允许用旧报告代替本轮证据。

## 操作状态与既有消费者兼容性

未完成的保存、部署只写 ClawWeb 私有 `clawweb_pack_operations`。正式部署历史仍使用既有 action，不写 pending，不要求 TaskGuard 或 ClawMind 升级。操作记录持久保留精确 Git 对象与恢复材料，只有 Git 已确认并且数据库事务成功才生成完成响应。数据库与 Git 不是单个事务，部分成功需按固定 requestId 对账，不能靠全局关闭原部署接口解决。

## 7. 运维与回滚

- 新 UI/写能力应分阶段开关：先读后写，精确部署与对账通过后再允许发布。默认关闭不安全的写入/部署路径；故障应可禁用新入口而不破坏旧定义读取。
- 单独记录操作者、workflow、Pack、基线与目标 commit/tree、操作 ID、动作、结果及异常类别；不记录脚本内容或 token。必要时追踪 Git/DB 分阶段状态以供重试/人工对账。
- 回滚代码只恢复旧 UI/API；已发布 tag 不可变，工作流回滚必须走现有受控新发布流程；无法靠重置分支抹去已推送的发布事件。共享 Pack 影响、历史快照可达性及审批策略需在发布前由负责人确认。

## 8. 实施与验证记录

当前改动正在按完整版本合同实施。Git 原语、服务端操作日志、宿主真实路由和 UI 均需分别验证，再运行组合测试。历史测试总数不是本轮验收结果；最终状态以当前工作区测试报告为准。本轮不执行生产数据库写入或部署。
