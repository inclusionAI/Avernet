# R3 开发与验收交付记录

- 日期：2026-11-26；源码基线：`f1813114104f`。下述开发与验证在提交前的工作区执行；后续提交/推送状态以 Git 历史为准，**未合并、未部署**。
- 输入：[R3 目标合同](spec.md)、[验收计划](plan.md)、[原设计证据](research.md)。原方案文件在开始开发时已经存在，保留其历史记录。
- 结论：新的正式领域对象、五方法服务、SQL 轮次权威、Method/YAML 联合校验、执行/消息端口及独立签名 Bot 进度入口已实现并有本地运行测试。**不能据此宣称计划 F 的旧产品全量切换、存量历史导入、真实 provider/目标 MySQL 验证已完成。**
- 当前 owning 合同：[产品运行模块](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/README.md)。新 v2 与明确命名的 v1 兼容路径不混写；兼容复核期限为 **2026-12-31**，责任域为 Backend context_collab。
- 后续接口精简以本文第 5 节及 [spec §5.2](spec.md#52-服务签名草图) 为准；干预唯一 ID 的最新修订见第 6 节及 [spec §5.6](spec.md#56-interactive)，替代第 5 节中干预按次生效/显式 revision 的合同。前述记录保留各轮开发和验证证据。

## 1. 实施结果及相对原计划的差异

| 切片 | 已落地 | 尚未覆盖的边界 |
| --- | --- | --- |
| A：合同/影响清单 | 新域精确字段/枚举、低层依赖和新旧名称隔离；旧 Python 消费者改用显式 Legacy 类型，旧 wire discriminator 不变。 | 这不是全部旧产品 facade 已改用新服务；复杂治理/Work 语义不能仅靠改名迁移。 |
| B：模型/Method/YAML | 正式 ProductProfile、Collet、Rollout、ArtifactContent、ProgressEvent；保留 MethodCompositionTemplate，YAML 为唯一阶段 DAG，graph-free SwarmStagesPolicy；封闭 codec 和安全解析。 | 新执行路径当前只支持已实现能力明确接纳的组合/候选；不支持的能力拒绝，不隐式启用 optional methods。 |
| C：持久化/执行/会话 | 五张 v2 SQL 表；独立整数 ID、当前/父轮次、MethodBinding/relations、配置及完整运行快照、回执/outbox、租约/attempt、实际 provider/session/Bot/owner/run。原子创建与 CAS 提交。 | 使用真实 SQLite 事务验证；未在目标 MySQL 执行 DDL，也没有外部 provider 的 exactly-once 承诺。 |
| D：五方法服务 | 显式 Protocol、五个 async 方法、API reexport、DI、五条用户鉴权 HTTP 路由。阻塞 SQL offload，独立发布管理权限复用。 | 旧 v1 authoring/publication/catalog/TaskCommission 仍属于有期限兼容路径；不能把它们称为已经收敛到新 create。 |
| E：上报/干预/消息 | 独立进度 Protocol 与签名 Bot HTTP、三类事件接纳、四类干预、后继轮次、停止/取消意图、真实 OpenAPI/BCS 消息端口适配；完成失败→后继→成功旅程。 | 外部调用用隔离的 recording Protocol doubles 验证；未访问真实 Bot/资源验证服务，也未确认远程取消。 |
| F：兼容/门禁/知识 | 兼容命名、旧 discriminator、显式迁移准备/映射验证/缺失历史拒绝、新 DDL、专项/端点/架构/完整 CI、owning 文档与导航。 | **未实现通用的旧配置/ID/运行历史批量导入器，未完成全部旧 facade/Work 的流量切换，未执行上线排空/停写/回滚演练或企业宿主集成。** |

### 核心代码

- 模型：[Collet](../../src/agentclaw/community/core/task/context_collab/l4_context_primitives/collet.py)、[Artifact](../../src/agentclaw/community/core/task/context_collab/l4_context_primitives/artifact.py)、[ProgressEvent](../../src/agentclaw/community/core/task/context_collab/l4_context_primitives/progress_event.py)、[ProductProfile](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile.py)。对象字段和三个枚举由专项测试逐项固定；整数拒绝 bool。
- [配置联合校验](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/configuration.py) 检查 exact MethodProfile、策略与 relations，保存 composition 与原始 YAML；不从候选反推权限，不接纳两份可独立改写的 DAG。
- [SQL 仓储](../../src/agentclaw/community/core/task/context_collab/l6_products/persistence/rollout_repository.py) 与 [DDL](../../src/agentclaw/community/core/task/context_collab/sql/2026_11_26_product_runtime_v2.sql)：完整 runtime JSON 是首版串行聚合的单一持久化权威，不是由进程内 Plan 拼出的查询视图。技术记录不扩充图示领域字段，快照有 12 MiB 上限。
- [L5 引擎](../../src/agentclaw/community/core/task/context_collab/l5_methods/rollout_engine.py)、[状态权威](../../src/agentclaw/community/core/task/context_collab/l5_methods/rollout_lifecycle.py)、[有界 worker](../../src/agentclaw/community/core/task/context_collab/l5_methods/rollout_worker.py)：先持久化分配/未知派发围栏再做网络 IO；dispatch 成功不等于运行完成；未知结果不盲重试；停止/干预后的迟到派发写取消对账意图。
- [会话读取](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/session_messages.py) 按已存 route 调用现有 OpenApiBotPort/BcsClientPort；不接受调用者 session_id、不用协作 Events/摘要伪装消息。无会话、空消息和上游失败区分。
- [迁移准备](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/migration.py) 验证旧 DAG 与 YAML/验收映射相符、source/version/digest 与新整数 ID 的映射；这不是 bulk importer。SQL child Tasks 不足以恢复治理/录取/租约/真实会话时 fail closed。

### HTTP 与可信上下文

五方法 base：`/api/v1/collaboration/task/products`。

| 方法 | 路径 |
| --- | --- |
| create_product_profile | `POST /profiles` |
| create_product_collet | `POST /collets` |
| query_collet_rollout | `GET /collets/{collet_id}/rollouts` |
| interactive | `POST /rollouts/{rollout_id}/interactive` |
| query_stage_rollout_details | `GET /stages/{stage_rollout_id}/messages` |

独立上报为 `POST /api/v1/collaboration/task/product-progress`，不是 ProductProfileService 的第六个方法。其窄 [Protocol](../../src/agentclaw/community/core/task/context_collab/l5_methods/rollout_progress_protocol.py) DI-bound 到同一 L5 引擎；[HTTP adapter](../../src/agentclaw/community/adapters/http/task/product_progress.py) 复用既有 Bot-only 签名 principal verifier。tenant、actor、accountable owner 只来自可信认证结果，body 的 attempt/revision 只是须与已存事实核验的声明。普通用户登录、正文 actor/tenant 不构成 Bot 上报授权。旧 commission A2A 入口仍为 v1，不能改写 v2。

成功验收仍需受信 evidence capability；签名不代替验收权限。报告/干预/停止竞争在同一 revision CAS 上裁决，回执幂等；晚到旧轮次不能污染当前事实。`overall_acceptance=owner` 下所有阶段通过只令 Collet 到 RUN_FINISHED，不伪造旧根 Task/Work 已被 owner 接受。

## 2. 实际验证

命令在公开仓库现有虚拟环境运行，不安装私有依赖，不连接外部 provider，不改动 mandatory gates。

### 专项、真实 HTTP/SQL 与 Service API 合同

```bash
cd apps/backend
DEPLOY_PROFILE=test .venv/bin/python -m pytest -q \
  tests/community/core/task/context_collab/product_profile_service \
  tests/community/endpoints/test_product_progress_endpoints.py \
  tests/community/architecture/test_service_api_conformance.py \
  --disable-warnings --maxfail=1
```

结果：**356 passed、18 warnings，9.42s**。其中产品专项 **117 项**（精确对象/行为、签名 HTTP、请求体上限、私有异常内容及 MySQL DDL 离线一致性）；独立上报注册端点 3 项；其余为完整 Service API conformance registry。此前五条产品路由已有 10 项注册端点案例；本次完整 CI 也会执行这 13 项真实服务端点案例。

主要证据：

| 验收主题 | 当前测试 |
| --- | --- |
| 精确字段/枚举、YAML mapping/list、封闭 Artifact、非法/重复/有环输入 | [test_models_yaml.py](../../tests/community/core/task/context_collab/product_profile_service/test_models_yaml.py) |
| composition/YAML 往返、实际绑定/关系、optional 不激活、管理/owner 权限、回执、事务回滚、进程重建读取 | [test_service.py](../../tests/community/core/task/context_collab/product_profile_service/test_service.py) |
| 未知派发、真实运行观察、租约/attempt、三类事件、后继/历史、停止与报告 CAS、owner 整体验收不冒充通过 | [test_execution.py](../../tests/community/core/task/context_collab/product_profile_service/test_execution.py) |
| 五方法 HTTP 与严格参数、敏感正文日志脱敏 | [test_http.py](../../tests/community/core/task/context_collab/product_profile_service/test_http.py) |
| 真实签名验证、跨租户、非执行者、伪造 attempt、可信验收者、无 SSO 降级 | [test_progress_http.py](../../tests/community/core/task/context_collab/product_profile_service/test_progress_http.py) |
| OpenAPI/BCS 消息端口实参等于已存 session/provider/Bot/owner；拒绝越权且无出站调用 | [test_session_ports.py](../../tests/community/core/task/context_collab/product_profile_service/test_session_ports.py) |
| DAG 提取冲突、显式 source/version/digest 映射、缺运行历史拒绝 | [test_migration.py](../../tests/community/core/task/context_collab/product_profile_service/test_migration.py) |
| 完整 app 注册、真实 SQL/服务 happy/error，非 handler-only 覆盖 | [产品端点](../../tests/community/endpoints/test_product_profile_endpoints.py)、[进度端点](../../tests/community/endpoints/test_product_progress_endpoints.py) |

这些测试使用真实隔离 SQLite 和真实运行/消息 adapter 的代码，出站能力是有记录的 Protocol double；**不是仅 mock 服务返回消息数组，也不等同于真实 provider 联调**。

### 完整 Backend CI/coverage

仓库根目录：

```bash
PATH="$PWD/apps/backend/.venv/bin:$PATH" \
BACKEND_CI_SKIP_INSTALL=1 BACKEND_CI_PYTEST_WORKERS=4 \
  bash apps/backend/scripts/ci_test.sh
```

`SKIP_INSTALL` 只复用已经安装的公开依赖；不跳过测试、架构、端点覆盖或覆盖率阈值。PATH 显式使用虚拟环境，避免本机 `/usr/bin/python` 的 Xcode 占位程序。

最终当前源码结果：**35,360 passed、41 skipped、651 warnings、13 subtests passed，576.04s（9m36s）**；脚本最终输出 **`backend CI gate passed`**。case pass rate 为 100%，全源行覆盖率 **90.80% ≥ 75%**。架构、完整 app 注册端点、合同及覆盖率门禁保持启用。

标准脚本按提交比较增量；执行上述 CI 时工作区尚未提交，所选 base/head 没有已提交的可执行增量，脚本明确输出 `no changed executable lines found`。因此另用报告检查器的同一 XML 解析/阈值函数，针对 **`git diff --unified=0 HEAD -- apps/backend/src` 的工作区新增行 + 所有未跟踪 Python 源文件的全部行** 做补充检查，并断言没有遗漏可执行模块：**87 个受影响/新增源文件，覆盖 1,531/1,681 条可执行增量行，91.08% ≥ 80%**。没有以提交差异为空冒充新代码已达覆盖率，也没有为测试目的暂存、提交或改动用户原有文件。

### 其他检查

- 最后只更新交付状态/API Context Boundary 的 provides 导航后，重跑 `tests/community/architecture/test_module_boundaries.py`：**3 passed、17 warnings，3.61s**；没有再次改变运行实现。

- 新增源代码/专项测试已执行 Ruff format 与 F401/F811/F821 检查并通过。另对全部受影响源文件做同项扫描，两个 legacy 文件仍有 **7 项基线 F401**：`products/bbs_public_discussion/service.py` 3 项、`products/task_commission/profile.py` 4 项。已用 `git show HEAD:<path>` 送入同一 Ruff 规则复核，基线同样报这 7 项；按“不清理本次变更未造成的 dead imports”规则保留，不能称全受影响文件 Ruff 全绿。
- 最终核验：全部新增/修改源文件均不超过 1,000 行；126 个受影响 Python/SQL/Markdown 文件的末尾换行/尾空格检查通过；新文档 103 个相对链接存在；`git diff --check` 通过。不加大文件豁免、不更改 mandatory gate。
- v2 SQL 使用 SQLAlchemy MySQL dialect 生成；SQLite 真实事务/回滚、CAS、同租户/跨租户隔离有本地测试；MySQL DDL 显式指定 InnoDB/utf8mb4 及二进制 key collation，不能把 SQLite 验证等同于目标 MySQL 行为。**目标 MySQL DDL 尚未执行**。

### 提交准备复核

- 暂存树新增内容的明文凭据扫描、仓库 SAST block gate、`git diff --cached --check` 均通过；没有修改扫描规则、关闭 hooks 或跳过强制门禁。
- 复用上面完整 CI 生成的 JUnit/coverage 报告，以最新 `origin/dev` 的 merge base 与暂存树运行标准 `singlebox/ci/report_check.py`：通过率 **100%**、全源覆盖 **90.80%**、实际可执行增量覆盖 **91.08%（1,531/1,681）**。此处不是重新执行完整 pytest。
- 仅规范化 SQL 末尾空行及测试夹具写法后，重跑产品专项、已注册进度端点和 Service API conformance：**356 passed、18 warnings，7.27s**，含 MySQL DDL 离线一致性测试；生产 Python 实现未再改变。提交范围为 126 个 Backend Python/SQL/Markdown 文件，不包含本机状态、日志或缓存。

## 3. 失败、修复与运行限制（与通过结果分开）

开发中确实遇到并处理了以下失败，不以旧通过数覆盖：

1. 新测试目录缺少 `__init__.py` 导致全量同名模块 collection 冲突：补包标记后恢复。
2. 初次完整回归测试大部分通过，但 endpoint coverage gate 拒绝只写自建 TestClient 的新路由：新增完整 app 注册 happy/error 案例，未豁免 gate。
3. 已通过专项测试后发现干预撤销会涉及不应变更的历史记录：改为只撤销当前轮次非终态分配，并增加“接受的前驱事实保持不变”断言。
4. 独立签名 ingress 专项中，最小 app 缺少项目 DomainError handler、测试使用不存在的运行状态 `completed`：分别装配实际 handler，并改为 provider 合同的 `final`。未放宽生产鉴权或运行状态合同。
5. 前一版完整 pytest：**35,341 passed、41 skipped、13 subtests passed，542.45s**，随后 CI report_check 被本机 `python` 的 Xcode 占位命令中断，因此不能记为完整门禁通过。改用上述 venv PATH 重新运行。
6. 一次重跑在 11,915 passed、11 skipped 时主动中断，以纳入进度解析异常链脱敏和可信 accountable owner 的最后修复；不计作一次通过。
7. 提交准备时暂存检查发现新增 SQL 文件末尾空行，安全扫描还保守拦截了测试内的动态 JWT 表达式及模拟 token provider 构造。清除多余空行，将 claims 构建与签名分开，并使用明确的 `test-token` 占位和独立 mock 方法赋值；真实签名验证及授权断言不变，复跑扫描和上述专项后通过。没有在门禁中添加例外。

公开默认执行/候选授权/evidence 能力仍 **fail closed**，交互默认 DO_NOTHING。部署方必须绑定实际参与/资源授权、身份、运行及验收能力，并在正确 tenant/env 调度 worker；不能为了演示给所有人授权或假造通过。当前没有新增隐式后台 scheduler，也没有提供未知派发自动重试。

未执行：真实 Bot/会话/provider、资源验证服务、远端取消确认、企业集成、目标 MySQL 迁移、旧 writer 排空/停写/导入与回滚演练、部署和生产负载测试。没有吞吐/恢复时延改善的量化结论。

## 4. 后续切换与知识更新

旧配置、字符串 ID、ResourceBinding Artifact、旧成功报告和 Work 的治理语义无法天然一对一转换。迁移准备代码仅输出显式可审计的准备结果；不可表达或缺可信事实时拒绝，**没有静默丢字段、默认 passed=true 或合成历史 session**。v1 保留原 discriminator 和自己的 Task 权威，v2 不回写其表。

上线前须由责任域完成：支持集合/治理等价性评审 → 提供完整源快照和精确映射 → 停止旧 writers、排空在途 → 经授权导入及核验 → 切换 facade/Work 消费者 → 验证回滚 reader。当前未提供 bulk importer，因此不能把准备结果当作可直接执行的线上迁移。

已更新的 owning 导航：[Backend](../../README.md)、[context_collab](../../src/agentclaw/community/core/task/context_collab/README.md)、[L5](../../src/agentclaw/community/core/task/context_collab/l5_methods/README.md)、[L6](../../src/agentclaw/community/core/task/context_collab/l6_products/README.md)、[存储指南](../../src/agentclaw/community/core/task/context_collab/l6_products/PROFILE-STORAGE.md)、[API](../../src/agentclaw/community/api/README.md) 与 [新产品运行模块](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/README.md)。依赖反向索引由架构测试生成，不手工编辑。没有宣称新的 ADR 已获得额外批准，也未为本次实现引入架构豁免。

旧文档中基线已缺失的 Swarm 专项/历史导航见 research E12；没有用 pycache、历史结果或新 v2 测试去冒充那些旧文件已恢复。


## 5. 用户追加修订：精简五方法参数

基于已经提交并推送的 `000f640ac4`，按用户追加要求，从 ProductProfileService 的五个公开方法、Protocol 和全部仓库内调用方移除 `context`、`request_id`，HTTP 创建/干预正文也不再包含 `request_id`。旧关键字和未知正文字段不继续作为可选兼容参数接纳。`interactive_context` 是业务反馈文本，不属于本次移除的身份上下文。

- G1 身份载体增加 transport-free 的 `actor_context_scope` / `current_actor_context`。HTTP 通过共享的已认证依赖绑定并清理作用域；库入口必须先认证并建立同一作用域。无匿名/默认 owner 降级，服务单例不保存调用者。嵌套异常恢复、缺失身份拒绝、并发任务隔离及线程 offload 的作用域传递均有测试。
- 三类写操作为每次调用生成内部回执标识，保留 SQL 原子提交和回执存储，不再承诺基于客户端键的重放。相同内容重复 create 会产生独立 Profile/Collet，不按名称或内容偷偷去重。
- `expected_revision` 继续作为干预的必填 CAS 围栏；已经提交的 revision 再次调用会冲突，调用者须重新查询。会话 `limit` 的范围限制不变。独立签名 Bot 进度上报的 ReportContext、request_id/attempt/expected_revision 和重放接纳合同完全不变。
- 没有修改数据库结构、旧 v1 兼容接口或给现有账户扩大权限；旧批量迁移、真实 provider/目标 MySQL、企业集成和部署仍保持前文所列未验证边界。本轮没有另行 commit/push、合并或部署。
- 已同步 [当前产品运行指南](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/README.md)、[spec](spec.md) 和 [plan 修订说明](plan.md)。前文原实现的客户端创建/干预幂等结果是历史验证，不应再当作本轮的公开接口承诺。

### 本轮验证及已修复失败

- 修正后的产品专项、独立进度 HTTP 和 Service API conformance：**372 passed、18 warnings，7.95s**。
- 首次完整回归在 **31,243 passed、13 skipped、6 subtests passed** 后，由新增并发身份测试的 `runtime configuration is missing` 失败停止，耗时 **448.32s**；这次不是完整 CI 通过。该测试同时在共享一条 SQLite 连接的 StaticPool 夹具上打开多个跨线程 SQL 会话，超出了该夹具的并发事务能力。修正为保持调用者作用域及线程上下文并发、仅串行使用这条测试连接；依然通过同一真实服务分别断言 owner 可读和 outsider 被拒绝。没有在生产代码增加串行锁、模拟被测服务、删除断言或跳过此测试。
- 在 `COVERAGE_CORE=sysmon` 下对 9 个身份/签名形状用例连续执行 5 轮，**每轮 9 passed，共 45 次通过**；没有关闭覆盖率或改变完整 CI 的 4-worker 配置。
- 修正夹具后，保持相同命令及 4-worker 配置重跑完整 Backend CI：**35,376 passed、41 skipped、651 warnings、13 subtests passed，633.38s（10m33s）**，最终输出 **`backend CI gate passed`**。报告检查器统计 case pass rate **100%（35,430/35,430）**、全源行覆盖率 **90.80% ≥ 75%**；架构、完整 app 注册端点、合同和覆盖率门禁均保持启用。
- 本轮未提交，完整脚本的提交范围增量 **91.12%（1,528/1,677）** 不是本次参数精简的独立增量。为验证实际修改，另以 `HEAD` 到包含工作区 15 个相关文件的临时 Git tree 运行标准 `singlebox/ci/report_check.py`，并用同一报告解析函数核对 `git diff HEAD` 的工作区新增行和未跟踪源文件：两者均通过，**4 个修改的 Python 源文件，可执行增量覆盖率 100%（36/36）≥ 80%**。复用本轮成功 CI 的 XML 报告，未遗漏可执行模块，没有改动真实 Git index 或为测试创建 commit。
- 本轮 11 个修改/新增 Python 文件的 Ruff format 与 F401/F811/F821、15 个相关文件的末尾换行/尾空格/源文件行数限制与相对文档链接、`git diff --check` 均通过。针对实际工作树的明文凭据扫描及仓库 SAST block gate 也通过；没有新增豁免、降低阈值或关闭 hooks。首次失败记录保留如上，不以最终通过数抹除。


## 6. 用户追加修订：干预唯一 ID 与内部并发围栏

在上一轮未提交的参数精简工作区上继续修改。`interactive` 的公开签名改为 `interactive(collet_rollout_id, interactive_uuid, interactive_context)`，Protocol、HTTP 正文、调用方及精确签名测试同步更新；不再接收 `expected_revision`，也不恢复旧 `context`/`request_id` 参数。独立 Bot 进度上报的参数和 CAS/幂等合同不变。

- 调用者为同一干预保持相同 UUID（非空白字符串，最多 128 UTF-8 字节），新干预使用新 UUID。复用现有 tenant/env/actor 作用域内的 SQL 唯一回执：同 UUID、同 rollout ID/反馈文本返回原结果；改内容或换 rollout 冲突。授权在回执读取前执行，不向越权调用者泄漏结果。历史 rollout 上已提交的干预也可以重放，新干预仍须指向当前 rollout。
- 干预结果、revision 更新、后继身份、停止事实、取消/推进意图与回执同事务提交。内部捕获已加载的 revision 做 CAS；重复调用若在回执预读后输掉 CAS，则从新事务复查 UUID 回执，有原结果就返回，没有则继续报冲突。不同干预或进度竞争不会静默覆盖，也不自动重算旧建议。服务重建后从 SQL 重放，不依赖内存锁或缓存。
- 并发重试可能重复计算无副作用的 proposal，新增能力合同注释明确其不得直接持久化或派发/取消；幂等保证覆盖已提交的干预效果，不声称外部模型请求只能调用一次。创建接口的独立 ID 语义、身份作用域、SQL schema、真实 provider/目标 MySQL/企业集成和部署边界均不变。
- 已同步 [当前产品运行指南](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/README.md)、[spec](spec.md)、[plan](plan.md) 和 proposal 能力合同。本轮仍未 commit/push、合并或部署。

### 本轮验证与失败记录

- 首次专项在 **59 passed** 后，由新 CAS 竞态测试的同步等待超时停止：用 RE_DO 在 SQLite UPDATE 前暂停时，前面的后继身份 INSERT 已占据单 writer 锁，阻止对手提交。将这个特定竞态改为在没有前置 INSERT 的 DO_NOTHING 上验证真实 CAS；后继回滚和 RE_DO 重放由其他真实事务案例覆盖。未修改生产并发保护、扩大超时、跳过用例或放宽断言。
- 第二次专项在 **67 passed** 后，同一新测试因工作线程尚在 SQLAlchemy 监听器回调内就移除监听器，触发 `deque mutated during iteration`。改为释放屏障并等待重复调用退出后再清理测试监听器；生产实现未因这两次夹具失败而改动。
- 修正后的产品专项、独立进度注册端点及 Service API conformance：**397 passed、18 warnings，23.01s**；完整 app 产品注册端点、全部架构及 HTTP 快速门禁：**524 passed、44 warnings，91.00s**。包括新增真实注册的 UUID 内容冲突 409 案例。
- 两种并发重复（提议期间对手提交、save 回执预读后输掉 CAS）在 `COVERAGE_CORE=sysmon` 下连续执行 3 轮，**每轮 2 passed，共 6 次通过**。测试使用文件 SQLite 的独立连接，没有用单连接 StaticPool 并发事务冒充持久化竞争。
- 完整 Backend CI 使用同一 `scripts/ci_test.sh`、4 workers 与标准覆盖率阈值，显式以本轮修改前/后的临时工作区 tree 作为 `--base`/`--head`（仅使用独立 `GIT_INDEX_FILE` 建快照，真实 index 保持为空）：**35,402 passed、41 skipped、651 warnings、13 subtests passed，644.37s（10m44s）**，最终输出 **`backend CI gate passed`**。case pass rate **100%（35,456/35,456）**，全源行覆盖率 **90.80% ≥ 75%**，本轮新增可执行行覆盖率 **100%（14/14）≥ 80%**；不是用空提交差异跳过增量门禁。
- 复用本轮完整 CI 生成的 XML 报告，额外对 `HEAD` 到全部未提交修改的 tree 运行标准报告检查器，并用工作区 diff/未跟踪源文件扫描核对遗漏：两者均通过。包括上一轮参数精简在内，共 **5 个修改的 Python 源文件，增量覆盖率 100%（47/47）**。CI 后源码/测试与已验证 tree 的内容哈希一致，仅追加本交付记录，没有再次改变实现。
- 全部 **13 个**修改/新增 Python 文件的 Ruff format 与 F401/F811/F821、**17 个**相关文件的末尾换行/尾空格/源文件行数上限与相对文档链接、`git diff --check` 均通过；真实未提交修改的凭据扫描和仓库 SAST block gate 通过。未改 mandatory gates、hooks 或真实暂存区，未执行本轮 commit/push。真实 provider/目标 MySQL、企业集成和部署仍未验证，不把本地 SQL/Protocol double 结果当作外部联调证据。
