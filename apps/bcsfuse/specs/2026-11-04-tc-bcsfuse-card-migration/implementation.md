# TC 同实例迁移实现与验证

2026-11-05。代码已按确认方案实现；本地证据不代表已发布、部署或迁移线上数据。
本轮在 Avernet `008d2e62a`、OCB `b8c8c2a53e` 基础上完成；规则见 [spec.md](spec.md)。

## 同一新版本、同一地址

| 接口 | 默认 `BCSFUSE_TC_V1_STORAGE=legacy` | 改为 `card` 并重启 |
| --- | --- | --- |
| 旧 Worker/Profile/config/状态/sync/删除 | 旧 Worker/Profile/状态/向量表 | 新 TC Card/Doc |
| 旧 search/recommend/Fuse、已有路径别名及管理入口 | 旧资料、配置和旧索引 | 新资料、配置和新索引 |
| `/api/v2` Card/搜索/推荐/config | 新 TC Card/Doc | 同一套新 TC Card/Doc |

选择发生在 TC 启动组装中。card 下两套 API 共用新服务和一套后台任务；legacy 下两套索引、
缓存和写入范围独立。非法值明确拒绝。没有只切 Qdrant、混读回退、双写或两服务代理切流。
`/ready` 报告 v1_storage、environment 和新索引状态；不把空索引就绪等同于迁移完成。

## 代码边界

| TC 文件 | 职责 |
| --- | --- |
| `app/tc/bootstrap.py` | 一次选择旧 API 后端，始终组装新 API，管理两套服务生命周期 |
| `legacy_catalog.py` | 复用现有旧 Worker/Profile/状态仓储，只构造请求内读取视图，不伪装公共 Card 仓储 |
| `legacy_index.py` | 写旧持久向量/旧 Qdrant，以当前旧状态前置过滤，使用新召回和排序 |
| `legacy_providers.py`、`legacy_transaction.py` | TC 专属旧仓储组装及同一次 SQL 变更的事务边界 |
| `v2.py` | 复用公共 v2 适配器，固定绑定新服务；仅补最小 TC config 入口 |
| `migration_api.py`、`scripts/migrate_tc_cards.py --url` | 复用 CSV/权威 JSONL 字段转换，串行调用 v2 并核对索引，不切配置 |
| OCB `internal/bcsfuse_internal/bootstrap/tc.py` | 延迟提供旧 ZDAS 仓储、环境后缀、原 collection；只在 legacy 使用 |

本轮未修改公共 search-core 源码、支付宝业务源码、支付宝配置或通用 provider。
TC 新 Card 使用 `bcsfuse_agent_cards`，新 Doc 使用 `search_index_documents`，与 zbcsfuse 表名一致。
TC 继续使用原有 Worker Registry 数据源，跨业务通过独立数据源隔离，不修改 zbcsfuse 数据源或配置。
新 Qdrant 继续使用 TC 环境/索引代次空间，避免旧、新链路混用集合。
融合历史沿用原权威存储；它可能与旧资料共处同一物理库，card 模式不能撤掉融合历史访问权限。

## 兼容与资料规则

旧 `/v1`、`/api/v1` 的路径、DTO、枚举校验、默认值、响应包装及 Fuse G1/G2/G5/G9 保留。
`BCSFUSE_EXPOSE_ADMIN=true` 的管理入口也使用选定后端。新接口始终位于同一服务地址。
旧 sync 成功要对应本后端的实际应用版本，不能用另一套 Doc 就绪冒充成功；v2 PUT 的 202
表示源资料已保存，工具随后等待 index-status 的当前版本就绪。

此前已确认的简化继续生效：单一当前资料；activate 只兼容接收；合法 min_score 不筛候选；
关键词、向量、精排默认启用，允许显式关闭精排。检索按 Worker 去重、RRF 合并，保留原始模型分数。
原有可见性、运行状态、生命周期和群相关过滤仍有效，不新增 TC 候选名单。

`bot_info.summary` 映射 description；skills 独立存真实名称和描述，不直接拼正文，不推导默认专家能力。
MD 不进入新检索正文。已有原始资料、画像和 MD 从旧库或显式迁移归档保留，不强制重跑模型画像。
新建 v2 Card 默认 offline/private，旧 sync 默认 online/protected，稀疏更新保留未给字段。

## 必要失败与恢复处理

旧 SQL 的 Worker、Profile、在线状态在一次事务中提交，失败则回滚，不返回假成功。
同进程写入与索引消费串行，避免提交前清掉待处理标记。旧向量计算完成后，发布前核对当前
版本及完整源资料；生产发布阶段以 Worker 行锁保护核对和落盘，Embedding 计算不持锁。
这也防止旧结果覆盖已删除后重建的同 ID 对象。

启动从旧持久向量恢复；已有无标记历史向量直接沿用，不默认重算整个旧库。
本适配写过的当前 Profile 使用既有 metadata 保存恢复标记；缺向量、孤儿向量、受管版本不一致
会恢复旧索引待处理。没有新恢复表、迁移平台或跨新旧源搬迁。新路径仍使用公共 Card/Doc 恢复。
SQLite 仅为单实例开发后端；真实 ZDAS/MySQL 事务、行锁和 Qdrant Server 仍需部署环境验证。

导入前由 `/ready.environment` 拒绝环境不符的目标。请求失败即停止，归档与错误报告不输出凭据。
普通路由 404 不作为删除成功；明确的对象不存在与 tombstone 按既有语义对账。
配置版本属于各自仓储，导入使用新表版本，不把旧版本数字带入新表。
CSV 不能替代运行状态、配置、当前 Profile 选择及历史的权威导出，工具也不自动补齐删除清单。

## 清理与测试

此前清理的旧评分/权重分支、多 Profile 草稿/激活平台、重复 HTTP 编排及冗余测试保持退出。
本轮没有为兼容旧存储恢复它们；只补必要的持久化、读取视图和索引更新。
现有新路径测试显式选择 card，新增测试使用生产默认 legacy，避免测试模式掩盖默认部署行为。
保留公共检索、模型协议、底层仓储与 Fuse 业务测试，删除或改正“TC 没有 v2”等过时断言。

| 最终本地检查 | 结果 |
| --- | --- |
| Avernet BCSFuse `tests` + 全部 `packages/search-core/tests` | 3529 passed / 167 skipped |
| 正式入口 `scripts/run_targeted_tests.sh --core` | 466 passed / 14 skipped |
| OCB BCSFuse 全部 `tests`，包括支付宝回归 | 645 passed / 16 skipped |
| 迁移、同实例及旧 SQL 事务集中契约 | 49 passed |
| TC 内部组合及行锁/连接绑定 | 8 passed |
| 当前版本单进程、同端口配置重启的真实 HTTP | 27 项通过，含 CLI v2 导入及幂等重放 |
| 模块 Ruff、脚本语法、diff 空白、源文件行数 | 通过；新增/修改源文件均少于 1000 行 |
| 最终审阅 | 所有实质问题已修复并补回归，无剩余重要问题 |

跳过为条件用例，不视为外部环境验收。测试日志保存在本机 `/tmp/tc-same-instance-*-final.log`。
运行命令从 `apps/bcsfuse` 执行，Python 使用本地测试环境：

```bash
PYTHONPATH=src:packages/search-core/src python -m pytest tests packages/search-core/tests -q
PYTHON=/path/to/python PYTHONPATH=src:packages/search-core/src bash scripts/run_targeted_tests.sh --core
```

内部测试显式将 `BCSFUSE_PUBLIC_ROOT` 和 `PYTHONPATH` 指向本次 Avernet 工作区，执行 OCB 的 `python -m pytest tests -q`。

真实 HTTP 验收在单个当前版本进程上完成：先默认 legacy，停止后改配置，在同一端口重启 card。
覆盖同 ID 不同数据、读写隔离、前置过滤、config、online/availability、推荐、Fuse、删除、
最终导入与重放，以及切换后旧表/向量冻结。使用 SQLite、Qdrant Local 和离线模型；进程和合成数据已清理。
本地报告保存于 `/tmp/tc-same-instance-verification/report.md`，脚本和 JSON 位于同目录。
历史双实例/代理结果不作为本方案验收依据。

本轮没有执行 CI、发布、部署、真实 MySQL/ZDAS、Qdrant Server 或线上模型验证，未迁移线上数据。

预发启动失败修正：撤掉 TC 专用 Card/Doc 表名，组装和迁移工具统一使用公共默认表名。
数据源继续读取既有 `WORKER_REGISTRY_ZDAS_DATASOURCE`，没有修改配置文件或改接 zbcsfuse。
本次表名修正验证：公开库 3529 passed / 167 skipped，内部库（含支付宝）646 passed / 16 skipped，
原数据源选择契约通过，SQLite/Qdrant Local 同实例 HTTP 27 项通过。
未对部署数据库执行 DDL 或迁移；预发需部署修正版本后再验收，不能据此称预发已恢复。
