# 数据库接入核对记录

日期：2026-09-10。本文是代码与手册证据，不替代 [Spec](spec.md) 或真实环境核验。

## 1. 已读取的 ODC 产品手册

使用用户提供的 ODC 知识库，通过语雀 CLI 读取以下文档正文（不是仅根据截图推断）：

| 文档 | 语雀知识库内标识 | 与本次工作的关系 |
|---|---|---|
| 结构设计 | `ob-product/odc/vf3bif1vik6avauz` | 结构包含表结构与发布表名/拓扑；输入 CREATE 不等于执行 CREATE |
| 发布结构 | `ob-product/odc/nleg0yf5x7nh3myb` | 生成 SQL、审批、按执行策略完成变更；同名表存在时会做差异比较 |
| Explorer新建表 | `ob-product/odc/nn5xcagiqr8wy2ag` | 结构设计界面操作与发布表名集、最终 SHOW CREATE TABLE 核验；Explorer 专属类型不作为本次 OceanBase DDL 依据 |
| 第2期-结构设计灰度执行 | `ob-product/odc/vsty2nmztyrg0c3w` | DDL 不保证可回滚，不能用删除表作为常规应用回滚 |

必要结论：单表结构编辑器每个结构输入一条 CREATE；2026-09-10 用户提供的“编写 SQL”批量入口截图允许不同表的多条 CREATE，不能把单表入口限制套用到该入口；发布表名必须匹配代码访问名；dev/prod 项目都需在各自阶段实际执行。ODC 列表元数据可能延迟，用目标库 SHOW CREATE TABLE 核验。

## 2. 已核对的代码路径

Avernet 基线 `43dc46c2c`，内部 Host 核对基线 OCB `e7c4cf20c9`。

| 路径（仓库相对路径） | 事实 |
|---|---|
| Avernet `src/evolverun/clawweb/public/shared/server/db.ts` | resolveDbConfig 选择连接参数；initMysql 建 mysql2 池；SQL 无表名前缀自动映射；runMigrations 在 DDL 被禁用时可跳过；初始化失败会产生 noop |
| Avernet `src/evolverun/clawweb/public/shared/server/db/dialect.ts` | 处理 SQL 方言而非 ODC 项目关联；有 VARCHAR(255)→VARCHAR(190) 转换，需对新监控列保留契约长度 |
| Avernet `src/evolverun/clawweb/public/modules/clawinsight/server/services/insight/insight-runtime.ts` | 接收同一个 IDatabase，构造 Insight Repository |
| Avernet `src/evolverun/clawweb/public/modules/clawinsight/server/repositories/insight-task-index-repository.ts` | 直接访问 insight_failure_task；新表统一采用 insight_ 前缀，不依赖自动表名改写 |
| Avernet `src/backend/src/agentclaw/community/plugin_api/models.py` | ac_bots 是 Backend 模型表名，仅看到它不能证明当前库就是 ClawInsight 所用库 |
| OCB `src/evolverun/clawweb/internal/bootstrap/clawweb/server/bootstrap.ts` | 合并公开配置、内部 default / 环境配置和 override，调用 configureClawWebRuntimeConfig |
| OCB `src/evolverun/clawweb/internal/bootstrap/clawweb/server/index.ts` | initDatabase → createInsightRuntime(db) → 挂载 /api/insight/v1 |
| OCB `src/evolverun/docs/clawweb-internal-development-guide.md` §4、§7.4 | 内部启动可经本地代理访问数据源；受管库 Schema 变更需单独评审发布，不能依赖启动 DDL |

项目方于 2026-09-10 与维护者沟通后确认：内部数据源接入由当前 ClawWeb 链路负责，监控沿用既有连接即可。数据源名称与 ODC 展示名称不同不再作为待确认项；不新增连接或修改 OCB。此结论来自项目方确认，不等于本次已执行真实环境连通性测试。本文不复制内部地址、账户或 Secret 到公开仓库。

## 3. 已确认方案与后续验收

已确认：两张表统一命名为 `insight_monitoring_diagnoses`、`insight_monitoring_bot_checks`；复用现有 ClawInsight 的 `IDatabase` 和内部数据源接入，不再要求重新核对数据源名称映射。

尚未执行的工作属于正常开发与验收：

1. 在目标环境按现有 ODC 流程校验并发布 MySQL 兼容模式建表稿；本次未连接数据库。
2. 通过既有服务账号验收两张新表的读写；个人 ODC 权限不能代替服务权限。
3. Monitoring Repository / HTTP 接口 / migration 均仍为待开发，建表不代表功能已上线。

验收方式：在服务连接与 ODC 各运行 [只读核验 SQL](verify.mysql.sql)；实现后通过契约 mock 上报、GET、ODC 按 event_id 查行，确认三处同一记录。不修改全局数据库连接或索要密码。

## 4. 本次验证范围

- 静态核对两张表的字段、可空性、索引、列/表注释与当前协议；表名和索引名长度均小于 64 字符。
- event_id / bot_id 使用 latin1 / latin1_bin，入口仍严格限制 ASCII ID；最长复合索引声明字符负载 328 字节，未将长诊断文本纳入索引。
- 两份公共接口契约保持逐字一致；本次不改变 HTTP 修订号，不修改 claw-validation 或 OCB 文件。
- 未运行 MySQL/OceanBase SQL 解析、数据库集成测试或业务实现测试。后续 migration 必须与审批稿做跨方言结构一致性测试，不能用静态检查代替实际数据库验证。

## 5. ODC 解析错误修正（2026-09-10）

用户上传旧稿后报 Parser Error，两处错误分别落在 diagnosis.event_id 和 bot_checks.bot_id 的 `CHARACTER SET ascii`。据此推测为当前 ODC 解析器兼容性，而非数据源或读写权限问题；内部解析器未在本地复现。截图批量入口明确允许不同表的多条 CREATE，故不将批量粘贴当成已确认原因。

兼容候选：三处 ID 列统一改成 `CHARACTER SET latin1 COLLATE latin1_bin`，其余字段不变；在 ASCII 合法 ID 集内保留二进制比较语义与单字节索引负载。本次尝试查询 OceanBase/MySQL 官方文档，但未取得可用于核对的正文；不将外部文档检索记为已完成的兼容性验证。上述写法仍需由目标 ODC 校验及建表测试确认。

此修正尚待用户在 ODC 重新校验；静态一致性通过不能代替平台校验或数据库执行。
