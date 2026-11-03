# 检索公共能力抽取验证（2026-10-27）

分支：Avernet / OCB 均为 `codex/bcsfuse-search-core`。本轮从已完成的目录迁移继续；结果是本地代码与测试证据，不代表预发部署或生产容量验收。

## 完成范围

- `search_core` 承接向量值、模型/索引接口、Qdrant Local 向量和稀疏关键词执行、Embedding 和精排 HTTP 调用。增加通用的独立双路召回、RRF、可选精排流程。
- TC 使用薄适配层，保留 Worker ID 关键词优先、Profile 分片聚合、阈值、Fuse 和诊断记录；v1 不切换到新的中立流程。旧导入位置重导出同一模型类型。
- 本地 `dev/dev_smoke` 改为 SQLite 保存向量和字段，Qdrant Local 构建向量/关键词索引。启动重建、状态更新、删除、重启恢复及快照已有测试。FAISS 实现、安装依赖和专属实现测试删除；通用业务用例迁到 Qdrant。
- OCB 移除 FAISS 依赖，并验证支付宝 HTTP 工厂能组装真实公共检索和临时 Qdrant 索引。BCN Card、运营策略、内部生产配置、TR 不在本次实现中。

## 执行结果

| 检查 | 实际结果 |
| --- | --- |
| 公共包、契约、开源启动、单元/业务、安全、Golden，以及相关离线 HTTP/持久化集成测试 | **3353 passed / 50 skipped / 2 failed**；失败为下述原有基线问题 |
| OCB composition / contracts / ci / alipay | **170 passed / 1 skipped**；跳过项需真实 MySQL |
| 公共包 wheel 在独立虚拟环境安装测试 | **36 passed**；安装模型、Qdrant、HTTP 可选依赖，不安装 TC |
| 公共包与 TC 应用 wheel 构建 | 均成功 |
| 移除测试环境 FAISS 后运行上述公共回归 | 已执行；环境中已卸载 `faiss-cpu` |
| 修改的 Python 文件语法/未定义引用检查（Ruff E9/F63/F7/F82） | 通过；54 个修改/新增 Python 文件，最长 620 行 |
| 同规则扫描整个公共源代码 | 21 项既有问题；对照本轮修改前 HEAD，新增 0 项 |
| Git diff whitespace | 通过 |

原有基线失败，未因本次搬迁降低断言或跳过：

1. `test_golden_regression_main.py::test_11_explicit_offline_participant_warning`。
2. `test_golden_smoke.py::test_app_basic_structure`：当前 FastAPI 的 `_IncludedRouter` 没有 `.path`。

复核发现并修复了两个本地兼容问题：补齐按完整 Worker/Profile 删除全部片段；快照改为只读、严格解析，损坏或缺表时保留当前数据。新增测试先复现失败再验证通过；完整回归在修复后重跑。

原始测试输出位于本机 `/tmp/bcsfuse-core-extraction-20261027/`；该目录是临时验证产物，不作为部署输入，也不提交日志或运行数据。

## 命令与限制

公共测试在 `Avernet/apps/bcsfuse` 执行，`PYTHONPATH=src:packages/search-core/src`：

```bash
python -m pytest packages/search-core/tests tests/contract tests/opensource tests/unit \
  tests/application tests/domain tests/golden_regression tests/security tests/verification \
  tests/smoke/test_smoke_main.py tests/integration/test_worker_vector_index_flow.py \
  tests/integration/test_worker_runtime_vector_sync.py tests/integration/test_isolated_runtime_acceptance.py \
  tests/integration/test_opencore_app_startup_health_e2e.py \
  tests/integration/test_opencore_dev_smoke_provider_registry_e2e.py -q --disable-warnings --tb=short
```

OCB 按配套公共源码路径设置 `BCSFUSE_PUBLIC_ROOT` 和 `PYTHONPATH` 后运行
`python -m pytest tests/composition tests/contracts tests/ci tests/alipay`。

- 模型协议由受控 HTTP 响应验证，未调用真实模型、没有新增吞吐或效果结论。
- Qdrant Local 仍串行访问；这次不是 Qdrant Server 或高 QPM 优化验收。
- 公共 `uv.lock` 已离线解析更新。OCB 因内部 SDK 不在本机缓存，无法完整离线重新解析；通过 TOML 解析器仅删除 FAISS 包及直接依赖记录，保留其余锁定版本，组合及构建脚本测试通过。
- 本机无 Docker，未执行容器镜像构建；未访问真实内部存储、未部署、未实现 TR。
- 原 FAISS 文件不删除、不自动导入。开发数据需重新同步；旧 FAISS 快照不能导入新存储。新快照保存 SQLite 数据，恢复时重建索引。
