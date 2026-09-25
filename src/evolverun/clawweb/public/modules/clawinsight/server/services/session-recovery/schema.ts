import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type { Dialect } from "@avernet/clawweb-shared/server/db/dialect";

/** Managed DBs are provisioned externally. No production/request-time DDL. */
export function renderRecoveryDeliveryDdl(dialect: Dialect): string {
  const ddl = `CREATE TABLE IF NOT EXISTS insight_session_recovery_delivery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id VARBINARY(512) NOT NULL,
    delivery_key VARBINARY(128) NOT NULL,
    request_fingerprint VARCHAR(64) NOT NULL,
    started_at_ms BIGINT NOT NULL,
    settle_after_ms BIGINT NOT NULL,
    outcome_json TEXT DEFAULT NULL,
    callback_delivered BIGINT NOT NULL DEFAULT 0,
    UNIQUE INDEX uk_recovery_delivery (event_id, delivery_key)
  )`;
  // Byte-exact keys (including case/trailing spaces) on both database families.
  if (dialect.name === "sqlite") return dialect.renderDdl(ddl.replace(/VARBINARY\(\d+\)/g, "TEXT COLLATE BINARY"));
  return `${dialect.renderDdl(ddl)} DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci`;
}
export async function initializeRecoveryDeliverySqlite(db: IDatabase): Promise<void> {
  if (db.dbType !== "sqlite") throw new Error("Recovery local initialization requires SQLite");
  await db.exec(renderRecoveryDeliveryDdl(db.dialect));
}
