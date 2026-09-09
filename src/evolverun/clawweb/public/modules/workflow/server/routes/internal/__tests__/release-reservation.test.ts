// @vitest-environment node
import { it, expect } from "vitest";
import Database from "better-sqlite3";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { sqliteDialect } from "@avernet/clawweb-shared/server/db/dialect";
import { migrations } from "@avernet/clawweb-shared/server/schema";
import { WorkflowDeployHistoryRepository } from "../../../repositories/workflow-deploy-history-repository.js";

it("reserves unique numbers, hides pending releases, and retries without duplicate rows or activation regression", async () => {
  const raw = new Database(":memory:");
  raw.exec(`CREATE TABLE workflow_specs (workflow_id TEXT PRIMARY KEY, version INTEGER);
    INSERT INTO workflow_specs VALUES ('story-telling', 2);
    CREATE TABLE workflow_deploy_history (
      id INTEGER PRIMARY KEY, pack_id TEXT, workflow_id TEXT, deploy_number INTEGER, version INTEGER,
      tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT,
      owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER,
      UNIQUE(pack_id, deploy_number, workflow_id));`);
  for (const sql of migrations.find(m => m.description === "Reserve workflow releases by saved Git commit")?.sql ?? []) raw.exec(sql);
  const db: IDatabase = {
    dbType: "sqlite", dialect: sqliteDialect,
    query: async <T>(sql: string, params: unknown[] = []) => raw.prepare(sql).all(...params) as T[],
    exec: async (sql, params = []) => ({ affectedRows: raw.prepare(sql).run(...params).changes }),
    transaction: async fn => {
      raw.exec("BEGIN");
      try { const value = await fn(db); raw.exec("COMMIT"); return value; }
      catch (e) { raw.exec("ROLLBACK"); throw e; }
    },
    close: async () => raw.close(),
  };
  const repo = new WorkflowDeployHistoryRepository(db);
  const input = { packId: "story-telling", workflowId: "story-telling", snapshotCommit: "a".repeat(40), specJson: '{"id":"story-telling"}', minDeployNumber: 1 };
  try {
    await repo.insert({ ...input, deployNumber: 9, version: 2, action: "edit", tagName: "", isActive: true });
    const other = { ...input, snapshotCommit: "b".repeat(40) };
    const [a, b] = await Promise.all([repo.reserveRelease(input), repo.reserveRelease(other)]);
    expect(a).toMatchObject({ deployNumber: 10, version: 3, tagName: "deploy/story-telling/#10", completed: false });
    expect(b.deployNumber).toBe(11);
    expect(await repo.reserveRelease(input)).toEqual(a);
    expect(await repo.findByVersionDeployOrEdit(input.workflowId, 3)).toBeNull();
    expect(await repo.findByDeployNumber(input.packId, input.workflowId, a.deployNumber)).toBeNull();
    expect(await repo.findByWorkflowAndDeployNumber(input.workflowId, a.deployNumber)).toBeNull();
    expect(await repo.setActive(input.workflowId, 3)).toBe(false);
    expect(await repo.listHistory(input.workflowId, 20)).toHaveLength(1);
    await expect(repo.completeRelease({ ...input, ...a, tagName: b.tagName })).rejects.toThrow(/mismatch/);
    await repo.completeRelease({ ...other, ...b });
    expect(await repo.completeRelease({ ...input, ...a })).toMatchObject({ completed: true });
    expect(await repo.completeRelease({ ...input, ...a })).toMatchObject({ completed: true });
    expect(await repo.reserveRelease(input)).toMatchObject({ ...a, completed: true });
    expect((await repo.findActiveByWorkflowId(input.workflowId))?.version).toBe(b.version);
    expect(raw.prepare("SELECT COUNT(*) AS n FROM workflow_deploy_history").get()).toEqual({ n: 3 });
    await expect(repo.reserveRelease({ ...input, specJson: '{"id":"changed"}' })).rejects.toThrow(/mismatch/);
  } finally { raw.close(); }
});
