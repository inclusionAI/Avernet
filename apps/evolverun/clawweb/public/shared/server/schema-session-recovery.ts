import type { IDatabase } from "./db.js";

/** Closed-loop task context contains immutable evidence references and timeline events. */
export const recoveryTaskContextMigration = {
  version: 141, description: "Allow full recovery task timeline and evidence references in existing Task extensions", sql: [],
  migrate: async (db: IDatabase) => {
    if (db.dbType !== "sqlite") await db.exec("ALTER TABLE ce_tasks MODIFY COLUMN config_json MEDIUMTEXT NOT NULL COMMENT '业务任务扩展与运行上下文'");
  },
};
