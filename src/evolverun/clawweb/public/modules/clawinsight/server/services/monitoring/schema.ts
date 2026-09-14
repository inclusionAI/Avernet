import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type { Dialect } from "@avernet/clawweb-shared/server/db/dialect";

/** Monitoring-owned DDL. Managed databases are provisioned externally, never by the router. */
export const monitoringDdl = [
  `CREATE TABLE IF NOT EXISTS insight_monitoring_diagnoses (
  id INTEGER PRIMARY KEY AUTOINCREMENT COMMENT '内部自增主键，不作为页面记录编号',
  event_id VARCHAR(128) CHARACTER SET latin1 COLLATE latin1_bin NOT NULL COMMENT '稳定上报编号，等于公共接口diagnosisId',
  schema_version VARCHAR(64) NOT NULL COMMENT '上报格式版本',
  bot_id VARCHAR(128) CHARACTER SET latin1 COLLATE latin1_bin NOT NULL COMMENT '固定监控Bot的业务ID',
  engine VARCHAR(2) NOT NULL COMMENT '引擎类型：OC或TE',
  session_key VARCHAR(1024) DEFAULT NULL COMMENT '上游会话键，无可靠值时为空',
  session_id VARCHAR(255) DEFAULT NULL COMMENT '上游真实Session ID',
  trace_id VARCHAR(255) DEFAULT NULL COMMENT 'AI Vision Trace ID，TE上报必填',
  occurred_at_ms BIGINT DEFAULT NULL COMMENT '会话时间，UTC Unix毫秒，无可靠值时为空',
  diagnosed_at_ms BIGINT NOT NULL COMMENT '最终诊断完成时间，UTC Unix毫秒',
  decision VARCHAR(16) NOT NULL COMMENT '诊断结论：ALERT、PASS或UNRESOLVED',
  tc_fault_label VARCHAR(128) DEFAULT NULL COMMENT '主要TC故障标签',
  confidence_json VARCHAR(32) DEFAULT NULL COMMENT '置信度规范JSON数字文本，接口转为number或null',
  business_problem_category VARCHAR(128) DEFAULT NULL COMMENT '业务问题大类',
  business_problem_subtype VARCHAR(128) DEFAULT NULL COMMENT '业务问题子类',
  system_diagnosis TEXT COMMENT '脱敏系统诊断文本，接口最多8000码点',
  business_diagnosis TEXT COMMENT '脱敏业务诊断文本，接口最多8000码点',
  handler_name VARCHAR(128) DEFAULT NULL COMMENT '可选处理人归属信息，不代表已处理',
  human_intervention BIGINT NOT NULL COMMENT '当前输入是否人工追问重试纠正前序任务：0否1是',
  received_at_ms BIGINT NOT NULL COMMENT '服务端首次接收时间，UTC Unix毫秒',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '数据库行创建时间',
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '数据库行最后修改时间',
  UNIQUE INDEX uk_monitor_diag_event (event_id),
  INDEX idx_monitor_diag_bot_time (bot_id, occurred_at_ms, event_id),
  INDEX idx_monitor_diag_bot_dec_time (bot_id, decision, occurred_at_ms, event_id)
) COMMENT='Agent监控诊断记录，不保存会话原文和通知状态'`,
  `CREATE TABLE IF NOT EXISTS insight_monitoring_bot_checks (
  id INTEGER PRIMARY KEY AUTOINCREMENT COMMENT '内部自增主键',
  bot_id VARCHAR(128) CHARACTER SET latin1 COLLATE latin1_bin NOT NULL COMMENT '固定监控Bot的业务ID，每Bot仅一行',
  engine VARCHAR(2) NOT NULL COMMENT '引擎类型：OC或TE',
  checked_at_ms BIGINT NOT NULL COMMENT '本次检查时间，UTC Unix毫秒，用于新旧判断',
  last_successful_check_at_ms BIGINT DEFAULT NULL COMMENT '最近成功检查时间，UTC Unix毫秒',
  status VARCHAR(16) NOT NULL COMMENT '检查状态：HEALTHY、ERROR、UNKNOWN或PAUSED',
  received_at_ms BIGINT NOT NULL COMMENT '最新有效检查上报的服务端接收时间，UTC Unix毫秒',
  gmt_create INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '数据库行创建时间',
  gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()) COMMENT '数据库行最后修改时间',
  UNIQUE INDEX uk_monitor_check_bot (bot_id)
) COMMENT='Agent监控每Bot最新检查状态，不保存心跳历史'`
];

/** Adapt only this module's fixed DDL; the shared dialect keeps its legacy policy. */
export function renderMonitoringDdl(dialect: Dialect): string[] {
  return monitoringDdl.map((ddl) => {
    if (dialect.name === "sqlite") {
      return dialect.renderDdl(ddl.replace(/CHARACTER SET latin1 COLLATE latin1_bin/g, "COLLATE BINARY"));
    }
    if (dialect.name !== "mysql" && dialect.name !== "zdas") throw new Error("Unsupported monitoring DDL dialect");
    // These non-indexed locators must retain the protocol's 255-codepoint capacity.
    const rendered = dialect.renderDdl(ddl.replace(/VARCHAR\(255\)/g, "MONITORING_LOCATOR_255"))
      .replace(/MONITORING_LOCATOR_255/g, "VARCHAR(255)");
    return `${rendered} DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci`;
  });
}

/** Explicit local/test setup only. Never called by production runtime or request handlers. */
export async function initializeMonitoringSqlite(db: IDatabase): Promise<void> {
  if (db.dbType !== "sqlite") throw new Error("Monitoring local initialization requires SQLite");
  for (const ddl of renderMonitoringDdl(db.dialect)) await db.exec(ddl);
  for (const table of ["insight_monitoring_diagnoses", "insight_monitoring_bot_checks"]) {
    await db.exec(`CREATE TRIGGER IF NOT EXISTS trg_${table}_update AFTER UPDATE ON ${table}
      FOR EACH ROW BEGIN UPDATE ${table} SET gmt_modified = (unixepoch()) WHERE id = NEW.id; END`);
  }
}
