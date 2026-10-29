# Spec: Knowledge publication review fixes

## Problem / Approved Scope
2026-10-28 用户批准修复 PR #3177 复审的五项 P2：配置错误被生命周期发现吞掉、旧状态入口绕过冻结/成功观测、artifact 与快照容量冲突、同名 Bot 锁定扫描、恢复扫描禁用绑定。
基线为 PR Head `7e5454c5d8c50db378566b735e686ab23974bc1a`。本次明确纳入旧状态入口，不沿用上一版 spec 的排除项。

## Acceptance
1. 非布尔 flag / 非 mapping 配置在所有 profile 的容器构造阶段失败；合法默认关闭保留原发布 handler，不增加外部启动调用。
2. 开启集成时，ONLINE_PUB -> SUCCESS 必须由传递 observed_workflow_id 的观测路径触发；通用状态调用不得更改线上知识或 native 成功状态。关闭时兼容原行为。
3. 所有允许的 DRAFT -> BUILDING 状态写入口统一冻结身份/选择/代次，并保留原 native status/ext CAS。合法 retry 不引入新的发布流程。
4. 有效的 <=100 个、每个 <=128 字符（含 UTF-8）KB 选择与阈值以下 artifact 可共同持久化；不因可 offload 的 artifact 把有效快照变为 UNKNOWN。
5. OSS 上传不进入新增锁；上传失败无 BUILDING 状态/半快照，上传期间状态/ext/工作选择变更不可被覆盖；删除竞态沿用 native prefix cleanup。
6. 已知 source PK 按 PK 锁定，保持租户/env/不可变身份复验、共享 Bot 锁和 SQLite writer reservation。
7. 恢复按当前启用索引与 <=100 个精确 KB key 分开查询；禁用历史与同名 Bot 从 1/1000/10000 增长时，受影响路径本地工作量保持有界。
8. 不新增表/字段/索引/Plugin/worker；不改变双选择模型、回滚/恢复 ledger、UNKNOWN 策略、权限或生产开关。不合并/部署/执行 DDL。

## Verification Seams
复用本次评审已使用的真实 config/DI/queue、HTTP handler -> Service -> Repository、publishing repository/SQLite 和 OSS 外部边界；性能用 SQLite VM/EXPLAIN，不冒充生产验收。

## Approved Enablement Amendment (2026-10-28)
用户批准：通过 ODC CLI 确认指定库结构就绪后，将本 PR 随包配置改为 enabled: true；不回填历史、不修改历史语义、不部署或执行 DDL。此项取代上文“生产开关不变”的原范围限制。显式 false 及缺失配置的兼容 fallback 保留。真实随包配置必须解析为布尔 true，并在正常 DI 中保留发布生命周期处理器。

## Failed Online Restart Retry Amendment
At repair base c3f841bcd, native failed-online-restart retry must work with/without knowledge and for recreated targets. Restricted recovery must match current FAILED, exact ext, source_status=success, environment and latest failed operation. It adds retry and restores native SUCCESS without mutating snapshots/results/summary/bindings. Generic status/completion gates remain; no HTTP bypass parameter. DB/CAS/ledger failures must precede new restart issuance. No DDL or production operations.

## Approved legacy-Owner draft-restore repair (2026-10-29)
Native source resolution treats a live source_bot_pk plus bot_id as authoritative even when old publication owner_id does not equal the current immutable entity_id. Knowledge integration must not turn that supported native restore into failure after device success. Split native source locking from knowledge identity trust: keep PK/bot_id/env/tenant/service/nondeleted scope, writer reservation and refresh; keep strict entity equality for build/online completion. During restore, an entity mismatch on either draft or previous publication produces knowledge UNKNOWN, preserves working/online flags and revisions, and atomically completes the native ledger. Latest draft/source version/operation/attempt/deadline and replay guards remain mandatory. No fallback, history backfill, schema, startup, config or deployment change.
Rejected: swallowing every source mismatch (loses isolation), or globally removing entity checks (would trust foreign snapshots). Verification uses the already approved native Flow/Service/Repository/operation seam, real PK Bot lookup and external device boundary doubles, plus negative identity/ledger cases.
