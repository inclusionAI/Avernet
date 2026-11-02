# BaaS ORM 短 Session 治理：实施与保留事务

- 日期：2026-10-29。
- 仓库 / 分支：Avernet / `fix_baas_orm_short_sessions`。
- 第一批基线：`d381ddc92`；第一批提交：`3d7f9900a`。
- 本次续改基于第一批提交，覆盖 Community 的 ORM、调用者及相关测试；未修改 OCB
  enterprise overlay、生产 schema、数据库插件或连接池容量。
- 审查覆盖 26 个 ORM Repository / 26 个 ORM Model，包含拆分出的辅助文件。
- 架构约定：[ADR-0016](adr/0016-short-repository-sessions.md)、
  [ADR-0017](adr/0017-transactional-repository-boundary.md)。

## 当前边界

**不是“ORM 不能包含业务逻辑”，而是“不要为无需同一事务的逻辑持有连接”。**

1. 必须一起提交的数据库操作，可以保留在一个 `@with_orm_session` 方法中，包含其
   必要判断。例如版本切换、管理员恢复、Bot/关系清理、锁、JSON RMW 和原子出队。
2. 不要求同一事务的准入策略、跨 Repo 查询编排、失败解释、导出格式化，交给
   Service/Worker；每次 Repo 返回后连接已归还，再作判断或发起下一次调用。
3. SQL 过滤、JOIN、分页、输入形状校验、记录映射并不因为有 `if` 就必须迁移。
   不用全表读取到 Python 替代 SQL 过滤。
4. 当前不引入 Service 事务装饰器，不把远程 I/O、sleep 或异步工作包进事务。

## 第一批已落地（`3d7f9900a`）

| 位置 | 改动 | 连接/一致性边界 |
| --- | --- | --- |
| file_transfer_ticket / session_file_ticket | 状态迁移、幂等和失败解释迁往票据 Service；Repo 暴露条件 UPDATE | 写调用返回后才按需另开短读；保留 env、error_message 语义，不无条件重试 |
| bot_manage 克隆 | 源读取、PENDING 预检、字段组装迁往 Service；删除旧克隆入口 | 短读 → Session 外组装 → 短 INSERT；唯一键裁决竞争，消除持外层连接申请内层连接 |
| bot INSERT 冲突映射 | Session 退出后处理重复键 | 覆盖 flush/commit；SQLite UNIQUE、MySQL 1062 转为领域冲突，其他写异常传播 |
| queue 超时扫描 | Worker 计算超时，Repo 只读有界未完成项 | 保持 oldest 排序和默认 200 条上限 |
| ac_bot_publish | Service 选择 verify/online binding key | Repo 只按显式 key 投影，保留批量查询 |
| bot.complete_destroy | RELEASED 与软删合并为单 UPDATE | 保留 tenant/env/live 条件，避免中间 live RELEASED 槽位冲突 |
| Session DEBUG 日志 | 不再用 session.connection() 获取连接 ID，改为 Session identity | DEBUG 不主动 checkout；方法耗时不冒充 SQL 执行时间 |
| 显式事务管理 | ContextVar + thread/task owner；插件负责 commit/rollback/close | 显式 scope 嵌套加入、错误 rollback-only；禁止跨 thread/task 共享 Session |

普通 `orm_session()` 保持独立；只有显式同步 `scoped_orm_session()` 参加同一事务。
上一批的大 Publish Service 已按职责拆分，未把发布全流程改为大事务。

## 本次续改

| 位置 | 实现 | 保留 / 改变的保证 |
| --- | --- | --- |
| bot_run_queue / Worker | RUNNING 计数准入、release/mark_done 零行诊断上提 Worker | 原子出队仍将有界候选选择、CAS、回读放在一个 Repo 方法；计数仅是 advisory，不是严格跨进程配额；诊断读失败不重试写、不改变原写结果 |
| DeviceBinding / Health | 删除旧的多步设备来源分支、沙箱装配、发布 binding 策略入口；调用原有 QueryService；query_service 改为必需注入，删除 fallback | 无需单事务快照的读取分步结束；保留 SQL JOIN 投影及字段原子更新，不把所有 JOIN 拆成 N+1 |
| Binding 导出 | Repo 每页最多 200 条原始投影；QueryService 在 Session 结束后解析/格式化 | 保留 ACTIVE/arca/env 条件、管理员跨 env 导出；坏 JSON/非对象兼容为空对象；按创建时间和 id 排序 |
| 设备续期 / 过期扫描 | Timer 计算 24 小时续期窗、now-grace 截止时间及默认 TTL，Repo 接收必填参数 | 保留 SQL JSON TTL/per-bot fallback 过滤及 keyset 分页，不全量内存扫描 |
| Relay Session | 状态机、路由校验、幂等、not-found 与 CAS0 诊断迁至新 Service；Router 仅委托 API Protocol | Repo UPDATE 增加 env/来源状态 fence，避免并发重新打开 closed；同状态不覆盖路由/关闭时间；旧 LocalPaasService terminal cleanup 保留无 fence 关闭入口 |
| Relay Service API | `api/paas` 暴露 Protocol 和独立 Info DTO，bootstrap 注入实现 | Adapter 不直接依赖 concrete Service 或 Repo record；沿用 HTTP 400/404/409 及响应结构 |
| Publish 创建 | 默认 name/publisher/副本/批容量/批数/cooldown 迁往 Service helper；Repo 参数必填 | 仅为 None 默认，0 和空串不被覆盖；生产调用者与工厂/测试同步迁移 |
| PublishBatch 阶段查询 | 去掉调用外层装饰器，单次 list_by_publish_id 返回后再做 stage 投影过滤 | stage 来自 extra_config，不是表列；仅一次 checkout，保留 tenant/env/软删/排序 |

DeviceBinding 的三个纯 SQL 查询移到同包 `_queries.py` 以满足文件大小限制，
不改变查询逻辑。修改的大测试文件按职责拆分；Device、Publish Service 和 Publish
Chain 原测试方法保留（部分测试类更名）。删除的旧 Binding 分支 SQL mock 测试，
由现有 QueryService 测试及新增导出/真实池回归覆盖，不声称测试数变化全是拆分。

SQLite integration 暴露外部表 BigInteger 主键不能生成 rowid 的测试建表问题：
测试工具现在复制独立 DDL metadata，仅在 SQLite 的副本中使用 Integer 主键。
新增幂等建表、真实 INSERT 和全局 model 类型不变的回归，未修改生产 model/schema。

## 全部 ORM 的审查结论

下表的“保留”不代表事务天然解决并发问题；也不是“所有逻辑都已搬离 ORM”。
路径前缀为 `src/secbaas/community/core/repository/`。

| Domain | 本轮结论 |
| --- | --- |
| ac_bot | default bot 的单 SQL 前输入 guard / 记录映射保留，不属于持连接的多步编排 |
| ac_bot_publish | verify/online key 选择已由 Service 负责 |
| api_gateway | CRUD、过滤、分页保留 |
| arca_ttl | 方言 upsert、候选 JOIN、SQL 过滤保留；未发现持事务调用远程续期 |
| bot | 克隆上提、销毁合并；版本切换、管理员恢复、Bot/关系清理需要同一事务，保留 |
| bot_device_rel | 关系持久化保留；批量 flush 优化不扩入本轮 |
| bot_qpm | UPDATE→INSERT 技术 upsert 保留 |
| bot_run | JSON RMW 保留；普通事务不保证避免 lost update |
| bot_run_interaction | 唯一键幂等、FOR UPDATE 原子修改保留 |
| bot_run_queue | 超时、准入、失败诊断上提；有界原子出队和 JSON patch 保留 |
| bot_run_queue_chunk | append/read/count/delete 保留 |
| bot_session | context merge/replace 等 JSON RMW 保留；并发控制另行审查 |
| device | 截止时间及默认 TTL 由调用方提供；err_msg prefix/字节截断 + SQL 原子追加属于字段持久化，保留 |
| device_binding | 非事务分支与导出已迁至 QueryService；fallback 删除；SQL 投影、JSON 字段更新保留 |
| device_template | 默认模板的单 SQL 查询及查询过滤保留；先查后删的技术优化不扩入本轮 |
| distributed_lock | 锁 upsert/确认是原子持久化能力，不拆散 |
| file_transfer_ticket | 状态机和失败解释已上提 |
| local_user_machine | CRUD/投影保留 |
| publish | 创建默认值已上提；活跃状态 SQL 条件保留，不据此推断长持连接 |
| publish_batch | 无外层 Session 的阶段投影；原有查询/软删持久化保留 |
| publish_record | retry claim 原子操作保留；外部 provision/sleep 本就发生在 Repo 返回后 |
| resource_key | 映射/查找保留 |
| session_file_ticket | 状态机和失败解释已上提 |
| system_config | CRUD/分页保留 |
| tenant | CRUD/软删持久化保留 |
| ws_relay_session | Service 持有迁移策略；Repo 只执行 env 隔离的读取或条件 UPDATE |

Model 未发现远程 I/O 或跨 Repo 用例编排。另以 AST 检查 Repository 的装饰方法
对同实例方法的调用，本次未再发现“装饰方法 → 另一个装饰方法”的直接嵌套；
这只是静态检查范围内的结论，不等于证明所有运行时动态调用都没有嵌套。

## 有意保留的专项风险

1. `bot.reconcile_unclaimed_pending_bots` 仍是未分批的约 `1+2N` SQL，且 Bot
   更新未重检 PENDING。后续需有界批处理 + 状态复核/锁，不能将多表清理随意拆成独立写。
2. `complete_update_transfer` 的关系迁移、旧 Bot 退役、新 Bot 激活，以及
   `restore_bot_record_by_id` 的 sibling 退役/恢复，都保留同一事务；按当前边界，
   不将这些方法内部的必要逻辑列为“必须上提”的欠债。
3. JSON RMW 的并发 lost update 未新增 CAS/锁解决；现有 QueryService 的 TTL RMW
   同样需要专项审查。本轮没有把 ORM 中的原子需求拆成无事务读/写。
4. Interaction/Lock 的内部 rollback 与未来外层事务的兼容，未泛化解决。
5. Queue 限额仍非严格跨进程配额。导出分页也不是单一强一致快照，并发增删时 offset
   可能漂移；不要将缩短 Session 宣称为这些一致性保证。

## 本次实际验证

| 套件 / 检查 | 结果 |
| --- | --- |
| 全量 `tests/unit` | 9,798 passed，5 skipped，9 xpassed（8 warnings） |
| `tests/architecture` | 74 passed（189 warnings，未放宽 gate） |
| 全量 `tests/contract`（含新增 Relay API v1） | 195 passed，7 skipped，1 xpassed |
| 相关 SQLite integration：DeviceBinding、Device expiry、Publish repositories / Service | 177 passed |
| Relay ASGI baseline | 9 passed |
| Session 文件共享 dispatcher-direct E2E | 11 passed |
| 新增/修改 Python | Ruff check / format；文件 ≤ 1,000 行；git diff --check |

小 QueuePool（size=1、max_overflow=0）回归覆盖：Relay CAS 竞争、幂等、env fence、
写失败与旧 cleanup；Queue 零行/owner mismatch 与诊断读失败；阶段投影只有一次
checkout；Binding 两页导出且 JSON 解析时 checkedout=0。全量 unit 还包含上一批的
克隆、票据、DEBUG 零 checkout、显式事务生命周期和 thread/task 隔离回归。

Skipped / xpassed 不计为普通通过；本轮只移除一个已确定性验证通过的旧 xfail，
其余保留。架构套件仍报告既有技术债和依赖警告，不将其描述为零警告通过。

## 兼容与上线边界

- HTTP/API 路径与响应、生产 schema、pool 容量不变；内部 Repository Protocol 有
  不兼容变更，调用者和实现需成组升级：Queue 不再接收 max_running；扫描增加
  due_before；Publish 创建字段必填；Relay 写返回行数并接收 CAS fence；Binding
  分支入口移除，导出改为 raw-page 接口；Health 必须注入 query_service。
- 本仓库调用者已迁移；OCB enterprise `apps/baas/src` 静态搜索未发现这些删改入口
  的调用，不等于验证所有下游自定义 Protocol 实现。不要只回滚一个 Repo 文件。
- 失败 CAS 的后续读看到更晚状态，不是 UPDATE 时刻的失败快照；禁止用无条件写重试。
- 没有连接真实 MOSN/ZDAS、运行完整 Singlebox 部署栈或执行线上操作。
  MySQL 异常原始格式和实际吞吐改善仍需真实环境验证。
- 已消除确认的嵌套 checkout 结构、DEBUG 主动 checkout 和部分持连接计算；
  **未证明 slow SQL 或双 pool 哪个是线上池满的唯一根因，也未量化线上缓解幅度。**
- 上线对比 checkout 等待、连接持有时间、SQL/锁等待、峰值连接数，以及
  worker×pool 总配额和请求并发，再判断瓶颈与实际收益。
