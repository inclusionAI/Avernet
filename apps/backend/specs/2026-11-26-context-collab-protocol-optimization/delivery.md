# R3 开发与验收交付记录

- 日期：2026-11-26；源码基线：`f1813114104f`。下述开发与验证在提交前的工作区执行；后续提交/推送状态以 Git 历史为准，**未合并、未部署**。
- 输入：[R3 目标合同](spec.md)、[验收计划](plan.md)、[原设计证据](research.md)。原方案文件在开始开发时已经存在，保留其历史记录。
- 结论：新的正式领域对象、五方法服务、SQL 轮次权威、Method/YAML 联合校验、执行/消息端口及独立签名 Bot 进度入口已实现并有本地运行测试。**不能据此宣称计划 F 的旧产品全量切换、存量历史导入、真实 provider/目标 MySQL 验证已完成。**
- 当前 owning 合同：[产品运行模块](../../src/agentclaw/community/core/task/context_collab/l6_products/product_profile_service/README.md)。新 v2 与明确命名的 v1 兼容路径不混写；兼容复核期限为 **2026-12-31**，责任域为 Backend context_collab。

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
