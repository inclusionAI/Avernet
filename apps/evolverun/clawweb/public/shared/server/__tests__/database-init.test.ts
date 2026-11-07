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
vi.mock("../schema-ordinary-run-request.js", () => ({ ordinaryRunRequestMigrations: [] }));

import { closeDatabase, initDatabase } from "../db.js";
const ledgerInsert = "INSERT INTO schema_version (version, description) VALUES (?, ?)";

function schemaQuery(sql: string): unknown[] {
  if (sql === "SELECT MAX(version) as v FROM schema_version") return [[{ v: 0 }], []];
  if (/^CREATE TABLE IF NOT EXISTS schema_version\b/.test(sql) || sql === "CREATE TABLE ordinary_probe (id INT)"
    || sql === ledgerInsert) return [{ affectedRows: 1 }, []];
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
      [1, "ordinary"], [2, "repair"],
    ]);
    expect(sql.some(query => /clawweb_pack_operations/i.test(query))).toBe(false);
    expect(state.repair).toHaveBeenCalledOnce();
  });

  it("keeps retired v149 untouched while still running self-healing repairs", async () => {
    state.query.mockImplementation(async sql => sql === "SELECT MAX(version) as v FROM schema_version"
      ? [[{ v: 149 }], []] : schemaQuery(sql));
    expect((await initDatabase()).db.dbType).toBe("mysql");
    expect(state.query.mock.calls.filter(([sql]) => sql === ledgerInsert).map(([, params]) => params)).toEqual([
      [2, "repair"],
    ]);
    expect(state.query.mock.calls.some(([sql]) => /clawweb_pack_operations/i.test(sql))).toBe(false);
    expect(state.repair).toHaveBeenCalledOnce();
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
