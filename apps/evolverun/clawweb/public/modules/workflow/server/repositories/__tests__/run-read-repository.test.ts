import express from "express";
import { createInternalRunReadsRouter } from "../../routes/internal/run-reads.js";
import { describe, it, expect } from "vitest";
import Database from "better-sqlite3";
import { RunReadRepository } from "../run-read-repository.js";
import { sqliteDialect } from "@avernet/clawweb-shared/server/db/dialect";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

function fixture() {
  const raw = new Database(":memory:");
  raw.exec(`CREATE TABLE flow_runs (id INTEGER PRIMARY KEY, flow_id TEXT, workflow_id TEXT, status TEXT, origin_bot_id TEXT, identity_key TEXT, started_at INTEGER, gmt_modified INTEGER, state_json TEXT);
  INSERT INTO flow_runs VALUES (1,'f1','wf','failed','bot:owner','identity',1,2,NULL),(2,'f2','wf','failed','bot:owner','identity',1,2,NULL),(3,'other','wf','failed','bot:other','identity',1,2,NULL),(4,'legacy','wf','failed',NULL,'identity',1,2,NULL);
  CREATE TABLE run_logs (id INTEGER PRIMARY KEY,flow_id TEXT,node_id TEXT,level TEXT,source TEXT,message TEXT,timestamp INTEGER,seq INTEGER);
  INSERT INTO run_logs VALUES (1,'f1','n','error','engine','first',100,1),(2,'f1','n','error','engine','second',100,1),(3,'f1','n','info','engine','info',100,2);`);
  raw.exec(`ALTER TABLE flow_runs ADD COLUMN origin_session_key TEXT;
  ALTER TABLE flow_runs ADD COLUMN origin_session_id TEXT;
  UPDATE flow_runs SET origin_session_key='session-a', origin_session_id='session-id-a' WHERE flow_id='f2';`);
  raw.exec(`CREATE TABLE bot_workflow_permissions (workflow_id TEXT, bot_id TEXT, bot_owner_id TEXT, can_view INTEGER, can_edit INTEGER);
  INSERT INTO bot_workflow_permissions VALUES ('wf', NULL, 'viewer', 1, 0);`);
  const db = { dbType: "sqlite", dialect: sqliteDialect, query: async (sql: string, args: any[] = []) => raw.prepare(sql).all(...args) } as unknown as IDatabase;
  return { raw, repo: new RunReadRepository(db), close: () => raw.close() };
}
const scope = { botId: "bot", ownerId: "owner" };
describe("scoped shared run reads", () => {
 it("paginates records across instances without leaking other owners or unowned legacy rows", async () => {
  const f = fixture(); try {
   const first = await f.repo.listRuns({ ...scope, limit: 1, status: "failed" });
   expect(first.items.map(r => r.flow_id)).toEqual(["f2"]);
   const second = await f.repo.listRuns({ ...scope, limit: 1, beforeId: first.nextCursor! });
   expect(second.items.map(r => r.flow_id)).toEqual(["f1"]); expect(second.nextCursor).toBeNull();
  } finally { f.close(); }
 });
 it("paginates duplicate-sequence logs by persistent id and applies filters", async () => {
  const f = fixture(); try {
   const q = {...scope, flowId: "f1", nodeId: "n", level: "error", limit: 1};
   const first = await f.repo.readLogs(q); const second = await f.repo.readLogs({...q, afterId: first.nextCursor!});
   expect(first.items[0].message).toBe("first"); expect(second.items[0].message).toBe("second"); expect(second.nextCursor).toBeNull();
   await expect(f.repo.readLogs({...q, ownerId: "other"})).rejects.toThrow(/not found/i);
  } finally { f.close(); }
 });
 it("refuses unscoped queries", async () => {
  const f=fixture(); try { await expect(f.repo.listRuns({botId:"bot",ownerId:""})).rejects.toThrow(/scope/i); } finally {f.close();}
 });
});

it("all scope filters by viewer permissions before cursor pagination and includes runs from other bots", async () => {
 const f = fixture();
 try {
  f.raw.exec("UPDATE flow_runs SET workflow_id='hidden' WHERE flow_id='legacy'");
  const first = await f.repo.listRuns({ ...scope, scope: "all", userId: "viewer", limit: 1 });
  expect(first.items.map(r => r.flow_id)).toEqual(["other"]);
  expect(first.nextCursor).toBe(3);
  const second = await f.repo.listRuns({ ...scope, scope: "all", userId: "viewer", limit: 1, beforeId: first.nextCursor! });
  expect(second.items.map(r => r.flow_id)).toEqual(["f2"]);
  expect((await f.repo.listRuns({ ...scope, scope: "all", userId: "no-access" })).items).toEqual([]);
  f.raw.exec("DELETE FROM bot_workflow_permissions");
  expect((await f.repo.listRuns({ ...scope, scope: "all", userId: "viewer" })).items).toEqual([]);
  await expect(f.repo.listRuns({ ...scope, scope: "all" })).rejects.toThrow(/userId/);
 } finally { f.close(); }
});

it("session scope filters before pagination and distinguishes reset sessions with the same key", async () => {
 const f = fixture();
 try {
  f.raw.exec("UPDATE flow_runs SET origin_session_key='session-a', origin_session_id='old' WHERE flow_id='f1'");
  const q = { ...scope, scope: "session" as const, sessionKey: "session-a", sessionId: "session-id-a", limit: 1 };
  const first = await f.repo.listRuns(q);
  expect(first.items.map(r => r.flow_id)).toEqual(["f2"]); expect(first.nextCursor).toBeNull();
  f.raw.exec("UPDATE flow_runs SET origin_session_id=NULL WHERE flow_id='f1'");
  const withLegacy = await f.repo.listRuns(q);
  expect(withLegacy.nextCursor).toBe(2);
  expect((await f.repo.listRuns({ ...q, beforeId: 2 })).items.map(r => r.flow_id)).toEqual(["f1"]);
  await expect(f.repo.listRuns({ ...scope, scope: "session" })).rejects.toThrow(/session/);
 } finally { f.close(); }
});

it("walks a large result set in bounded pages without duplicates when new runs arrive", async () => {
 const f = fixture();
 try {
  f.raw.exec("DELETE FROM flow_runs");
  const insert = f.raw.prepare("INSERT INTO flow_runs (id,flow_id,workflow_id,status,origin_bot_id) VALUES (?,?,'wf','failed','bot:owner')");
  for (let id=1; id<=105; id++) insert.run(id, `run-${id}`);
  let page = await f.repo.listRuns({...scope,limit:20});
  const seen = page.items.map(r => r.id);
  insert.run(106, "new-arrival");
  while (page.nextCursor !== null) {
   page = await f.repo.listRuns({...scope,limit:20,beforeId:page.nextCursor});
   expect(page.items.length).toBeLessThanOrEqual(20);
   seen.push(...page.items.map(r => r.id));
  }
  expect(seen).toEqual(Array.from({length:105},(_,i)=>105-i));
  await expect(f.repo.listRuns({...scope,limit:51})).rejects.toThrow(/limit/);
 } finally { f.close(); }
});


it("HTTP read routes enforce scope, validate filters, and return pagination data", async () => {
 const f=fixture(); const app=express(); app.use(express.json()); app.use(createInternalRunReadsRouter(f.repo));
 const server = app.listen(0, "127.0.0.1");
 await new Promise<void>(resolve => server.once("listening", resolve));
 const url = `http://127.0.0.1:${(server.address() as import("node:net").AddressInfo).port}`;
 const request = (_app: unknown) => ({post:(path:string)=>({send:async(body:unknown)=>{
   const response=await fetch(url+path,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(body)});
   return {status:response.status,body:await response.json()};
 }})});
 try {
  const first=await request(app).post("/runs").send({...scope,limit:1});
  expect(first.status).toBe(200);expect(first.body.data.items[0].flow_id).toBe("f2");
  expect(first.body.data.items[0].origin_session_key).toBe("session-a");
  expect(first.body.data.items[0].origin_session_id).toBe("session-id-a");
  expect(first.body.data.scope).toBe("bot");
  const all=await request(app).post("/runs").send({...scope,scope:"all",userId:"viewer",limit:1});
  expect(all.status).toBe(200); expect(all.body.data.scope).toBe("all");
  expect(all.body.data.items[0].flow_id).toBe("legacy");
  const session=await request(app).post("/runs").send({...scope,scope:"session",sessionKey:"session-a",limit:1});
  expect(session.status).toBe(200); expect(session.body.data.scope).toBe("session");
  expect(session.body.data.items.map((r:any)=>r.flow_id)).toEqual(["f2"]);
  for (const invalid of [{scope:"invalid"},{scope:"all"},{scope:"session"},{scope:"all",userId:123}]) {
    expect((await request(app).post("/runs").send({...scope,...invalid})).status).toBe(400);
  }
  expect((await request(app).post("/runs").send({botId:"bot"})).status).toBe(400);
  expect((await request(app).post("/runs").send({...scope,beforeId:"2"})).status).toBe(400);
  expect((await request(app).post("/logs").send({...scope,flowId:"other"})).status).toBe(404);
  const logs=await request(app).post("/logs").send({...scope,flowId:"f1",nodeId:"n",level:"error",limit:1});
  expect(logs.status).toBe(200);expect(logs.body.data.items[0].message).toBe("first");expect(logs.body.data.nextCursor).toBe(1);
 } finally {server.closeAllConnections(); await new Promise<void>((resolve,reject)=>server.close(e=>e?reject(e):resolve())); f.close();}
});


it("shared lists respect hidden records and explicit includeHidden", async () => {
 const f=fixture();
 try {
  f.raw.prepare("UPDATE flow_runs SET state_json=? WHERE flow_id='f2'").run(JSON.stringify({workflowData:{flowHidden:true}}));
  expect((await f.repo.listRuns(scope)).items.map(r=>r.flow_id)).toEqual(["f1"]);
  expect((await f.repo.listRuns({...scope,includeHidden:true})).items.map(r=>r.flow_id)).toEqual(["f2","f1"]);
 } finally {f.close();}
});


it("all scope applies per-Bot workflow grants before pagination without treating Bot IDs as patterns", async () => {
 const f = fixture();
 try {
  f.raw.exec("UPDATE bot_workflow_permissions SET bot_id='bot_%'");
  f.raw.exec("UPDATE flow_runs SET origin_bot_id='bot_%:owner' WHERE flow_id IN ('f1','f2')");
  f.raw.exec("UPDATE flow_runs SET origin_bot_id='bot_X:other' WHERE flow_id='other'");
  const q = {...scope, scope:"all" as const, userId:"viewer", limit:1};
  const first = await f.repo.listRuns(q);
  expect(first.items.map(r=>r.flow_id)).toEqual(["f2"]);
  expect(first.nextCursor).toBe(2);
  const second = await f.repo.listRuns({...q,beforeId:first.nextCursor!});
  expect(second.items.map(r=>r.flow_id)).toEqual(["f1"]);
  expect(second.nextCursor).toBeNull();
 } finally { f.close(); }
});
