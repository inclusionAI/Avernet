# ETL: 好友关系 → 边权限 全量迁移

## 概述

把 System A（`ac_bot_friend`, 人→Bot）+ System B（`bcs_friendships`/`bcs_friend_requests`, Bot↔Bot）的历史好友数据迁移到 08-12 边权限表（`edge_grants`/`permission_profiles`/`permission_requests`）。`ac_bot_friend` 和 `bcs_friend_requests` 按关系维度取 latest-status 后迁移，避免历史流水中的旧状态污染新事实源。

## 前置条件

1. Phase 1 Build 已部署：五表 DDL（`011_edge_permission.sql`）已 apply + ensure 端点已上线。
2. Phase 0 补录完成：所有 `ac_bots` 中的 bot 都已在 BCS 注册（`scripts/phase0_backfill_missing_bots.py`）。
3. 备份：对 `ac_bot_friend`、`bcs_friendships`、`bcs_friend_requests`、`bcs_actor_relations` 做快照。
4. 明确 System B SoR：好友关系以 `bcs_friendships` 为准；`bcs_actor_relations` 中 `is_creator=0` 的 bot↔bot 关系只作为镜像缓存，不单独迁移。脚本会输出镜像 drift 检查，非 0 时必须先人工修复或补充迁移来源。

## 执行步骤

### Phase 0: 补录缺失 bot

```bash
BCS_BASE_URL=http://localhost:21000 \
BCS_SERVICE_KEY=<admin-key> \
DB_HOST=<mysql> DB_USER=<user> DB_PASSWORD=<pw> DB_NAME=<db> \
python3 scripts/phase0_backfill_missing_bots.py --env prod --dry-run   # 先看有多少缺失
python3 scripts/phase0_backfill_missing_bots.py --env prod              # 执行补录
```

### Phase 2: 全量 ETL

```sql
-- 替换 <your_database> 为目标数据库名
-- 按 env 分批；所有 INSERT IGNORE 幂等可重跑
USE <your_database>;
SOURCE src/bcs/migrations/etl/full_etl_friend_migration.sql;
```

脚本顺序（在同一文件中）：
0. default profile 批量 seed（每 bot 一条 wildcard-allow）
1. bot_uuid 映射视图 `v_ac_bot_map` + latest-status 视图 `v_ac_bot_friend_latest` / `v_bcs_friend_requests_latest`
2. System A latest ACCEPTED → 人→Bot 边 (1 条) + approved request
3. System A latest PENDING/REJECTED/CANCELLED → permission_requests (无 edge)
4. System B `bcs_friendships` → Bot↔Bot 双向 2 边 + 2 approved requests
5. System B latest `bcs_friend_requests` pending/rejected → permission_requests

### 对账

文件末尾的 reconciliation 查询验证，输出字段尽量统一为 `check_name/env/old_count/new_count/diff/result` 或 `check_name/env/*_count/result`：
- `bcs_actor_relations` 镜像 drift 必须为 0
- System A bot 映射 orphan 必须为 0
- System B friendship orphan bot 必须为 0
- 人→Bot 边数 = `ac_bot_friend` latest ACCEPTED 数（按 env）
- Bot↔Bot 边数 = `bcs_friendships` × 2（按 env）
- default profile 覆盖 = `bcs_bots` 数（按 env）
- System B latest accepted request 必须存在对应 `bcs_friendships`
- System B latest 非 accepted request 不应仍存在 `bcs_friendships`
- approved request 必须有对应 approved edge
- approved edge 必须有对应 approved request
- 非 approved request 不应挂 approved edge

### 重放

所有 INSERT 用 `INSERT IGNORE` + 自然键/临时映射表 → 可重跑。第二次跑 `affected_rows=0`，计数不变。

## 后续

- Phase 3 增量对账：手动重跑全量 ETL 拾取新增，再执行 `incremental_reconciliation.sql` 做 latest-status 双向 reconciliation（UPDATE + REVOKE）。
- Phase 4 shadow（Installment 5）：新读 vs 旧读并行比对。
- Phase 5 cutover + 退役（Installment 6）：翻转读写到 BCS → 旧冻结 → drop 旧表。
