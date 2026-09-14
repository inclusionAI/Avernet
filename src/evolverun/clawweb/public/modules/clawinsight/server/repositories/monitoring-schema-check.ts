import type { IDatabase, Row } from "@avernet/clawweb-shared/server/db";

export const DIAGNOSES_TABLE = "insight_monitoring_diagnoses";
export const CHECKS_TABLE = "insight_monitoring_bot_checks";
const diagnosisColumns: Record<string, [string, boolean]> = {
  id: ["BIGINT", false], event_id: ["VARCHAR(128)", false], schema_version: ["VARCHAR(64)", false],
  bot_id: ["VARCHAR(128)", false], engine: ["VARCHAR(2)", false], session_key: ["VARCHAR(1024)", true],
  session_id: ["VARCHAR(255)", true], trace_id: ["VARCHAR(255)", true], occurred_at_ms: ["BIGINT", true],
  diagnosed_at_ms: ["BIGINT", false], decision: ["VARCHAR(16)", false], tc_fault_label: ["VARCHAR(128)", true],
  confidence_json: ["VARCHAR(32)", true], business_problem_category: ["VARCHAR(128)", true],
  business_problem_subtype: ["VARCHAR(128)", true], system_diagnosis: ["TEXT", true], business_diagnosis: ["TEXT", true],
  handler_name: ["VARCHAR(128)", true], human_intervention: ["BIGINT", false], received_at_ms: ["BIGINT", false],
  gmt_create: ["TIMESTAMP", false], gmt_modified: ["TIMESTAMP", false],
};
const checkColumns: Record<string, [string, boolean]> = {
  id: ["BIGINT", false], bot_id: ["VARCHAR(128)", false], engine: ["VARCHAR(2)", false],
  checked_at_ms: ["BIGINT", false], last_successful_check_at_ms: ["BIGINT", true], status: ["VARCHAR(16)", false],
  received_at_ms: ["BIGINT", false], gmt_create: ["TIMESTAMP", false], gmt_modified: ["TIMESTAMP", false],
};
function assert(condition: unknown): asserts condition {
  if (!condition) throw new Error("Monitoring schema is missing or incompatible");
}
/** Read-only validation. Never creates/changes tables or the shared migration version. */
export async function verifyMonitoringSchema(db: IDatabase): Promise<void> {
  assert(db.dbType !== "noop");
  for (const [table, expected, indexes] of [
    [DIAGNOSES_TABLE, diagnosisColumns, [[true, ["event_id"]], [false, ["bot_id", "occurred_at_ms", "event_id"]],
      [false, ["bot_id", "decision", "occurred_at_ms", "event_id"]]]],
    [CHECKS_TABLE, checkColumns, [[true, ["bot_id"]]]],
  ] as [string, Record<string, [string, boolean]>, [boolean, string[]][]][]) {
    const sqlite = db.dbType === "sqlite";
    const columns = sqlite ? await db.query(`PRAGMA table_info(${table})`) : await db.query(
      `SELECT COLUMN_NAME AS name, DATA_TYPE AS data_type, CHARACTER_MAXIMUM_LENGTH AS max_length,
       IS_NULLABLE AS nullable, COLLATION_NAME AS collation, COLUMN_KEY AS column_key, EXTRA AS extra
       FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?`, [table]);
    for (const [name, [type, nullable]] of Object.entries(expected)) {
      const column = columns.find((c) => c.name === name);
      assert(column);
      const expectedType = sqlite ? type.replace(/BIGINT|TIMESTAMP/g, "INTEGER") : type;
      const actualType = sqlite ? String(column.type).toUpperCase() : `${String(column.data_type).toUpperCase()}${column.max_length != null && type.startsWith("VARCHAR") ? `(${column.max_length})` : ""}`;
      assert(actualType === expectedType);
      assert((sqlite ? !(Number(column.notnull) || Number(column.pk)) : column.nullable === "YES") === nullable);
      if (name === "id") assert(sqlite ? Number(column.pk) === 1 : column.column_key === "PRI" && String(column.extra).includes("auto_increment"));
      if (!sqlite && ["event_id", "bot_id"].includes(name)) assert(column.collation === "latin1_bin");
    }
    // Required full indexes; prefix/partial indexes cannot enforce our identity or paging semantics.
    let actualIndexes: { unique: boolean; columns: string[]; binary: boolean; full: boolean }[] = [];
    if (sqlite) {
      const list = await db.query(`PRAGMA index_list(${table})`);
      for (const index of list) {
        const safeName = String(index.name).replaceAll("'", "''");
        const info = (await db.query(`PRAGMA index_xinfo('${safeName}')`)).filter((c) => Number(c.key) === 1);
        actualIndexes.push({ unique: !!index.unique, columns: info.map((c) => String(c.name)),
          binary: info.filter((c) => ["event_id", "bot_id"].includes(String(c.name))).every((c) => c.coll === "BINARY"), full: !index.partial });
      }
      // Index collation alone is insufficient: WHERE bot_id uses the column's own collation.
      const [ddl] = await db.query<{ sql: string }>("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", [table]);
      assert(ddl && !/\bCOLLATE\s+(?!BINARY\b)\w+/i.test(ddl.sql));
    } else {
      const list = await db.query(`SELECT INDEX_NAME AS name, NON_UNIQUE AS non_unique, COLUMN_NAME AS column_name,
        SEQ_IN_INDEX AS seq, SUB_PART AS sub_part FROM information_schema.STATISTICS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ? ORDER BY INDEX_NAME, SEQ_IN_INDEX`, [table]);
      const groups = new Map<string, Row[]>();
      for (const row of list) groups.set(String(row.name), [...(groups.get(String(row.name)) ?? []), row]);
      actualIndexes = [...groups.values()].map((rows) => ({ unique: Number(rows[0].non_unique) === 0,
        columns: rows.map((r) => String(r.column_name)), binary: true, full: rows.every((r) => r.sub_part == null) }));
    }
    for (const [unique, names] of indexes) {
      assert(actualIndexes.some((index) => index.unique === unique && index.binary && index.full
        && JSON.stringify(index.columns) === JSON.stringify(names)));
    }
    // Ensures SELECT privilege through the same service connection, not just metadata visibility.
    await db.query(`SELECT ${Object.keys(expected).join(", ")} FROM ${table} WHERE 1 = 0`);
  }
}
