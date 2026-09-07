import { expect, it } from "vitest";
import Database from "better-sqlite3";
import { migrations } from "../schema.js";

it("upgrades existing retry rows and adds the scoped run index", () => {
  const db = new Database(":memory:");
  try {
    db.exec("CREATE TABLE flow_retry_requests (request_id TEXT, flow_id TEXT); INSERT INTO flow_retry_requests VALUES ('old-request', 'old-flow'); CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, origin_bot_id TEXT)");
    const migration = migrations.find(item => item.version === 119);
    expect(migration).toBeDefined();
    for (const sql of migration!.sql) db.exec(sql);
    expect(db.prepare("SELECT * FROM flow_retry_requests").get()).toEqual({request_id:"old-request",flow_id:"old-flow",options_json:null});
    expect(db.prepare("PRAGMA index_info(idx_flow_runs_origin_id)").all().map(row => (row as {name:string}).name)).toEqual(["origin_bot_id", "id"]);
  } finally { db.close(); }
});
