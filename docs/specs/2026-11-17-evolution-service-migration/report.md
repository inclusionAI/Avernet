# Evolution 独立服务迁移记录（2026-11-17）

## 结论与范围

**完成了代码提取、独立控制面、持久化恢复、Bot RSI 面板与 Simulator 开发桥接部署；真实任务验收仍阻塞，生产委托与 Swarm 子操作接入未完成。**

以下按阶段保留历史证据，当前状态以文末最新的2026-11-18后续验收小节为准。

遵循 ADR 0021，canonical 实现位于 `apps/evolution`，不是将 EvolutionRun 转换为
Swarm Task。策略入口继续为 `async run(ctx) -> RunSummary`。本次聚焦01–06；
已有 Verification 实现仅迁出，没有新增07–09评测/晋升流程。`completed` 和
候选 `pending` 均不等于评测通过、发布或 active 已更新。

## 备份与恢复

- 修改前 HEAD：`0840d21f21218c60882b9bc62dec80c9013d857a`。
- 原分支：`dev_youan_rsi_test`；没有切分支、创建提交或推送。
- 备份目录：`/private/tmp/avernet-before-evolution-20261117-giyrahra`。
- 内容：`worktree.tar.gz`、`tracked.patch`、`manifest.json`，覆盖修改前58个
  已变更/未跟踪文件；归档逐文件 SHA-256 校验通过，最终收尾再次核对58个全部一致。
- 目录权限0700、文件0600。备份可能包含原有非公开实现，**不要提交到仓库**。
- 按要求排除并未触碰 `test_llm.py`；未读取或使用真实 IAM 凭据。
- **该目录属于临时存储，应在清理临时文件前复制到用户受保护的持久位置。**

恢复时先停止消费者，将归档解到新目录，按 manifest 校验后逐文件比对恢复。
不要 blanket reset/restore：本次开始前工作区已经有用户未提交的改动。
这是代码备份，不是真实环境运行数据库、Genome 内容或沙箱的备份。

## 已实施

### 1. 模块边界与兼容

- `apps/evolution/src/avernet_evolution`：Run、Strategy、Ledger、现有 Verification、
  Worker SDK/入口及相关运行时适配器；无 Backend Python imports。
- `packages/genome-contracts`：共享 Genome DTO、规范化/摘要、纯文本编辑与树契约；
  不迁移 Backend 的 Genome 政策、发布或晋升权限。
- Backend 旧导入路径保留模块对象别名，避免重复实现和类型/异常身份不一致。
  原平台适配器、旧组合根仍留在 Backend；兼容阶段 Backend 安装新包，
  但独立服务不反向依赖 Backend。

### 2. 独立生命周期与接口

- 独立 `evolution serve`、配置校验、组合根、HTTP 生命周期和单进程文件锁。
  未知配置项及拼错的 `EVOLUTION_` 环境变量拒绝启动。
- 用户控制 API：Bot-scoped Binding/ETag、Run/幂等提交、取消、候选和执行证据，
  策略目录；用户接口与内部 Worker 接口分别维护协议。
- `entity_id` / `owner_id` 只是归属选择器，鉴权 provider 必须验证实际证明并解析
  权威 BotRef。无凭据或跨 Bot 访问拒绝，不把工号当授权。
- local profile：有独立令牌的明确夹具、只绑定回环、合成经验、真实改写夹具候选、
  不移动 active；无需 Backend。**不是用户 Bot 的真实02数据或远端沙箱证据。**
- production profile：必须供应可信 Platform factory、隔离沙箱和 digest 固定的
  Worker 镜像；不回退 local，也不通过 import Backend 内部实现绕过契约。

### 3. 持久化、重启和可观测性

- SQLite WAL/FULL；Run+幂等键+首 Job 原子提交；Job claim/续租/完成/失败/
  重派发/过期处理采用事务，拒绝过期或错误 fence，终态不复活。
- 重启保留状态、签名材料及策略注册身份；静默换钥或同版本重定义拒绝启动。
- artifacts 按 Run 持久化，单份5MiB上限，重启可读、跨 Run 不可读、落盘失败报错。
  暂无公开 artifact 下载接口。
- 过期租约启动下一次 attempt，保留 Run 和冻结输入；**不是 Python 指令级续跑**，
  不保证外部副作用 exactly-once。真实 provider 须实现幂等、TTL和孤儿沙箱清理。
- evidence 返回 Job/Worker/lease/attempt/events，并保留 platform / worker-reported
  来源区分；不返回 fence/token。不能用策略自报日志冒充平台创建沙箱的证据。
- Runner 循环错误记录安全异常类型；HTTP500记录 trace_id 和类型，不打印任意
  provider 异常文本。状态、事件和独立 Ledger 不是跨库原子事务，不能当严格审计账本。

### 4. Bot RSI 页面与路由

- Bot聊天左下角「+ → RSI任务」以及右侧配置/启动/查看/取消面板。
- Bot+归属实体隔离，不按聊天 session 创建第二份 RSI 状态；与 Agent Compute 面板互斥。
- 配置使用 ETag；提交结果未知时在当前标签页 sessionStorage 保留同一幂等键及
  binding引用，关闭/重开不会自动换键重复提交；不持久化凭据。
- 轮询任务、Job、Worker、租约、事件和候选；明确提示 completed 不等于通过/发布。
- `RSI_ENABLED` 默认false；容器显式开启必须配置 `EVOLUTION_UPSTREAM`。
  开发代理与 Nginx 只转发 Evolution 路径，Genome/Promotion及其他 Bot 路径不变。
- 修复开发代理对子路径的 glob 匹配；按原门禁拆出菜单主体、使用项目 Button、
  共享纯类型放 `src/types`，未放宽结构检查。

### 5. 打包、部署和门禁

- 独立公共依赖 lock、Dockerfile、单副本/Recreate+PVC+Secret 的部署模板。
  模板不含真实平台工厂、私有地址或真实凭据，不是可直接切流的生产配置。
- Backend Docker构建和本地包依赖同步更新；过渡兼容安装不依赖 builder 源码路径。
- 独立 CI：冻结依赖、ruff、格式、架构/消费方测试；pre-push 对新模块和共享契约
  运行对应检查，共享代码也触发 Backend 回归。没有执行实际 push。

## 实际验证

| 验证 | 结果 | 证据与边界 |
| --- | --- | --- |
| Backend Evolution/Genome/Worker及架构定向回归 | **1,252 passed**，47 warnings | `/private/tmp/evolution-compat-final.log`；不是完整 Backend 全量测试 |
| Runner 日志修改后的相关 Backend 回归 | **8 passed** | `/private/tmp/evolution-runner-final.log` |
| 独立应用完整 CI | **16 passed**，1 warning；ruff及91文件格式检查通过 | `/private/tmp/evolution-standalone-final4.log`；清除 Backend PYTHONPATH |
| 独立真实CLI进程强杀重启 | **通过**，包含在上述16项 | HTTP提交→认领→kill→同库同钥重启→旧租约过期→attempt2完成；旧fence写回401 |
| 前端请求/Store/开发代理/部署脚本定向测试 | **22 passed / 5 suites** | `/private/tmp/evolution-frontend-final4.log` |
| 前端结构门禁 | **通过** | `/private/tmp/evolution-frontend-guardrails-final.log` |
| Open Core artifact 检查 | **通过** | `/private/tmp/evolution-open-core-final.log` |
| 修改的前端 TS/TSX 语法转换 | **16文件通过** | esbuild语法检查，不是完整typecheck或bundle |
| 修改/新增源文件行数 | 无文件超过1,000行 | 排除禁止读取的文件；前端更严格的组件300行门禁也通过 |
| Shell语法、`git diff --check` | **通过** | 包含服务CI、pre-push与前端entrypoint |
| 迁移所属文档相对链接 | **13份文档，0处断链** | 修正迁出模块README中的旧路径、旧组合根与消费开关说明 |
| 修改前代码备份校验 | **58/58匹配** | manifest与归档内容SHA-256再次比对 |

### 复核命令

独立服务（仓库根；Python3.12+uv，CLI测试需要允许绑定本机端口）：

```bash
apps/evolution/scripts/ci_test.sh
```

Backend兼容性（绝对PYTHONPATH；社区conftest会切换工作目录）：

```bash
PYTHONPATH="$PWD/apps/evolution/src:$PWD/packages/genome-contracts/src:$PWD/apps/backend/src" \
  apps/backend/.venv/bin/python -m pytest \
  apps/backend/tests/community/unit/evolution_run \
  apps/backend/tests/community/unit/evolution_strategy \
  apps/backend/tests/community/core/bot_genome \
  apps/backend/tests/community/plugins/evolution \
  apps/backend/tests/community/contracts/test_bot_genome_schema.py \
  apps/backend/tests/community/endpoints/test_evolution_jobs_router.py \
  apps/backend/tests/community/architecture -q --disable-warnings --maxfail=5
```

前端结构/公开边界：

```bash
cd apps/frontend-nextgen
node scripts/ci-guardrails.mjs
node scripts/check-open-core.cjs .
```

前端22项测试通过临时独立Jest配置借用旧前端已安装的Jest/esbuild运行，
没有安装或伪造 nextgen 的完整 node_modules，不能替代 nextgen 标准CI。
临时复核（仍在 `apps/frontend-nextgen`）：

```bash
node ../frontend/node_modules/jest/bin/jest.js \
  --config /private/tmp/avernet-rsi-jest-final.config.cjs --runInBand --no-cache
```

临时配置依赖同目录 `avernet-rsi-test-transform.cjs`、`avernet-rsi-test-setup.cjs`，
仅适用于本机验证，不属于生产构建或仓库配置。标准依赖安装后须用该工程的
`npm test` / `npm run ci` 重新验证。独立CI剩余一条Starlette/httpx TestClient
弃用警告；本机沙箱不允许绑定端口时CLI测试失败，授权后重跑已通过。

## 第一阶段结束时未执行/未完成

以下是第一阶段记录；其中前端构建、Nginx及simulator发布的后续进展见文末补充，
不代表独立Evolution服务已部署或真实任务已执行。

1. 没有启动真实 Bot 的RSI任务、使用真实会话导出、创建远端沙箱或修改平台 active。
2. 没有实现生产 Platform bridge（真实鉴权/服务委托、Genome契约客户端、02经验适配）
   或 Agent Swarm operations/workspace适配器；保留明确插件边界，生产不能空配运行。
3. 没有进行生产/预发部署、数据迁移或代理切流；没有构建Docker镜像、运行Nginx
   配置验证或Kubernetes实际部署。需要在目标环境补验卷权限、网络策略、镜像及健康检查。
4. 未运行 nextgen 全量typecheck/lint/build、组件DOM测试或浏览器页面验收：本机缺该
   工程完整依赖。已执行的语法和Service/Store测试不能证明完整页面交互正确。
5. 未完成singlebox新服务接线、统一覆盖率及全模块CI；没有将RSI-08整体标为完成。
6. 未新增07–09评测/晋升和SkillSet兼容；后者继续跟踪RSI-25。
7. 当前SQLite仅支持单进程、单实例本地持久卷；旧Backend不遵守新锁，严禁同时写同库。
8. 运行列表UI目前只显示最近20条、证据最多500条；提交结果待确认时保守保留幂等键，
   包括权限/配置错误场景，需核对任务后处理，不应换标签页绕过而重复提交。

## 下一阶段切流顺序

1. 提供并测试真实 Platform provider（鉴权/委托、Genome、02经验、治理和隔离沙箱），
   按现有Worker契约接线；实现Swarm子操作映射，而非把Swarm任务当Run权威。
2. 在独立验证环境安装前端依赖、运行标准CI、构建镜像、接singlebox并做页面验收。
3. 停旧consumer及调度写入，对控制库/ledger/Genome/密钥做一致性备份，保留可回退副本。
   SQLite运行中不能仅复制主db而丢失WAL；使用停写快照或backup API。
4. 在同钥同策略版本下验证真实排队/claim/候选/取消/超时/强杀恢复，核查孤儿沙箱治理。
5. 仅切Evolution路由，然后显式开启RSI入口。回滚先停新写入、核对平台候选增量，
   不允许新旧消费者同时写库，也不能静默丢弃迁移后状态。

详细接口、启动方法、持久化语义及平台接入要求见
[apps/evolution/README.md](../../../apps/evolution/README.md)。


## 后续：simulator 前端构建与部署（2026-11-17）

### 已实际执行

- 在内部部署仓库补齐 simulator 专用运行时开关、窄路径代理和入口脚本；
  公共组件及独立 Evolution 实现仍在本仓库，没有将私有部署配置引入公共默认值。
- 构建时显式选择本 Avernet 工作区作为源码，而不是内部仓库默认的另一份源码。
  完成前端生产 bundle 与 Docker 镜像构建，并只滚动更新 `frontend-nextgen`。
  Backend 和现有 Bot runtime 没有重启；没有提交任何 RSI Run 或创建执行沙箱。
- 实际 Ready Pod：`frontend-nextgen-59c9477967-hdcpm`；运行镜像摘要：
  `sha256:973ba215d622fbddecf4e98e0f0cceb94fe18a0354639fc159edeb24d1f6b544`。
- 入口是 **Bot 聊天页 `/workspace/chat` 左下角「+ → RSI任务」**，
  不是 Bot workshop 编辑页。需选中自己的真实 Bot 和聊天会话。
- 公共面板增加安全的503诊断：配置未加载时不启动轮询、不允许保存或启动；
  不将上游任意异常文本展示给用户，也不把提交结果未知误报为确定失败。
- 内部构建遇到两个既有 `SafeHtml` 旧导入，已备份后仅修正组件导入路径，
  未改市场业务逻辑。

### 证据与验收边界

| 验证 | 结果 | 证据与边界 |
| --- | --- | --- |
| 前端定向测试（含503错误处理） | **28 passed / 6 suites** | `/private/tmp/evolution-frontend-simulator.log`；不是组件DOM验收 |
| simulator运行时与真实Nginx容器测试 | **7 passed** | `/private/tmp/evolution-nginx-simulator.log`；含空/失效上游503、正确代理、Worker接口404、Genome路由不变及脚本注入 |
| 前端生产bundle及镜像构建 | **通过** | `/private/tmp/evolution-frontend-build-simulator.log`；存在既有bundle体积提示，不等于全量typecheck/lint |
| simulator前端滚动发布 | **通过** | `/private/tmp/evolution-frontend-up-simulator.log`；没有发布独立Evolution控制面或Worker |
| 运行中HTTP及Pod核查 | **通过预期行为** | `/private/tmp/evolution-frontend-live-simulator.json`；见下述503含义 |
| 前端结构门禁及Open Core检查 | **通过** | `/private/tmp/evolution-frontend-guardrails-simulator.log`、`/private/tmp/evolution-open-core-simulator.log` |
| 浏览器页面交互验收 | **未完成** | 浏览器工具导航报错，未取得页面DOM或截图；HTTP200和新镜像不能替代视觉验收 |

运行中HTTP检查：

1. `/runtime-config.js` 返回200、`no-store`，`RSI_ENABLED=true`。
2. `/workspace/chat` 返回200；HTML中的运行时脚本位于应用入口脚本之前。
3. Bot bindings和策略目录接口返回明确的JSON503：**独立服务未接通**。
   内部overlay暂时允许显示入口来诊断，但未配置服务地址，绝不回落到旧Backend。
   这与公共容器“开关为true则必须配置上游”的严格部署要求不同。
4. 浏览器入口对内部Worker claim接口返回404；Worker须使用独立执行网络。
5. 503发生在平台鉴权前，因此不是Bot存在、所有权或真实数据读取成功的证据。

### 备份与回滚

内部部署文件及两处旧导入的修改前备份位于
`/private/tmp/ocb-before-rsi-simulator-gze2stj8`，manifest记录5份文件的SHA-256，收尾逐份校验5/5一致；
目录0700、备份文件0600，权限核对通过。旧前端镜像保留标签
`frontend-nextgen:rsi-before-20261117`。它们与前述本仓库58文件备份用途不同。
临时目录均应在清理前复制到受保护的持久位置。

内部仓库 `simulator/docs/rsi.md` 记录准确构建参数、运行时配置名、测试、日志及
回滚步骤。回滚只针对前端：先核对旧ReplicaSet或使用保留镜像，恢复必要的
部署源文件后重建；不要对已有用户改动做整库reset，也不要恢复运行数据库。

### 当前可以验收与仍阻塞的内容

**可以验收：新前端部署、入口开关、窄路径路由和服务不可用提示。**
尚不能验收“真实Bot配置→02真实经验→排队→隔离Worker→候选产出”。
阻塞是可信Platform bridge与独立控制面/Worker部署未完成，**不是缺少07–09评测**。
下一步仍需接真实鉴权/委托、Genome与经验API、隔离执行及持久卷，再以真实Bot
验证01–06。不能用local fixture或旧Backend路由假冒已经接通。


## 后续：独立控制面接通后的验收（2026-11-17）

### 已部署与已验证

- Simulator opt-in 部署独立控制器：单实例、Recreate、非 root、只读根目录、
  持久卷存放控制库/ledger/经验快照，签名密钥单独挂载。未重启 Backend 或 Bot。
- 公共 `platform_http` / `session_http` 插件通过平台公开 API 读取 Genome 与会话；
  内部部署组合根负责开发鉴权及 Kubernetes Worker。独立包不导入 Backend。
- **鉴权是 Simulator 专用的短期开发登录，不是生产应用委托**；限定 namespace、
  Bot 和实际登录用户，并读取权威 Bot 归属，不把工号参数当成凭证。
- 修复动态 Nginx 解析短 Service 名失败的问题：内部部署使用完整集群服务域名。
  只修改部署上游并滚动前端，不需再次构建静态 bundle。
- 从实际前端 origin 检查：匿名403、错误Bot403、浏览器Worker接口404；
  策略目录200、Binding读取200、ETag条件保存200，示例Binding `identity` 已保存。
- 示例策略为确定性的 persona 修改，仅用于打通执行协议，不代表LLM语义改进，
  不修改 Skill，不启用07–09，也不会移动 active。

### 实际启动仍被阻塞（不可宣称沙箱验收通过）

实际 `POST /runs` 返回500，trace为 `4ac2d4340dd821ddaac4c70b571731c4`。
追踪到入队前的漂移检查调用平台 capture API，平台返回422：

```text
Current state cannot be captured
reason: resource_paths, location: spec.resources[0]
resource path 'skills/test_skill/SKILL.md' is under 'skills',
which is not a resources directory
```

这是当前状态中资源路径与 Genome 契约冲突的证据，不足以判定由用户操作还是历史
写入路径造成。不能删除条目、忽略 resources、绕过漂移检查或重置 active 来掩盖。
应先确认 Skill 与 resource 的登记关系，取得用户对修复其 Bot 状态的确认后再操作。

本次提交在 Run/Job 持久化前被拒绝，**没有新 Run ID，没有启动 Worker 沙箱，
没有验证真实02会话处理或产出新候选**。平台422目前被客户端转成安全异常，再由
控制层返回通用500；面板尚不能精确呈现该原因，需要单独补齐安全、结构化的错误映射。

修复基线后复用同一幂等键验证排队、claim、Worker Pod UID/镜像/退出、执行事件、
02快照与候选，并核对 active 未变。之后才能验证独立控制器重启恢复；当前PVC存在
和本机恢复测试通过不能替代真实运行中断验收。网络策略实际强制与崩溃孤儿Pod接管
尚未验收；TTL不能被描述成完善的孤儿恢复机制。

### 定向测试与证据

- 独立服务CI：25 passed，Ruff检查与格式检查通过；
  `/private/tmp/rsi-independent-ci.log`。
- Simulator开发鉴权Mock契约：8 passed；不等于生产鉴权验收。
- Worker/ContainerHost相邻Backend兼容测试：5 passed；
  `/private/tmp/rsi-worker-contracts.log`。
- 部署回归测试与开发鉴权契约合并重跑：10 passed；包括完整集群域名、持久卷与只读根目录。
- 收尾只读复核：目录/Binding/Run列表均200，Binding为 `identity`，独立服务Run总数为0。
- 实际前端origin检查与失败提交：`/private/tmp/rsi-accept.log`。
- 未完成浏览器DOM/视觉交互、真实Worker、真实02、候选、取消、控制器强杀恢复验收；
  不沿用旧Backend任务作为独立服务运行成功的证据。

具体内部部署命令与安全日志查看方式记录在内部 Simulator runbook；公共文档不写
私有服务地址、凭据或会话正文。前述临时备份仍需在清理临时目录前转存保护。


## 后续：删除冲突资源后的重试（2026-11-17）

用户确认已删除 `skills/test_skill` 后，复用原幂等键和已保存的 `identity` Binding
提交同一请求，返回202。此次 capture 与基线检查已通过，不再是上一节的422阻塞。

| 证据 | 实际值 |
| --- | --- |
| Run | `run_ccec806a3b56` |
| Job | `run_ccec806a3b56_1`，attempt=1 |
| 提交 trace | `179d76ff13ebb8a45f14f9893bdde925` |
| Run 状态 | `queued`，`started_at=null` |
| Worker Pod | `rsi-11351c82a3eb5f14` |
| Pod UID | `2063bda7-3157-40a2-bbd4-a141d0615be0` |
| Pod 状态 | `Pending` / `Unschedulable` / `Insufficient cpu` |
| 节点资源 | allocatable=4 CPU，已有requests=3100m，剩余900m |
| Worker资源 | request=1 CPU，limit=2 CPU；900m不足以满足1000m申请 |

控制器日志记录 `rsi.sandbox.created`，Kubernetes对象与UID匹配，证明宿主确实请求
创建一次性沙箱；**Pod对象创建不等于容器已经启动**。当前Job未claim，无worker_id
和lease，证据只有 `run.queued`，候选为空。尚未读取真实02经验或执行策略。
Host的sandbox事件在claim后才按worker_id关联到Run，未claim时不能仅靠面板定位
这个Pending Pod，须关联控制器安全日志和Kubernetes调度事件；这是可观测性限制。

active前后保持 `sha256:f11b02c57d022236c22685898b5f63b56688f61cf81d13a3d7566d01ce3c11cf`。
本次没有重建镜像、降低Worker申请、重启集群、停止其他服务、改变active或新增第二个
Run。解除CPU调度阻塞后应观察已有任务，不要换幂等键重复创建。

最新证据为 `/private/tmp/rsi-retry-observation-20261117.json` 与
`/private/tmp/rsi-retry-20261117.log`；均为安全元数据，不含凭据/会话正文。
本次仅执行真实接口与调度状态检查，没有修改生产源码，未重跑单元测试。


## 后续：临时释放资源后的真实执行（2026-11-17）

用户授权暂停一个不参与本次验证的常驻服务后，将 Simulator BCSFuse 从1副本临时
缩容到0，释放350m CPU requests；没有删除 Deployment、持久卷或 Bot。原副本数与
恢复命令记录在 `/private/tmp/rsi-resource-pause-20261117.json`。本次不修改部署默认
副本配置，后续重新应用原清单会恢复该服务；本次收尾时仍保持暂停，为后续排障留资源。

继续观察既有 `run_ccec806a3b56`，未新建第二个Run：

- 原Pod `rsi-11351c82a3eb5f14` 成功调度并进入Running，UID与上一节相同。
- Worker `wk_10503dae1024` 实际claim了Job，三次attempt均记录
  `worker.strategy.started` 后 `job.failed`；Run最终为failed / max_attempts。
- 宿主记录实际镜像ID、容器exit_code=1及sandbox.destroyed；Pod已回收。
  这证明隔离容器与队列执行确实发生，不代表策略执行成功。
- 控制器三次对应错误均为 `PlatformHttpError`。独立只读探测真实会话列表接口，
  使用与适配器一致的online阶段与分页参数，返回HTTP409；与02经验读取阻塞一致，
  但409的业务原因及阶段/运行时契约仍待进一步核实，不推断为数据为空或用户错误。
- 没有experience快照成功事件，没有候选；active仍与上一节一致。未启用07–09。
- 新版前端聊天页HTTP200，runtime-config.js为200且RSI_ENABLED=true；新版前端、
  Backend、Evolution和Bot运行环境未停止。未获得浏览器DOM/视觉验收证据。

安全执行证据：`/private/tmp/rsi-after-resource-pause-20261117.json`。
本次仅调整明确授权的运行副本数、只读验证及更新记录，未修改执行源码、重建镜像、
重启集群、重置Run或改动active，未重跑单元测试。01–06仍未验收通过；下一步应定位
真实会话接口409，而非重复点击启动或掩盖失败状态。


## 2026-11-18 后续验收：02读取成功，候选被去重契约拒绝

本节取代上一阶段“online会话409”的当前阻塞描述，历史结果不删除。

### 已核实的真实执行

- Simulator 的会话读取配置明确为 `draft`；不是将409解释为空数据或跳过02。
- Run `run_3f904c41489f` 已发生真实队列、隔离 Worker 创建、claim、三次策略执行、
  退出及销毁。每次均有平台 `experience.query.completed`，脱敏快照 count=4；
  页面可查看事件、Worker和固定镜像，未打印会话正文。
- 候选提交实际被 Genome HTTP422 / `already_recorded` 拒绝：同一候选内容已经由
  之前 Run 记录，历史 provenance 不归属于这次 Run。这不是权限拒绝，也不是07评测失败。
- 本轮 candidate_count=0，空 revision / pending 仅为中断提交占位，active保持不变。
  不将队列可执行、02成功或 pending 当作01–06完整验收成功。

### 本次拒绝诊断修复

- 通过 `CandidateRejected` 领域错误连接 HTTP Genome provider、候选提交流水线与
  内部 Worker HTTP；仅映射严格白名单 `already_recorded`，不暴露任意 provider文本。
- 新增持久化平台事件 `candidate.rejected` 和 `rejection_code`，现有前端事件面板可直接显示。
  不回填或伪造旧 Run 的事件；旧失败记录保留原样。
- 新增消费者级回归覆盖正常写入、已知拒绝、13类未知/越界响应、安全事件校验、
  写失败传播、重启后事件可读，以及无成功候选/Ledger/active改动。
- 独立 `apps/evolution/scripts/ci_test.sh` 通过：42 tests，Ruff和格式检查通过；
  仅有现存 Starlette/httpx弃用警告。不是全部 Backend / 全量前端 / singlebox验收。
- 仅更新独立控制器，保留原已注册 Worker digest；部署前确认无queued/running任务，
  SQLite online backup备份三库、部署和身份记录。签名Secret UID/resourceVersion及
  持久化身份记录前后相同；未删除PVC或重启Backend/Bot，BCSFuse仍暂停。
- 代码前备份 `/private/tmp/rsi-before-rejection-diagnostics-oh7svwi_`；
  本次部署备份 `/private/tmp/rsi-before-stage-deployment-yzy6fvhm`。
  临时目录不是长期备份，应在清理前迁往受保护存储。

### 登录问题与后续边界

- 独立匿名探测身份接口为401；现有 Simulator 开发登录后解析到预期用户，
  RSI bindings接口200。工号查询参数本身不是凭据。
- 部署的 Gateway 登录环境为dev；登录服务使用对应stable环境，身份策略含dev_cookie。
  尚无证据证明当前部署存在排查文档所述的跨环境登录服务配置错误。
- Simulator开发登录与企业IAM/聊天凭据是不同边界；这里的登录状态不共享给用户自己的浏览器。
  token文件更新也不会自动安装到任一浏览器。企业登录尚未在用户自己的浏览器验收。
- 后续若要候选写入成功，需明确新的有意义输出及策略版本迁移方案；当前部署固定一个
  全策略Worker镜像，不能直接替换同版本镜像绕过数据库身份保护。
  不删除历史候选、不改写provenance、不以随机输出或数据库重置制造通过。
- 取消、真实控制器崩溃/孤儿治理的端到端验收及生产委托仍待后续，07–09未启用。


### 修复部署后的页面触发实测（2026-11-18）

从真实 Bot 聊天 RSI 副屏点击一次启动，生成 `run_dec60c4b01b1`，没有用脚本代替
本次页面提交，也没有再次重复启动。页面显示 failed / max_attempts，candidate_count=0。

| 关联证据 | 实际记录 |
| --- | --- |
| Worker | `wk_a70c18b0e31f` |
| 一次性沙箱 | `rsi-44958d31cb49d16f` |
| 入队 / 创建 / 首次claim | 平台事件46 / 47 / 48 |
| 02真实读取 | 平台事件50、56、62；每次 count=4，无会话正文 |
| 已知拒绝 | 平台事件52、58、64；`candidate.rejected` / `already_recorded` |
| 退出 / 回收 | 平台事件66（exit_code=1）/ 67（destroyed） |

新事件已由既有前端执行证据区域展示。Worker沿用原已注册digest，控制器为本次诊断
构建；未知错误仍为通用失败。当前Worker遇到拒绝仍按既有策略重试到3次，本次没有
改变重试契约，也没有将失败改为completed。

最终只读核验：Evolution与前端Deployment均Ready=1；该一次性Pod已不存在；
BCSFuse继续保持明确授权的0副本；Genome active读取成功，仍为
`sha256:f11b02c57d022236c22685898b5f63b56688f61cf81d13a3d7566d01ce3c11cf`。
本次没有新增候选修订或Ledger成功记录，pending空revision不是成功候选。

本机临时证据（不是仓库产物或长期备份）：

- `/private/tmp/rsi-run-dec60c4b01b1-20261118.png`：真实运行列表及失败状态。
- `/private/tmp/rsi-candidate-rejected-20261118.png`：平台拒绝事件及占位候选。
- `/private/tmp/rsi-refusal-verification-20261118.json`：部署、回收与active只读核验。

01–06仍未完整通过；下一步是合法的新策略版本/候选输出验收，不是开启07–09，
也不是再次重复点击相同确定性示例。用户自己浏览器的企业IAM/聊天权限仍需单独验收。

## 2026-11-18 补充：新版策略部署与尚未完成的真实验收

前一节的 `already_recorded` 是旧确定性策略的实际失败。没有通过删除历史修订、
重置数据库、随机追加内容或跳过去重来规避。新增独立版本
`simulator/identity-hardening@1.1.0`：02有界会话仅由可归属的用户消息选择固定、
预先审阅的 persona guardrail；artifact 只保存规则 ID、episode ID、计数和上限，
不保存会话正文；无匹配或已有规则时不提交候选，不执行 07–09 或移动 active。
旧 `1.0.0` 策略及镜像身份保持不变，`HostPool` 按注册版本的固定镜像派发。
这只是保守的协议验收策略，不证明 Bot 质量提升。

新版独立镜像已在 Simulator 部署；部署前无排队/运行任务，runtime、Ledger、
sessions 三个数据库做了 SQLite online backup 且 `quick_check=ok`，签名 Secret 未替换，
Backend/Bot 未重启。临时备份路径 `/private/tmp/rsi-before-v1_1-20261118/`；
代码/部署旧状态见 `/private/tmp/rsi-before-experience-upgrade-_q4uiqgp` 和
`/private/tmp/rsi-v1_1-deploy-20261118.log`。这些临时材料不是长期备份。
实际镜像摘要和部署命令由内部 Simulator runbook 管理，不写入公共默认配置。

本机夹具验证了新版候选、artifact、Ledger 和 active 不变；新增的离线双固定镜像
`ContainerHost` 经实际 Job claim/complete 的跨版本路由测试也通过。
独立 CI：47 passed、1 条已有弃用警告；内部 Simulator 定向测试先前 19 passed。
这些仍不能代替真实 Bot 的 Worker、02、候选写入。浏览器内容观察授权已被拒绝，
未绕过浏览器门禁；**本阶段没有从前端触发 1.1.0 Run、没有对应 Run ID、真实候选
或 Ledger 成功证据。**需要用户在自己的 RSI 面板创建新版 Binding（persona、manual、
parent active、`pending@1`），仅启动一次并提供 Run ID；再核查真实排队/claim、
02安全计数、一次性沙箱创建和回收、候选 revision、Ledger、active 未变。
不要把旧 Run 的证据挂到新版。


## 2026-11-18 本地源码调整：RSI 复用默认登录与 Bot 权限

### 授权、范围与交付状态

本次仅修改本机 Avernet 源码；未修改 OCB、部署、重建镜像、发起远端调用或操作
浏览器登录态，未读取实际 IAM/token 文件。未创建提交或推送；保留原有未提交迁移
工作树，没有 reset/clean。此前的 Simulator 部署、开发登录桥接和直连路由是历史
证据；**当前源码已替代该接入方式，不代表远端运行实例已经同步更新。**

RSI 是 Bot 的普通功能，不新增用户登录、用户目录或 RSI 角色。当前链路为：

```text
RSI 面板 → 共享 backendRequest → 原 Gateway 登录
  → Backend 已验证 principal + 既有 Bot admission/collaborator gates
  → 显式九个薄控制接口 → 独立 Evolution → 持久化队列/Worker
```

Backend 只承接控制入口，不迁回调度器、数据库或策略执行。Evolution 不再读取
浏览器 Cookie 做生产用户鉴权，而验证 Backend 完成授权后签发的60秒内部回执；
绑定租户、权威 BotRef、实际 actor 及精确请求。Worker/fencing 密钥仍独立。
这不是第二次用户登录，内部回执不暴露给浏览器。

前端继续由系统 identity store 注入请求身份，再由 Backend 与已验证 principal
比对；请求中的 user_id/归属选择器不构成凭据。不使用对话提供的工号、admin 名称
或 Bot owner 代替登录用户；本次没有改共享用户初始化、昵称和登录实现。读采用
既有 MEMBER 门槛；保存/启动/取消采用既有显式 MEMBER（Owner 通过）。Space
合成的聊天权限不自动等于 Bot 操作权限，未为 RSI 放宽全局策略。

### 已实现与当前知识归属

- [Backend 控制入口与配置](../../../apps/backend/docs/flows/bot-evolution-control.md)：
  仅九个 Bot-scoped 控制操作，保留 CAS/幂等语义；拒绝 Worker/泛代理。网络客户端
  不使用环境代理、不重定向、不重试；不转发 Cookie/Authorization/伪造回执，
  不回传 Set-Cookie/跳转。请求512KiB、响应5MiB；超时返回未确认，不伪报失败已终结。
- [Evolution 边界与部署](../../../apps/evolution/README.md)：生产只接受 Backend
  回执；持久化固定 profile/租户，拒绝重启静默换范围。旧未固定归属的数据库升级前
  必须停写备份、离线确认。后台平台凭据改为正式委托的
  `PlatformCredential(headers, acting_user_id)`，不再从 owner 推断调用者。
- [前端部署](../../../apps/frontend-nextgen/docs/deployment.md)：开发代理和 Nginx
  都走默认 Gateway `/openapi`，不保留浏览器直连 Evolution 的特殊路由；
  `RSI_ENABLED` 只控制显示，不承担鉴权或选择用户。

### 实际验证

以下均为本机源码/合成夹具测试，不是当前远端页面验收：

| 检查 | 结果与边界 |
| --- | --- |
| Backend 架构、协议框架、控制接口、Bot access、principal、身份参数、schema 与 handler 检查 | 693 passed、1 skipped、44 warnings；无新增豁免 |
| 系统授权/admission 清单定向复验 | 50 passed、18 warnings；保持既有清单约束 |
| 完整应用路由框架的九个 RSI 接口 | 18 passed、1981 deselected；每路由各授权转发/未登录拒绝，仅模拟网络边界 |
| Backend 薄入口定向用例 | 42 passed；另一次 schema+入口联合检查50 passed，均包含在相关回归范围内，不相加为独立覆盖数 |
| Evolution 全部测试（含本机 CLI 强杀/重启） | 73 passed、1 warning；首次沙箱拒绝 loopback bind，经明确授权后本机夹具重跑通过，无远端调用 |
| Evolution 最终 DTO 描述更新后复验 | 排除上述需本机端口的 CLI 文件，72 passed、1 warning；该轮未重跑 CLI |
| Ruff 与格式 | Evolution 83个文件、共享 Genome 12个文件各使用所属配置通过；新增 Backend 六个文件通过 |
| 前端静态边界与部署脚本 | open-core、ci-guardrails、shell 语法通过；先前本机 Node 夹具覆盖开关默认/开/关/非法值及无直连路由 |
| 差异检查 | git diff --check 通过 |

曾误用 Evolution 的格式配置检查共享 Genome，报告9个文件需格式化；该操作仅为
check，没有改文件。随后按各包自身配置复验均通过，没有批量重排既有代码。
前端依赖不可用，未安装依赖，**未执行 Jest、TypeScript 类型检查或前端构建**。
本次没有整体运行所有 Backend 业务测试，不将所选回归称为全仓验收。

### 未验证与切换前置条件

- 当前部署及真实浏览器登录未验证，`admin`、`missing login cookie` 的真实根因
  仍未证实；不能宣称源码调整已修复线上登录问题。
- 部署需配置 Backend 内部 Evolution origin，并向 Evolution 安全提供同一平台
  principal 签名材料及匹配租户，和 Worker key 分开；缺少时明确失败，不能回退
  开发 Cookie 或冒用 owner。平台委托插件须适配新的凭据返回类型。
- 真实02会话、隔离 Worker、合法新版候选及 Ledger、取消/恢复和页面最终验收
  尚需用户授权后的环境集成；此次未启动任务或改变 active。
- 沿用前述单实例持久化/租约恢复限制；没有声称实现指令级续跑、跨库原子审计、
  强杀后远端孤儿沙箱治理或完整 Swarm 子操作接线。07–09不在本次范围。


## 2026-11-18 本地源码整理：RSI 单一实现归属

### 范围与结论

按用户“先统计、再整理”的要求，本次先盘点 Backend 与 Evolution 的实现、调用者、
测试和架构约束，再执行归位。仅修改本机 Avernet，保留已有未提交修改；未操作 OCB、
Simulator、浏览器登录态、IAM/token、远端接口或真实任务，未构建镜像、部署、提交或推送。
此前报告中的部署和运行证据仍为历史记录，不代表本次源码已经部署。

当前领域实现唯一归属是 `apps/evolution/src/avernet_evolution/`。不是将 Backend 的
所有 `evolution` 字样都删除：ADR 0019/0020/0021 要求 Genome 与 Promotion 留在平台；
Bot 的系统鉴权/权限入口、真正使用平台 SDK 的适配器也继续属于 Backend。前端 RSI
入口仍在 frontend-nextgen，本轮未调整登录、身份来源或前端业务。

### 盘点结果（Python 源文件，不含 Markdown、缓存和依赖）

| 范围 | 整理前 文件/行 | 整理后 文件/行 | 含义 |
| --- | --- | --- | --- |
| Backend `core/evolution_run` | 29 / 490 | 28 / 146 | 移出组合根及平台适配器，只留兼容导出 |
| Backend strategy + ledger + verify | 22 / 100 | 22 / 100 | 已是兼容导入，无重复领域实现 |
| Backend `core/evolution_promote` | 8 / 1270 | 8 / 1270 | 平台晋升责任保留，不迁移 |
| Backend `plugins/evolution` | 8 / 320 | 9 / 429 | 接收 Registry 平台适配器；含格式整理，不是新增运行时 |
| Backend 显式 acceptance 组合 | 不存在此目录 | 4 / 632 | 原组合根/DI 归位，非第二套生产服务 |
| Evolution canonical source | 75 / 11340 | 75 / 11340 | 唯一领域实现，本轮不改策略/运行语义 |
| Evolution `tests/unit` | 本轮归位 | 27 / 3352 | 领域回归与中立端口夹具 |
| Evolution `tests/contracts` | 本轮归位 | 4 / 650 | Worker HTTP 与 Kubernetes 契约 |

Backend 四个旧 core 目录共44个模块别名、220行。别名指向 canonical 模块对象，
不是复制实现；保留它们是为了不强迫其他模块批量改 import。旧组合根兼容导出采用
延迟加载，普通领域导入不会自动加载 acceptance 组合。统计基线是本轮开始时的工作树，
**不是与 Git HEAD 的差异**；工作树还包含此前的独立服务迁移，不能把整份 diff 都算成本轮新增。

### 具体归位与兼容性

- 运行、策略、Ledger、现有 Verify 的详细领域说明归 Evolution；Backend 对应 README
  保留职责、兼容性与导航，不继续维护两份实现说明。
- 原 `core/evolution_run/app.py` 的组合实现及旧 Runtime/Jobs DI 移入
  `di/evolution_acceptance/`；旧 builder 仍可显式调用。随机密钥、内存数据库和宽松
  测试 fallback 只供历史验收，禁止作为生产配置。既有07/08组合回归保留，独立生产
  组合没有因此启用评测或晋升。
- Backend 默认容器不再安装旧 Runtime/Jobs 模块，HTTP app 不再挂载旧 Worker router。
  九个 Bot 控制入口及其默认系统权限链路保持；执行、消费和持久化生命周期由独立
  Evolution 负责。旧环境变量不能在默认 Backend 内重新启动 RSI consumer。
- Registry adapter 移到 `plugins/evolution/genome_registry.py`。旧 core 路径直接移除，
  不增加 core → concrete plugin 的违规兼容桥。旧 Backend Worker router 和旧 DI module
  路径也移除，仓内调用者已更新；仓外显式调用者需按新路径迁移。
- 独立 Worker/Kubernetes 适配与 HTTP 契约统一归 Evolution；58个历史 Worker HTTP
  声明用例改为真实异步 HTTP/领域副作用断言，另迁移3个 Kubernetes 端口用例。
  Backend 保留 Registry/Engine/Promotion 集成与显式组合测试。
- 只读事件脚本移到 `apps/evolution/scripts/evolution_events.py`；重复的 Backend Worker
  Dockerfile/requirements 移除，只留退休说明。唯一镜像构建入口是 Evolution Dockerfile。
  默认入口是 controller，Worker 必须显式指定模块命令，不能直接按镜像默认命令启动。
- 策略接口仍是 `async run(ctx) -> RunSummary`；本轮不修改公共策略契约、控制面路径、
  Genome 或 Promotion 合约，不新增用户登录方案、架构豁免或其他模块业务改动。

### 实际验证

以下为本机源码与夹具验证，不是远端部署验收；各轮范围重叠，不能简单相加为独立覆盖数。

| 检查 | 结果 |
| --- | --- |
| Evolution 全部测试，含 CLI 真进程强杀/重启 | 288 passed、1 warning |
| Backend RSI runtime/platform、Genome、控制入口、全部架构及 HTTP 框架 | 1117 passed、47 warnings |
| Backend 完整应用九个 Bot 控制入口端点 | 18 passed、1923 deselected、18 warnings |
| Backend 最终格式整理后，RSI unit/platform 与全部架构复验 | 495 passed、43 warnings |
| 清除 PYTHONPATH 后，独立 Evolution 全部测试（仅排除需 loopback 的 CLI 文件） | 287 passed、1 warning；不依赖 Backend source |
| Evolution + shared Genome Ruff / format | 通过，133个文件格式检查 |
| 新归位 Backend acceptance、Registry adapter、夹具和归属测试 Ruff / format | 通过，7个文件格式检查 |
| 当前工作树新增/修改源文件行数 | 250个文件检查通过，无超过1000行 |
| 变更 Markdown 本地链接、旧运行路径调用者、git diff --check | 通过；历史退休说明保留旧路径文字 |

全量中本机 CLI/跨进程 HTTP 需要监听回环端口；受限沙箱首次失败后，经授权重跑上述
全量测试通过，没有对外服务调用。保留现有 warning（含第三方弃用和已有转义警告），
没有通过删门禁、放宽目录豁免或忽略失败来宣称通过。新增归属断言检查了九个 Bot
路由仍在、默认 Worker 路由已移除、默认组合不导入旧 runtime、旧导入与新导入是同一模块。
未执行全仓 CI、前端构建、镜像构建或真实服务部署。

### 当前知识与后续边界

- [Evolution 服务入口与目录归属](../../../apps/evolution/README.md)
- [canonical Run 领域](../../../apps/evolution/src/avernet_evolution/core/evolution_run/README.md)、
  [Strategy SDK](../../../apps/evolution/src/avernet_evolution/core/evolution_strategy/README.md)、
  [Ledger](../../../apps/evolution/src/avernet_evolution/core/evolution_ledger/README.md)、
  [已有 Verify](../../../apps/evolution/src/avernet_evolution/core/evolution_verify/README.md)
- [Backend 显式验收组合及安全前置条件](../../../apps/backend/src/agentclaw/community/di/evolution_acceptance/README.md)
- [平台适配器边界](../../../apps/backend/src/agentclaw/community/plugins/evolution/README.md)
- [Bot 控制入口](../../../apps/backend/docs/flows/bot-evolution-control.md)
- [原对接说明的后继导航](../../../apps/backend/docs/bot-evolution/evolution-run-integration.zh-CN.md)

源码已经完成归位，不代表旧 Backend 镜像已经停止消费。切换前仍需显式停写/备份、
确认数据库与密钥、平台正式服务委托、Worker 命令/镜像和内部路由；不得并行启动新旧
权威写入同一库。真实02会话、隔离 Worker、合法候选/Ledger、取消/恢复及页面验收
仍需后续环境授权。Agent Swarm 子操作、工作区映射、强杀后远端孤儿沙箱治理和07–09
不在此次整理范围，既有恢复/单实例/跨库审计限制不因目录整理而消失。

## 补充：Simulator 第2步接线，不执行任务

本轮只新增 Backend → 内部回执 → Evolution 控制路由/持久化的隔离集成测试和知识导航，
未修改默认登录/用户信息、RSI 领域实现或其他业务模块。
[测试](../../../apps/backend/tests/community/adapters/http/openapi_v1/test_evolution_service_wiring.py)
使用真实双方路由与 Store，合成系统 principal/仓储/平台，只替换 HTTP 传输，不访问
真实 Bot。覆盖策略目录、Binding CAS、实际 actor、拒绝越权、关闭重开后持久化读取；
断言没有 Run/Job/候选、consumer 未启动、夹具 active 未改变。

企业宿主的镜像依赖安装、前端 runtime flag、Backend 内部 origin、只读预检和操作说明
留在宿主仓；公共仓不纳入私网端点、个人身份/凭据或运行状态。没有构建镜像、执行部署、
提交 Run 或申请沙箱。原有迁移和历史任务证据保持不变。

### 本轮验证与未完成项

- 新增双服务接线测试：6 passed；含接收端密钥/租户不匹配拒绝。首次运行暴露测试表名错误，已修正为真实 schema。
- Backend 测试虚拟环境初始缺少迁移后的共享包，直接 pytest 在 collection 阶段失败；
  本轮以显式 PYTHONPATH 指向同工作树的 canonical 包运行，不宣称虚拟环境/容器已安装。
- Evolution 回执与 Service 回归：31 passed，1条已有第三方弃用 warning。
- 宿主只读预检：不通过。镜像/前端/独立服务的真实部署验收仍待操作者构建和平台组装。
- 仍须为生产 Platform 明确正式服务委托、内部签名材料/租户、持久化身份与注册策略镜像；
  不使用旧开发登录桥接，不用本机夹具绕过启动校验，不覆盖旧库和密钥。
- 没有真实浏览器登录、Binding 写入或完整01–06验收；07–09仍不在范围。

- Backend 控制入口及新增双服务测试合跑：48 passed，18条已有warning。
- Backend HTTP adapter架构/源码归属检查：10 passed，17条已有warning。
- 宿主新增接线脚本/配置夹具测试：7 passed；原有Gateway身份配置回归：25 passed。
  Gateway回归首次使用系统Python缺PyYAML，换已有Backend虚拟环境重跑通过；未安装依赖。
- 新增Python文件Ruff/format、shell语法、两个overlay的YAML解析、diff空白检查通过。
  没有运行镜像构建、nginx实际启动或全仓CI；runtime entrypoint仍需部署验证。
