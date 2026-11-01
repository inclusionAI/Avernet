import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  config: {
    mode: "mysql" as "mysql" | "sqlite", sqlitePath: ":memory:",
    mysql: { host: "localhost", port: 3306, user: "test", password: "", database: "test", poolSize: 1, poolMin: 0 },
  },
  query: vi.fn(async (_sql: string, _params?: unknown[]): Promise<unknown[]> => { throw new Error("Unexpected query"); }),
  connection: vi.fn(async () => ({ release: vi.fn() })),
  close: vi.fn(async () => {}),
  repair: vi.fn(async () => {}),
}));
vi.mock("mysql2/promise", () => ({ default: {
  createPool: () => ({ query: state.query, getConnection: state.connection, end: state.close }),
} }));
vi.mock("../db/config.js", async original => ({
  ...await original<typeof import("../db/config.js")>(),
  resolveDbConfig: () => state.config,
}));
vi.mock("../schema.js", () => ({
  migrations: [
    { version: 1, description: "ordinary", sql: ["CREATE TABLE ordinary_probe (id INT)"] },
    { version: 2, description: "repair", selfHealing: true, migrate: state.repair, sql: [] },
  ],
  sqliteTriggers: [],
}));
vi.mock("../schema-workflow-snapshot.js", () => ({ workflowSnapshotMigrations: [] }));

import { closeDatabase, initDatabase } from "../db.js";
import { packOperationMigrations, renderPackOperationDdl } from "../schema-pack-operations.js";
import { dialectFor } from "../db/dialect.js";

const packColumns = [
  ["request_id", "varchar", 190, "NO"], ["workflow_id", "varchar", 256, "NO"],
  ["actor_id", "varchar", 256, "NO"], ["kind", "varchar", 16, "NO"],
  ["input_hash", "varchar", 128, "NO"], ["phase", "varchar", 16, "NO"],
  ["attempt", "bigint", null, "NO"],
  ["lock_workflow_id", "bigint", null, "YES"], ["baseline_json", "mediumtext", 16777215, "NO"],
  ["payload_json", "longtext", 4294967295, "YES"], ["result_json", "longtext", 4294967295, "YES"],
  ["error_json", "longtext", 4294967295, "YES"], ["created_at_ms", "bigint", null, "NO"],
  ["updated_at_ms", "bigint", null, "NO"],
].map(([column_name, data_type, max_length, is_nullable]) => ({ column_name, data_type, max_length, is_nullable }));
const packIndexes = [
  { index_name: "PRIMARY", column_name: "request_id", prefix_length: null },
  { index_name: "uk_clawweb_pack_operation_lock", column_name: "lock_workflow_id", prefix_length: null },
];
const packRead = "FROM clawweb_pack_operations WHERE 1 = 0";
const ledgerInsert = "INSERT INTO schema_version (version, description) VALUES (?, ?)";
const packDdl = renderPackOperationDdl({ dbType: "mysql", dialect: dialectFor("mysql") }, packOperationMigrations[0].sql[0]);

function schemaQuery(sql: string): unknown[] {
  if (sql === "SELECT MAX(version) as v FROM schema_version") return [[{ v: 0 }], []];
  if (sql.includes(packRead)) return [[], []];
  if (sql.includes("TABLE_NAME = 'clawweb_pack_operations'")) {
    if (sql.includes("information_schema.COLUMNS")) return [packColumns, []];
    if (sql.includes("information_schema.TABLES")) return [[{ engine: "InnoDB" }], []];
    if (sql.includes("information_schema.STATISTICS")) return [packIndexes, []];
  }
  if (/^CREATE TABLE IF NOT EXISTS schema_version\b/.test(sql) || sql === "CREATE TABLE ordinary_probe (id INT)"
    || sql === packDdl || sql === ledgerInsert) return [{ affectedRows: 1 }, []];
  throw new Error(`Unexpected SQL: ${sql}`);
}

beforeEach(() => {
  vi.clearAllMocks();
  state.config.mode = "mysql";
  state.connection.mockResolvedValue({ release: vi.fn() });
  state.query.mockReset();
  state.query.mockImplementation(async sql => schemaQuery(sql));
});
afterEach(async () => { await closeDatabase(); });

describe("database initialization strategy", () => {
  it("keeps ordinary migrations, repairs and ledger writes as the default", async () => {
    const { db } = await initDatabase();
    expect(db.dbType).toBe("mysql");
    const sql = state.query.mock.calls.map(([sql]) => sql);
    expect(sql).toContain("CREATE TABLE ordinary_probe (id INT)");
    expect(sql).toContain("SELECT MAX(version) as v FROM schema_version");
    expect(state.query.mock.calls.filter(([query]) => query === ledgerInsert).map(([, params]) => params)).toEqual([
      [1, "ordinary"], [2, "repair"], [149, packOperationMigrations[0].description],
    ]);
    expect(sql).toContain(packDdl);
    expect(sql.some(query => query.includes(packRead))).toBe(true);
    expect(state.repair).toHaveBeenCalledOnce();
  });

  it.each(["create", "read", "schema"] as const)("falls back without recording v149 when pack %s fails", async failure => {
    state.query.mockImplementation(async sql => {
      if (failure === "create" && sql === packDdl) throw new Error("CREATE command denied");
      if (failure === "read" && sql.includes(packRead)) throw new Error("pack read denied");
      if (failure === "schema" && sql.includes("information_schema.COLUMNS") && sql.includes("TABLE_NAME = 'clawweb_pack_operations'")) {
        return [packColumns.slice(1), []];
      }
      return schemaQuery(sql);
    });
    expect((await initDatabase()).db.dbType).toBe("noop");
    expect(state.query.mock.calls.filter(([sql]) => sql === ledgerInsert).map(([, params]) => params)).toEqual([
      [1, "ordinary"], [2, "repair"],
    ]);
    expect(state.query.mock.calls.some(([sql]) => sql === packDdl)).toBe(true);
    expect(state.query.mock.calls.some(([sql]) => sql.includes(packRead))).toBe(failure !== "create");
  });

  it("only connects for externally provisioned tables, without any migration SQL or repair", async () => {
    const first = await initDatabase(undefined, { schema: "external" });
    expect(first.db.dbType).toBe("mysql");
    expect(await initDatabase(undefined, { schema: "external" })).toBe(first);
    expect(state.connection).toHaveBeenCalledOnce();
    expect(state.query).not.toHaveBeenCalled();
    expect(state.repair).not.toHaveBeenCalled();
  });

  it("keeps the default NoOp fallback on connection failure", async () => {
    state.connection.mockRejectedValueOnce(new Error("database unavailable"));
    expect((await initDatabase()).db.dbType).toBe("noop");
  });

  it("propagates external connection failure and permits a subsequent retry", async () => {
    state.connection.mockRejectedValueOnce(new Error("database unavailable"));
    await expect(initDatabase(undefined, { schema: "external" })).rejects.toThrow("database unavailable");
    expect((await initDatabase(undefined, { schema: "external" })).db.dbType).toBe("mysql");
  });

  it("keeps default migration failures on the existing NoOp path", async () => {
    state.query.mockRejectedValueOnce(new Error("migration failure"));
    expect((await initDatabase()).db.dbType).toBe("noop");
  });

  it.each(["migrate", "external"] as const)("does not silently reuse a cached %s strategy for the other mode", async schema => {
    await initDatabase(undefined, { schema });
    const other = schema === "migrate" ? "external" : "migrate";
    await expect(initDatabase(undefined, { schema: other })).rejects.toThrow("different schema strategy");
    await closeDatabase();
    expect((await initDatabase(undefined, { schema: other })).db.dbType).toBe("mysql");
  });

  it("rejects an unknown strategy before connecting", async () => {
    await expect(initDatabase(undefined, { schema: "invalid" as "external" })).rejects.toThrow("Unsupported");
    expect(state.connection).not.toHaveBeenCalled();
  });
});
