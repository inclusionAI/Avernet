# TC 迁移实现与兼容边界

2026-11-05。实现依据同目录 `spec.md`；本文说明实际代码和切换边界，不代表已经部署或迁移线上数据。

## 当前链路

TC 的公共入口和内部 TC 入口均组装 `app.tc`。历史 `app.adapters.http.app` 导出复用同一应用工厂，
不再启动第二套 Worker/Profile、扫描、评分和向量写入逻辑。支付宝仍由自己的 composition 组装公共 search-core。

每个 Worker 只有一个当前 Card。兼容 Worker/Profile 对象仅供旧 Fuse 读取，不再写独立的活跃关系。
名称和 description 经 TC converter 生成 Doc，长文本按公共 Doc 数量上限分段；关键词和向量分别按 Worker
聚合后经过 RRF、默认精排。显式关闭精排和精排失败降级继续有效。

模型分数原样返回，为有限浮点数，可以小于 0 或大于 1；不再套用旧概率范围或归一化。
旧 `min_score` 参数仍校验原类型和范围，但不在任何阶段筛选结果。
未额外引入候选访问名单、Profile 注册平台、补偿状态机或长期双写。

## 旧接口

下列路径在 `/v1` 和 `/api/v1` 均可用，Fuse 继续使用 `/api/v1/groups/{group_id}/fuse`。

| 路径与方法 | 当前行为 |
| --- | --- |
| `POST /recommend` | 旧请求字段、recommendations、driver/group/type、trace 继续使用；min_score 仅兼容接收 |
| `POST /workers/{id}/sync` | 稀疏合并唯一当前资料；省略/null 的运行状态保留；新 Worker 默认 online/protected；activate 各取值均保存当前内容，成功 profile_activated=true |
| `PUT /workers/{id}/online`、`offline` | 更新当前运行状态及索引过滤；inactive/disabled 不得上线 |
| `PUT /workers/{id}/availability`、`trust-level` | 保留枚举校验，更新过滤标签而不重新生成相同文本的 Embedding |
| `GET/PUT /workers/{id}/config`、`POST /workers/config/batch` | 配置持久化、fusion_enable 默认 false；批量读格式不变 |
| `POST /workers/batch` | 保留 data 映射、workers 列表和 not_found_ids；维持 1～100 条边界 |
| `GET /workers`、`GET/PATCH/DELETE /workers/{id}` | Card 的兼容视图；Worker GET 为原扁平格式；删除发布 tombstone |
| `POST /workers` | 保留旧创建语义：201、默认 offline/private；与 sync 默认值区分 |
| `GET /workers/{id}/profiles` | 返回 items/active_profile_id/total，供 Backend 诊断继续读取；无模型画像时 contents.short_profile 为原始 description 的读取视图 |
| `GET /workers/{id}/profiles/active`、`profiles/{profile_id}` | 返回原扁平 Profile 结构；MD 不作为当前内容恢复 |
| `PUT/PATCH /workers/{id}/profiles/{profile_id}` | 更新同一份内容，保留已有单一兼容标识，不创建新 Profile 或草稿 |
| `PUT /workers/{id}/profiles/{profile_id}/activate` | 已存在当前标识的兼容空操作；不创建绑定，is_active=true；不存在的标识返回 404 |
| `POST /search`、`POST /workers/profiles/search` | 旧结果包装，统一走新检索；不恢复旧全表关键词扫描或 min_score 过滤 |
| `GET /workers/profiles/active-profiles` | 列出唯一当前内容 |
| `GET /workers/config/by-source`、`workers/{id}/profiles/quality`、`profiles/{profile_id}/quality` | 保留管理读取入口；仅返回当前资料/已有质量元数据，不构造 MD 质量评分器 |
| `POST /verify/batch`、`verify/batchAll` | 保留请求校验和入口；能力验证服务未启用时返回原 SERVICE_UNAVAILABLE，不恢复验证事件平台 |

`BCSFUSE_EXPOSE_ADMIN=true` 时保留 `/v1/admin` 的 Worker 创建/删除/PATCH、配置写入/批量写入、
单资料 DELETE 和 analyze 入口。资料 DELETE 清空当前 description/skills，保留 Worker 身份及历史；
analyze 在未启用自动画像生成时明确返回 503，不返回旧固定分数的伪分析。
管理入口仍要求原 Bearer/受信任网关认证，未扩大候选访问策略。

G1/G2/G5/G9 保留既有模式、options、显式成员优先、可用性开关、G9 fusion_enable 和会话历史。
补充成员统一走新检索；显式成员不被新增 public/online 硬门槛排除。
结构化 Worker/Profile 读取与原始 participant 解析分开，避免 `alpha` 和 `alpha:default` 同时存在时串读资料。

## 资料与迁移

- 旧表 `bot_info.summary` 是 description；旧 sync 的 `profile.summary` 对象不直接字符串化或拼接成画像。
- skills 独立存名称和描述，不直接进入主检索正文，不从默认技能推导 expert、builtin/trusted 或虚构能力。
- 历史 MD、旧画像由原存储和显式归档保留；新 TC 不将其自动认作新画像。首期不强制调用模型重建画像。
- 源文本未变时复用已生成 Doc/Embedding；状态和配置只更新标签、版本与缓存。
- 保存和索引失败对调用方可见；索引尚未应用时返回可重试错误，已提交资料可经同一请求重试。

离线工具与归档格式见 `docs/deploy/tc-card-migration.md`。默认只读盘点，显式 SQLite 目标仅用于隔离演练；
部署侧可将同一 `migrate_rows` 注入 TC SQL 仓储，在停写窗口执行。CSV 不是权威运行状态/配置/历史的完整导出，
这些信息须从原存储选取并归档。无效枚举、状态组合和配置在写入前拒绝，计入 invalid，不留下部分坏记录。

TC 显式使用三层独立空间：

| 数据 | TC | 支付宝默认 |
| --- | --- | --- |
| Card | bcsfuse_tc_agent_cards | bcsfuse_agent_cards |
| Doc | tc_search_index_documents | search_index_documents |
| Qdrant | tc_环境_配置索引标识 | 原 composition 的 collection |

不能仅凭数据源别名假定物理库不同。公共 SQL adapter 增加安全表名参数，旧默认值和已有宿主 mixin 保持兼容。
同物理库、同环境、相同 Worker ID、不同索引版本的删除隔离由回归测试直接验证。
内部 TC 的 DDL 与启动依赖在 OCB 的 `apps/bcsfuse/docs/deployment/tc-card-storage.md`。

## 清理与测试归属

| 退出实现/测试 | 保留的验证 |
| --- | --- |
| 旧 ModeAware、语义 ranker、V2、旧 candidate/vector/fragment reranker 算法及其公式、配额测试 | 公共 core 检索/RRF/降级；TC 有效过滤、去重、strict 和原始模型分数契约 |
| 第二套旧 HTTP/R3 Worker/Profile/recommend handlers、后台画像/激活向量编排 | 当前组合的 sync/http/fuse 契约和真实 SQLite/Qdrant Local 重启验收 |
| 多 Profile、独立草稿、激活补偿、绑定写入排序、旧注册仓储注入用例 | 单 Card、历史标识、稀疏合并/清空、状态、配置、失败与重试 |
| 仅导入/断言存在的查询摄取服务、重复 schema 测试、重复旧 golden CRUD | 当前请求 DTO 和实际 TC 组合调用；保留独立 Fuse 业务、HTTP 模型协议和存储契约 |
| 旧全局 store/旧 app fixture | 宿主 provider 优先、组合切换隔离、日志/trace、共享融合历史存储 |

没有删除公共 search-core、支付宝契约以及仍有意义的模型 wire 协议、底层存储持久化与独特 Fuse 业务测试。
不以减少测试数量或提高覆盖率数字为目标。

现有 CI `search-core.yml` 和 `scripts/run_targeted_tests.sh --core` 已改为执行当前链路。
本地命令从 `apps/bcsfuse` 运行：

```bash
PYTHON=/path/to/python PYTHONPATH=src:packages/search-core/src bash scripts/run_targeted_tests.sh --core
```

G9 原有“队列中有任意等待任务就拒绝画像模型”的分支已移除；同一次请求的会话总结排队
不代表模型过载。并发与超时仍由原 GroupFusionService 的线程池和期限约束，不新增限流平台。

真实 CSV 的隔离演练得到 pre 45、prod 123 条有效 Card，逐条完成 TC converter、Doc 发布和
Qdrant Local 索引对账，全部本地应用版本与保存版本一致。使用的是本地合成 Embedding，
这证明数据加工与恢复链路，不证明线上模型推荐质量。

真实 MySQL、Qdrant Server、ZDAS 和线上模型调用与离线验收是不同证据。本次未执行部署或生产数据迁移。

## 回归中的既有时序边界

并行高负载检查曾触发支付宝 `test_operations_are_not_mounted_by_default` 的模型调用次数断言。
用事件 gate 在修改前的 Avernet 40be509 / OCB 8201f09 与当前代码分别复现，结果相同：
`/ready` 已允许服务时，后台主文和技能示例加工仍可继续，因此一次在线查询期间总计可增加
3 次调用（主文、示例、在线查询各 1 次），并非在线查询执行了 3 次。14 个相关同步、缓存、
检索与支付宝测试文件同基线逐字节相同；这条断言依赖后台加工已经结束的时序，未修改支付宝代码或屏蔽该测试。

## 本地验证记录

| 检查 | 结果 |
| --- | --- |
| 公共 TC 完整 tests | 3321 passed / 153 条件跳过 |
| 现有 `run_targeted_tests.sh --core`（含完整 search-core） | 449 passed / 14 条件跳过 |
| 内部 TC composition/contracts 与支付宝回归 | 471 passed / 15 条件跳过；其中支付宝 359 passed / 13 条件跳过 |
| 真实 CSV 离线 Card → Doc → Qdrant Local | pre 45 / prod 123，全部逐条版本对齐 |
| 代码审查 | 4 项 P2 均补回归并闭环；另修复 G9 排队误判 |

新代码 Ruff 通过；既有源文件未新增 Ruff 诊断；修改/新增源文件均未超过 1000 行，
脚本语法与 diff 空白检查通过。未执行真实 MySQL/ZDAS/线上模型或 Docker 部署验收；
条件跳过不视为这些环境已通过。上述结果均为本地证据，不代表 CI、发布或线上运行结果。
