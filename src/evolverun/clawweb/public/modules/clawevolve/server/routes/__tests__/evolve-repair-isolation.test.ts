import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import type Database from "better-sqlite3";
import { DatabaseSync } from 'node:sqlite';
import '../../test/setup.js';
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { EVOLVE_TASK_REGISTRY } from "../../services/evolve/task-registry.js";
import { createEvolveRouter } from "../evolve.js";

let db: SqliteDatabase;
let repo: EvolveRepository;
let server: ReturnType<express.Application["listen"]> | null;
let baseUrl: string;

async function seedTask(taskId: string, taskType: string, createdBy: string): Promise<void> {
  await repo.createTask({
    taskId,
    taskType,
    userId: createdBy,
    botId: `bot-${createdBy}`,
    taskName: taskId,
    configJson: "{}",
    createdBy,
  });
}

beforeEach(async () => {
  db = new SqliteDatabase(new DatabaseSync(":memory:") as unknown as Database.Database);
  await runMigrations(db, "sqlite");
  repo = new EvolveRepository(db);

  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => {
    req.isClawEvolveAdmin = req.header("X-Test-Evolve-Admin") === "true";
    next();
  });
  app.use("/api/evolve", createEvolveRouter(repo, {
    artifactUrlStore: { createSignedUrl: vi.fn(async () => "https://oss.example/signed") },
  }));
  const started = await new Promise<ReturnType<express.Application["listen"]>>((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  server = started;
  baseUrl = `http://127.0.0.1:${(started.address() as { port: number }).port}`;
});

afterEach(async () => {
  const active = server;
  server = null;
  if (active) await new Promise<void>((resolve) => active.close(() => resolve()));
  await db.close();
});

describe("generic Evolve Repair isolation", () => {
  it('excludes workflow repair from generic list rows and counts, even for the administrator', async () => {
    await seedTask('WORKFLOW-PRIVATE', 'workflow_repair', 'owner');
    await seedTask('LEGACY-PUBLIC', 'diagnose', 'owner');
    for (const query of ['scope=mine&category=all', 'scope=all&category=all', 'scope=all&category=not-a-category']) {
      const response = await fetch(`${baseUrl}/api/evolve/tasks?${query}`, { headers: { 'X-User-Id': 'owner', 'X-Test-Evolve-Admin': 'true' } });
      const body = await response.json() as { tasks: Array<{ task_id: string }>; total: number };
      expect(response.status).toBe(200);
      expect(body.tasks.map(task => task.task_id)).toEqual(['LEGACY-PUBLIC']);
      expect(body.total).toBe(1);
    }
  });
  it('rejects all generic workflow repair entrypoints without changing task or step rows', async () => {
    await seedTask('WORKFLOW-PRIVATE', 'workflow_repair', 'owner');
    await db.exec(`INSERT INTO ce_steps (step_id, task_id, step_type, step_no, command, status)
      VALUES ('WORKFLOW-STEP', 'WORKFLOW-PRIVATE', 'workflow_repair_draft', 1, '', 'running')`, []);
    const tasksBefore = await db.query('SELECT * FROM ce_tasks');
    const stepsBefore = await db.query('SELECT * FROM ce_steps');
    for (const [method, path] of [
      ['GET', '/tasks/WORKFLOW-PRIVATE'],
      ['POST', '/tasks/WORKFLOW-PRIVATE/steps/WORKFLOW-STEP/retry'],
      ['POST', '/tasks/WORKFLOW-PRIVATE/steps/WORKFLOW-STEP/cancel'],
      ['GET', '/internal/tasks/WORKFLOW-PRIVATE/steps/WORKFLOW-STEP/input'],
      ['POST', '/internal/tasks/WORKFLOW-PRIVATE/steps/WORKFLOW-STEP/report'],
    ]) {
      const response = await fetch(`${baseUrl}/api/evolve${path}`, { method, headers: { 'Content-Type': 'application/json', 'X-User-Id': 'owner' },
        ...(method === 'POST' ? { body: JSON.stringify({ status: 'succeeded', summary: 'attempted bypass' }) } : {}) });
      expect(response.status, `${method} ${path}`).toBe(404);
    }
    expect(await db.query('SELECT * FROM ce_tasks')).toEqual(tasksBefore);
    expect(await db.query('SELECT * FROM ce_steps')).toEqual(stepsBefore);
  });
  it("uses the same request user identity for Repair task lists", async () => {
    await seedTask("REPAIR-OWNER", "repair", "verified-owner");
    await seedTask("REPAIR-FORGED", "repair", "forged-user");

    const response = await fetch(`${baseUrl}/api/evolve/tasks?scope=mine&category=repair`, {
      headers: { "X-User-Id": "forged-user" },
    });
    const body = await response.json() as { tasks: Array<{ task_id: string }>; total: number };

    expect(response.status).toBe(200);
    expect(body.tasks.map(task => task.task_id)).toEqual(["REPAIR-FORGED"]);
    expect(body.total).toBe(1);
  });

  it("lets a ClawEvolve administrator list Repair globally and includes it in all categories", async () => {
    await seedTask("REPAIR-GLOBAL", "repair", "owner-1");
    await seedTask("EV-GLOBAL", "diagnose", "owner-2");
    const headers = { "X-User-Id": "admin", "X-Test-Evolve-Admin": "true" };

    const repair = await fetch(`${baseUrl}/api/evolve/tasks?scope=all&category=repair`, { headers });
    const repairBody = await repair.json() as { tasks: Array<{ task_id: string }>; total: number };
    expect(repair.status).toBe(200);
    expect(repairBody.tasks.map(task => task.task_id)).toEqual(["REPAIR-GLOBAL"]);
    expect(repairBody.total).toBe(1);

    for (const category of ["all", "not-a-category"]) {
      const response = await fetch(`${baseUrl}/api/evolve/tasks?scope=all&category=${category}`, { headers });
      const body = await response.json() as { tasks: Array<{ task_id: string }>; total: number };
      expect(response.status).toBe(200);
      expect(body.tasks.map(task => task.task_id)).toEqual(expect.arrayContaining(["EV-GLOBAL", "REPAIR-GLOBAL"]));
      expect(body.total).toBe(2);
    }
  });

  it("returns 404 for every generic detail, retry, cancel, input, and report entry", async () => {
    await seedTask("REPAIR-ISOLATED", "repair", "verified-owner");
    const requests: Array<[string, string]> = [
      ["GET", "/tasks/REPAIR-ISOLATED"],
      ["POST", "/tasks/REPAIR-ISOLATED/steps/STEP-1/retry"],
      ["POST", "/tasks/REPAIR-ISOLATED/steps/STEP-1/cancel"],
      ["GET", "/internal/tasks/REPAIR-ISOLATED/steps/STEP-1/input"],
      ["POST", "/internal/tasks/REPAIR-ISOLATED/steps/STEP-1/report"],
    ];

    for (const [method, path] of requests) {
      const response = await fetch(`${baseUrl}/api/evolve${path}`, {
        method,
        headers: { "Content-Type": "application/json", "X-User-Id": "verified-owner" },
        ...(method === "POST" ? { body: "{}" } : {}),
      });
      expect(response.status, `${method} ${path}`).toBe(404);
    }
  });

  it("does not advertise generic retry or cancel for Repair", () => {
    expect(EVOLVE_TASK_REGISTRY.repair).toMatchObject({
      supportsRetry: false,
      supportsCancel: false,
    });
  });
});
