import type { migrations } from "./schema.js";

/** Shared scan ownership only; business tasks and execution leases stay in their existing stores. */
export const repairSchedulerMigration: (typeof migrations)[number] = {
  version: 147,
  description: "Coordinate Repair and session recovery background scans across instances",
  sql: [`CREATE TABLE IF NOT EXISTS ce_repair_scheduler_leases (
  id INTEGER PRIMARY KEY AUTOINCREMENT COMMENT '主键ID',
  environment VARCHAR(8) NOT NULL COMMENT '控制面环境',
  job_name VARCHAR(48) NOT NULL COMMENT '后台作业名称',
  lease_owner VARCHAR(64) NOT NULL DEFAULT '' COMMENT '本轮领取身份',
  lease_expires_at BIGINT NOT NULL DEFAULT 0 COMMENT '调度租约到期秒数',
  next_run_at BIGINT NOT NULL DEFAULT 0 COMMENT '下一轮最早运行秒数',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '创建时间',
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '修改时间',
  UNIQUE INDEX uk_ce_repair_scheduler_job (environment, job_name)
) COMMENT='Repair与会话自愈后台扫描调度租约'`],
};
