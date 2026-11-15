import type { IDatabase } from './db.js';

export type DbType = "sqlite" | "mysql" | "zdas";

export type Migration = {
  version: number;
  description: string;
  sql: string[];
  sqliteOnly?: boolean;
  mysqlOnly?: boolean;
  /**
   * Run even when `schema_version` reports the migration as already applied,
   * and even when the progress table is unavailable (managed MySQL denies DDL,
   * so the table stays empty and every version looks "already applied").
   * The migration must therefore be idempotent. Reserved for repairing a
   * physical schema that drifted from the declared one.
   */
  selfHealing?: boolean;
  migrate?: (db: IDatabase) => Promise<void>;
};
