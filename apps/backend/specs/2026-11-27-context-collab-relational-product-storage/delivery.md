# 五表存储改造：开发进度与验证记录

日期：2026-11-27。状态：**第一阶段切换完成，完整执行协议尚未交付**。

以下初始实现与验证为历史记录，其中七个物理外键已被文末“移除物理外键”修订替代；不得将原外键测试结果作为当前数据库引用保护的证据。

## 已实现范围

1. 增加 [MySQL DDL](../../src/agentclaw/community/core/task/context_collab/sql/2026_11_27_product_relational_storage.sql)、三个新模型和共享 Collet/Event 新列、索引、七个真实外键。Stage 保留约定的 15 列，不含 attempt 等七项排除字段；Collet 只增加约定四列，Event 只增加两个关联列。
2. 增加严格 Profile config YAML 编解码、独立生命周期枚举及存储元数据记录；新创建 DRAFT、creator=modifer 来自可信主体；保留四字段 execution encode，不改变执行摘要。不向旧 snapshot Profile 解码器补造历史生命周期/审计事实。
3. 增加独立 Profile 使用授权 Protocol 与默认拒绝实现。业务仓储显式注入发布、使用及运行读取授权；不凭 creator/modifer 赋权。
4. 增加 `SqlProductBusinessRepository`：新建/读取 Profile；在 Profile 锁定并重读 ONLINE 后，一个事务建立真实 Collet、初始 rollout、静态 stages、current 指针和 creation Event；提供一致快照的授权业务历史/类型明确的子级定位。无旧六表读写、无控制快照、无通用回执/Outbox/slot。
5. 增加阶段 JSON/容量解码校验、按确切 YAML name 顺序加载、完整父子/axes 检查及 12 MiB 聚合边界。SQLite 显式 BEGIN/BEGIN IMMEDIATE，MySQL 显式 REPEATABLE READ；其他未验证方言拒绝。
6. 旧字符串版本 Profile ORM 指向 `_legacy_v1`，保留旧字节/摘要；L4 读写与 Task 解码拒绝 product roots，L4 append 保留 product audit 命名空间。已有环境部署前必须完成同名表检查和条件更名；本轮未执行数据库迁移。
7. 增加反馈确定性 event_id 编码，但**没有开放反馈 writer**；真实 SQLite 反例证明当前 Event 唯一索引不提供跨 context 仲裁。

当前能力与部署边界已提升到 owning [RELATIONAL-STORAGE.md](../../src/agentclaw/community/core/task/context_collab/l6_products/persistence/RELATIONAL-STORAGE.md)。该仓储只实现业务存储基础，不实现旧 `RolloutRepository` Protocol；新业务 view 不伪造 revision、attempt、route 或 MultiRollout 控制字段。

## 尚未完成，不能据此上线切换

- 方案第 4.1 节缺口：移除控制事实后，异步派发/回调、防陈旧、完整 session 路由、取消、运行恢复、策略 fence/预算/deadline 的替代合同及其真实宿主能力。
- 无 attempt/revision 的签名进度协议、执行适配器和消费者；自动观察/验收落库、父级汇总、human 反馈后 Agent 决策及后继 rollout 的完整执行链路。
- 方案第 4.3 节跨 context UUID 仲裁，以及 Agent 事务外执行后的合法提交依据。
- 五表仓储的正式服务/HTTP/DI 切换、正常 metadata 中旧六表退出、状态管理入口及历史数据迁移/回滚工具。当前 ProductProfileService/worker 继续使用旧 v2，不能把新业务 view 强塞给旧引擎。
- 真实 MySQL/OceanBase DDL、并发锁/隔离/字符集限制及真实 provider/host 验证。

未另建被排除的五张配套表，未将被删除的控制字段藏入其他业务列/Event；也未用默认空值或 process cache 冒充持久化。因此不能把本轮进度写成“开发和测试全部完成”。

## 实际验证

### 开发前基线

```bash
cd apps/backend
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/core/task/context_collab/product_profile_service \
  tests/community/core/task/context_collab/multi_rollout \
  tests/community/repository/task/context_collab -q --tb=short
```

结果：594 passed，19 warnings。

### 新增存储套件

```bash
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/repository/task/context_collab/product_relational -q --tb=short
```

结果：45 passed。真实文件 SQLite、多连接、PRAGMA foreign_keys=ON；覆盖五表独立建库、原子创建与 Event 注入故障回滚、current/前驱/阶段复合 FK、跨用户配置使用与运行 owner、DRAFT/OFFLINE 拒绝、确定性反馈 ID 及唯一范围反例、纯读、原 YAML 阶段顺序、严格 YAML/JSON、8192/8193 字符及 65535/65536 UTF-8 字节边界。另使用真实 CommunitySqliteDatabase 验证其已经开启事务时不会再次 BEGIN，创建和查询均可通过真实插件独立读取验证。

### 原合同及新增测试组合

```bash
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/core/task/context_collab/product_profile_service/test_models_yaml.py \
  tests/community/repository/task/context_collab/test_models.py \
  tests/community/repository/task/context_collab/product_relational -q --tb=short
```

结果：114 passed。开发中曾有一次较大回归为 1937 passed / 3 failed：一处因尝试扩展旧四字段 ProductProfile，被恢复为原 execution value，并新增独立存储记录；另两处是 L4 新列的精确 schema 断言未同步，现已按新增 DDL 修改，未删除或放宽字段断言。上述 114 个组合测试已复验这三处。

### 完整 Backend CI

```bash
BACKEND_CI_PYTEST_WORKERS=8 bash scripts/ci_test.sh
```

结果分开记录：

- pytest 完整执行：**35602 passed、41 skipped、740 warnings，13 subtests passed**；耗时 736.22 秒。包含架构和现有 context_collab/HTTP/DI/legacy 回归，源文件在本次最终执行期间未再修改。
- 总行覆盖率 **90.81%**。标准报告检查器针对原 git 范围的变更行覆盖率 **90.83%（2784/3065）**。
- 包装脚本最后一步退出 **72**：其 `command -v python` 选到本机 `/usr/local/bin/python` 的 Xcode 占位程序，无法启动报告检查器；pytest 本身已成功完成。**不把这次包装脚本执行记为整体通过**，也没有改脚本或关掉门禁。
- 使用项目 `.venv/bin/python` 对相同 JUnit/XML、阈值和已 fetch 的同一 git 范围补跑 `singlebox/ci/report_check.py`：case pass rate 100%，line coverage ≥75%，change line coverage ≥80%，全部通过，退出 0。
- gate 的 base 为 `7c4545e6b4a1d21177d332e53326fb6dfa91956d`，head 为 `d10cb044b603068a137619ff923bcce5b7ae8a63`。`--head HEAD` 不包含本次未提交工作区，因此另基于 `git diff --unified=0 HEAD` 加全部新增 Python source，对 XML 的实际可执行行补算：**工作区新增/修改可执行行覆盖 393/419 = 93.79%**。不拿原 HEAD 范围的 90.83% 冒充本轮工作区覆盖。
- 再次运行包装器时应将项目 venv 放到 PATH 前面，例如 `PATH="$PWD/.venv/bin:$PATH" BACKEND_CI_PYTEST_WORKERS=8 bash scripts/ci_test.sh`。该整条修正后的包装命令未重复跑第三遍；已单独补验失败的报告门禁。

补验命令（仓库根目录，base 使用上次脚本已 fetch 的同一 merge target）：

```bash
apps/backend/.venv/bin/python singlebox/ci/report_check.py \
  --junit apps/backend/pytest_report/TEST-junit.xml \
  --coverage apps/backend/pytest_report/TEST-cov.xml \
  --source-root "$PWD/apps/backend/src" \
  --min-case-pass-rate 100 --min-line-coverage 75 \
  --base 7c4545e6b4a1d21177d332e53326fb6dfa91956d --head HEAD \
  --min-change-line-coverage 80
```

一次中间的四 worker 全量运行在修正真实 SQLite 插件事务适配后主动中止并重跑，不能算通过；上述八 worker 结果才是最终源码的完整 pytest 证据。

### 静态验证与未验证边界

- 改动 Python 的 ruff format 与 `ruff check --select F` 通过。
- 新增/修改 source 均未超过 1000 行；`git diff --check` 通过。
- 修改/新增 Markdown 的相对链接检查通过；`specs/README.md` 原有 `2026-11-23-swarm-execution-interactions/spec.md` 死链接在 HEAD 中已存在，本次保留并单列，不称整个旧导航全部无误。
- 新模型的 MySQL 方言编译/类型/默认值和实际 SQLite FK 已验证；不是目标 MySQL/OceanBase 执行证据。
- 当前机器没有可用的 mysql/docker 命令，未运行目标库、生产迁移/回滚、provider/host 集成。
- 未进行 commit/push，未执行部署或删除实际表。既有文档改动保留，当前仅在工作区继续开发。

## 后续调整：Profile config TEXT 与索引名（2026-11-27）

- 按用户要求将目标 `ac_context_collab_product_profile.config` 从 MEDIUMTEXT 改为 TEXT，DDL/ORM/方案保持一致；非唯一 scope/name 索引改名为 `idx_cc_profile_scope_name`，列顺序仍为 avernet_tenant/env/name。
- 完整 YAML 编解码上限同步从 1 MiB 收紧为序列化后 65535 UTF-8 字节；创建和读取均检查，超限不截断。旧字符串 Profile 和 snapshot v2 的 JSON 配置合同不变。
- 补充 MySQL 方言类型/索引断言、实际 SQLite 索引反射、ASCII/中文 65535/65536 字节边界、失败创建不遗留记录，以及拒绝直接写入 SQLite 的超限存储值。
- 当前容量约束及已有五表结构升级前的停写/容量检查要求已更新到 owning [RELATIONAL-STORAGE.md](../../src/agentclaw/community/core/task/context_collab/l6_products/persistence/RELATIONAL-STORAGE.md)。本次不改变执行链路/DI 切换的未完成状态。

实际验证（`apps/backend/`）：

```bash
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/repository/task/context_collab \
  tests/community/core/task/context_collab/product_profile_service -q --tb=short
```

结果：**508 passed，19 warnings**。修改 Python 的 Ruff lint/format、文件行数、Markdown 相对链接和 `git diff --check` 均通过。本次未重跑 Backend 全量 CI，未执行目标 MySQL/OceanBase ALTER、实际迁移或部署，未 commit/push。已有环境不能仅更新 bootstrap SQL；必须先处理超限历史配置，再按实际 schema 评审列类型调整和索引重命名。

## 后续调整：移除物理外键（2026-11-27）

- 按“研发规范禁止外键”的要求，移除 ColletRollout 的 `fk_cc_rollout_collet` / `fk_cc_rollout_predecessor`、StageRollout 的 `fk_cc_stage_rollout`；同份 SQL 中 Collet/Event ALTER 的其余四个外键也一并移除，避免后续步骤同样报错。ORM 不再注册任何上述外键，关联 ID 列、普通/唯一索引全部保留。
- 当前原子创建、Profile 行锁、current/前驱/阶段父子链和 creation Event writer 校验保持不变。将依赖数据库外键拒绝的测试改为在真实 SQLite 写入坏关联后，验证仓储读取明确拒绝；补充缺父/孤立 stage、审计写入前拒绝和唯一键回滚测试。
- 验证五表 metadata、MySQL 方言编译和实际 SQLite 反射均无物理外键，迁移 SQL 无外键语句；SQLite 仍保持 `PRAGMA foreign_keys=ON`，没有关闭检查或绕开门禁。两张新表的关联索引在 ORM 和实际 SQLite 中均精确验证。
- 同步方案全部六条 CREATE/ALTER 和 owning [RELATIONAL-STORAGE.md](../../src/agentclaw/community/core/task/context_collab/l6_products/persistence/RELATIONAL-STORAGE.md)。明确数据库不再提供引用 RESTRICT 保护；尚未开放的 stage/反馈 Event writer、后继运行和删除操作必须配套事务校验，不能宣称本次已补齐这些运行路径。
- 已存在的外键不会因更新 bootstrap 自动消失，需停写、核验实际 schema 后走评审过的升级操作；关联列/索引不得随之删除。此前部分 DDL 若已成功，续执行前先检查已增加列和索引，不盲目重跑整个脚本。

实际验证（`apps/backend/`）：

```bash
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/repository/task/context_collab/product_relational -q --tb=short
```

结果：**61 passed，18 warnings**。本次未执行目标数据库 DDL/迁移或部署，未 commit/push。

相关仓储、服务、MultiRollout 与架构回归：

```bash
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/repository/task/context_collab \
  tests/community/core/task/context_collab/product_profile_service \
  tests/community/core/task/context_collab/multi_rollout \
  tests/community/architecture -q --tb=short
```

结果：**1119 passed，45 warnings**，170.44 秒。修改 Python 的 Ruff check/format、source 行数（均不超过 1000 行）、Markdown 相对链接/锚点、六条 DDL 与方案逐条一致性及 `git diff --check` 均通过。本次未重跑完整 Backend CI，也没有目标 MySQL/OceanBase 执行证据；不将 MySQL 方言编译和 SQLite 验证写成目标库部署通过。

## 后续调整：Stage 唯一键与普通索引命名（2026-11-27）

- 按用户要求移除 `ac_context_collab_stage_rollout` 的 `uk_cc_stage_name`、`uk_cc_stage_parent_id` 两个唯一键；SQL/ORM/测试和 owning 文档同步更新。阶段名称重复不再由数据库拒绝，改由仓储按确切 Profile YAML 分组并在读取时 fail closed；新增反向用例证明无数据库约束时重复名称仍被业务读取拒绝。
- 五表本次新增的普通索引名统一改为 `idx_` 前缀：`idx_cc_collet_profile_status`、`idx_cc_collet_current`、`idx_cc_rollout_predecessor`、`idx_cc_rollout_scope_collet`、`idx_cc_stage_scope_rollout`、`idx_cc_event_rollout`、`idx_cc_event_stage`、`idx_cc_event_stage_parent`。DDL/ORM/实际 SQLite 反射和方案一致，当前迁移 SQL 中不存在 `ix_cc_` 名称。
- 已安装环境不会被 bootstrap 更新自动改名或删键：需停写、核验实际 schema，再经评审的升级操作把旧 `ix_cc_*` 改为对应 `idx_cc_*`；删除 Stage 两个唯一键前必须先清理或隔离重复阶段名。关联字段和保留索引不得删除。
- 测试补充 Stage 无二级唯一键、五表普通索引全部使用 `idx_` 且回归配置索引仍为 `idx_cc_profile_scope_name`；把仅依赖 Stage 唯一键的数据库 IntegrityError 测试收敛到 Rollout，Stage 的重复名由应用校验用例覆盖。

实际验证（`apps/backend/`）：

```bash
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/repository/task/context_collab/product_relational -q --tb=short
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/repository/task/context_collab \
  tests/community/core/task/context_collab/product_profile_service -q --tb=short
```

结果：存储专项 **62 passed，18 warnings**；相关仓储和 ProductProfileService 回归 **523 passed，19 warnings**。Ruff format/check、DDL 与方案逐条一致性、Stage 唯一键不存在、新普通索引 `idx_` 前缀、Markdown 链接/格式和 `git diff --check` 均通过。未执行目标数据库 ALTER/迁移，未运行完整 Backend CI，本节变更尚未 commit/push。

## 后续调整：迁移 SQL 字段 COMMENT（2026-11-27）

- 按用户要求，`2026_11_27_product_relational_storage.sql` 中 Profile/ColletRollout/StageRollout 的全部 CREATE 字段，以及 Collet/Event 的所有 ADD COLUMN 字段都补充明确 MySQL `COMMENT`。方案中的 6 条 DDL 与 SQL 文件逐字保持一致，字段含义不再只依赖外部文档。
- 新增静态测试确保迁移 SQL 的 ADD COLUMN 和建表列都有 `COMMENT`，并保留无外键、`idx_` 索引前缀和 Stage 无唯一键的检查。SQLite 不反映 MySQL COMMENT，本项只验证 SQL 文本和方案一致性。

实际验证（`apps/backend/`）：

```bash
DEPLOY_PROFILE=test .venv/bin/python -m pytest \
  tests/community/repository/task/context_collab/product_relational -q --tb=short
```

结果：**63 passed，18 warnings**。DDL 与方案逐条一致、SQL 中无 `ix_cc_` 新索引名、Stage 无两个业务唯一键、Markdown 链接/格式和 `git diff --check` 通过。未执行目标数据库 DDL/迁移，未 commit/push。


## 后续调整：第一阶段服务/DI 切换（2026-11-28）

按“长期方案，不再补建旧 `ac_product_*` 表”的要求，本阶段完成：

- `ProductProfileRuntimeModule` 不再绑定 `SqlProductRuntimeRepository`。`ProductProfileRepository` 和 `RolloutRepository` 均由新增 `ProductBusinessRepositoryAdapter` 提供，底层为 `SqlProductBusinessRepository`。
- Profile 创建通过五表正式存储，不再执行 publication/use allowlist/deny-all 检查；仍依赖已有认证 actor scope 和结构化配置编译，不要求执行/证据/MultiRollout 能力。
- Collet 创建在能力校验通过后写五表：Collet、首个 Rollout、静态 StageRollout、Event 和 current 指针。Profile 必须为 `ONLINE`，默认创建仍为 `DRAFT`。
- 服务查询 `get_collet`/`locate` 从五表业务历史重建，不读取旧表；未实现的 runtime mutation contract 不伪造，也不再回落到旧表。

明确限制：五表当前没有 runtime revision、attempt、lease、dispatch、route、receipt、outbox、slot 或 MultiRollout control facts。适配器对 `save`、late delivery、intent claim/finish 等修改路径显式拒绝；`replay` 不伪造回执。因此 interactive、进度上报、runtime worker 和完整 MultiRollout 控制流尚未切到五表。这解决的是 Profile/Collet 创建链路的生产报错，不是完整执行协议交付。

新增服务适配测试：

```bash
cd apps/backend
DEPLOY_PROFILE=test uv run --no-sync pytest \
  tests/community/repository/task/context_collab/product_relational/test_service_adapter.py -q
```

结果：**3 passed**。覆盖 DI 使用五表适配、Profile 创建不要求 execution/MultiRollout 能力、能力未安装时 Collet 不落库，以及 ONLINE Profile 在 capability 准入通过后原子写 Collet/Rollout/Stage/Event。

组合回归：

```bash
DEPLOY_PROFILE=test uv run --no-sync pytest \
  tests/community/core/task/context_collab/product_profile_service \
  tests/community/repository/task/context_collab/product_relational -q
```

结果：**246 passed**（完整 ProductProfileService + 五表存储套房）。HTTP 与架构：

```bash
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/adapters/http/task/test_context_collab_products.py \
  tests/community/architecture
```

结果：**501 passed**。以上为 SQLite 单测、HTTP 协议替身和架构门禁结果；未执行目标 MySQL/OceanBase 迁移、生产部署、真实 Bot/provider 或 runtime worker 验证。


## 后续调整：结构化 Profile stages YAML（2026-11-28）

- 按“存储内 stages 也要求 YAML 格式、config 总体更可读”的要求，将新写入的 Profile schema 升级为 `product-profile/config@2`。`stages` 现在是结构化 YAML 列表，不再是带 `\n` 转义和续行反斜杠的字符串。
- API 输入仍然接收 `stages: str`。写入前先执行既有 `compile_stages` 严格校验，再规范化为存储原生 stage/candidates/depends_on/acceptance 图；用户缩进、注释和折行不再作为配置权威字节保留。
- 读取兼容旧 `product-profile/config@1` 字符串 stages 格式，因此部署前已创建的 Profile 不会失效；新写入只使用 `config@2`。
- 服务对外 `ProductProfile.stages` 类型仍为 YAML 字符串。新写 Profile 读回时返回规范化的 YAML 字符串，而不是调用方的原始文本。
- 更新编码/解码测试：验证新 YAML 列表、schema 校验、重复/别名/未知字段拒绝、旧字符串格式兼容、以及 MySQL TEXT 65535 UTF-8 字节边界。

验证：

```bash
cd apps/backend
DEPLOY_PROFILE=test uv run --no-sync pytest \
  tests/community/core/task/context_collab/product_profile_service \
  tests/community/repository/task/context_collab/product_relational -q
```

结果：**246 passed**。包括 HTTP 与架构后的组合回归：

```bash
DEPLOY_PROFILE=test uv run --no-sync pytest -q \
  tests/community/core/task/context_collab/product_profile_service \
  tests/community/repository/task/context_collab/product_relational \
  tests/community/adapters/http/task/test_context_collab_products.py \
  tests/community/architecture
```

结果：**747 passed**。本次不需要新增数据库列或执行 DDL；不迁移已有 `config@1` 行，也尚未执行目标库部署。


## 后续调整：受限 ProductProfile 更新接口（2026-11-28）

- 新增 `ProductProfileServiceProtocol.update_product_profile`，Repository seam 同步提供 `update_profile`，并由 `ProductBusinessRepositoryAdapter` 直接写入 `ac_context_collab_product_profile`。更新在 Profile 行锁事务中校验 tenant/env、生命周期、YAML 和完整 config 65535 字节边界。
- 生命周期规则：DRAFT 可修改 `name`、`description`、`stages`，也可在同一次请求中 `DRAFT -> ONLINE`；ONLINE 只允许 `ONLINE -> OFFLINE`。`DRAFT -> OFFLINE`、`OFFLINE -> ONLINE` 和仅重复当前状态的 no-op 拒绝。ONLINE/OFFLINE 配置内容禁止修改，避免历史 Collet/rollout/stage 与上线时配置失配。
- 更新不开放 composition 参数，服务固定重新使用默认 MultiRollout NEST Swarm。修改 stages 时先重新编译校验，再写 `product-profile/config@2` 结构化 YAML。运行能力仍不在 Profile 更新链路中检查。
- 服务先通过记录的 Profile owner 做现有 runtime authorization，再调用仓储；`creator` 保持不变，`modifer` 写入可信调用者，响应通过 `ProductProfileRecord` 返回 description/status/creator/modifer。
- 新增 HTTP：`PATCH /api/v1/collaboration/task/products/profiles/{profile_id}`。请求字段全部可选但至少一个，`status` 仅接受 `ONLINE`/`OFFLINE`，strict schema 拒绝未知字段/DRAFT，入口日志只记录字段名和字节数，不打印 stages/description 原文。本阶段不新增外部 `/openapi/v1` 路由。

验证：

```bash
cd apps/backend
DEPLOY_PROFILE=test uv run --no-sync pytest -q   tests/community/core/task/context_collab/product_profile_service \
  tests/community/repository/task/context_collab/product_relational
```

结果：**257 passed，19 warnings**。

```bash
DEPLOY_PROFILE=test uv run --no-sync pytest -q   tests/community/adapters/http/task/test_context_collab_products.py \
  tests/community/architecture
```

结果：**504 passed，45 warnings**。本次不改变 DDL、不新增表列、不迁移历史 `config@1` 行，也未执行目标 MySQL/OceanBase、生产部署或真实 Bot/provider 验证。
