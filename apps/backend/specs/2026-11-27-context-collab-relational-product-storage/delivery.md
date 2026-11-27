# 五表存储改造：开发进度与验证记录

日期：2026-11-27。状态：**部分实现，完整方案尚未交付**。

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
