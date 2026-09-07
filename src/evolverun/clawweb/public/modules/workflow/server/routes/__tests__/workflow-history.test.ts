/**
 * Tests for workflow version history read-only endpoints:
 *   GET /api/workflows/:workflowId/history
 *   GET /api/workflows/:workflowId/history/:version
 *   GET /api/workflows/:workflowId/history/diff?from=&to=
 *
 * workflow_deploy_history is a MySQL-only table (migration v64, mysqlOnly), so it is
 * not created by runMigrations() under SQLite. Instead of standing up the real table,
 * we inject a fake WorkflowDeployHistoryRepository that implements only the methods
 * the history routes touch. This isolates route-handler logic (ordering, status codes,
 * response shape) from the MySQL-only storage layer.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import http from "node:http";
import express from "express";
import type { WorkflowDeployHistoryRepository } from "../../repositories/workflow-deploy-history-repository.js";
import type { WorkflowDeployHistoryRow } from "../../repositories/workflow-deploy-history-repository.js";
import { createWorkflowsRouter } from "../workflows.js";

type HistoryRow = Omit<WorkflowDeployHistoryRow, "spec_json"> & { spec_json: string };

function makeFakeRepo(rows: HistoryRow[]) {
  return {
    async insert() { /* noop */ },
    async listHistory(workflowId: string, limit: number) {
      return rows
        .filter((r) => r.workflow_id === workflowId)
        .sort((a, b) => b.deploy_number - a.deploy_number)
        .slice(0, limit)
        .map((r) => {
          const { spec_json, ...rest } = r;
          void spec_json;
          return rest;
        });
    },
    async getLatestVersion(workflowId: string) {
      const vs = rows.filter((r) => r.workflow_id === workflowId).map((r) => r.version);
      return vs.length ? Math.max(...vs) : 0;
    },
    async getMaxDeployNumber(_packId: string, workflowId: string) {
      const ns = rows.filter((r) => r.workflow_id === workflowId).map((r) => r.deploy_number);
      return ns.length ? Math.max(...ns) : 0;
    },
    async findByVersion(workflowId: string, version: number) {
      const r = rows
        .filter((x) => x.workflow_id === workflowId && x.version === version)
        .sort((a, b) => b.deploy_number - a.deploy_number)[0];
      if (!r) return null;
      return {
        deploy_number: r.deploy_number, tag_name: r.tag_name, action: r.action,
        spec_json: r.spec_json, note: r.note, gmt_create: r.gmt_create,
      };
    },
    async getLatestDeploy() { return null; },
    async findByDeployNumber() { return null; },
    async findByVersionDeployOrEdit(workflowId: string, version: number) {
      const r = rows
        .filter((x) => x.workflow_id === workflowId && x.version === version && (x.action === "deploy" || x.action === "edit"))
        .sort((a, b) => b.deploy_number - a.deploy_number)[0];
      if (!r) return null;
      return {
        deploy_number: r.deploy_number, version: r.version, tag_name: r.tag_name,
        action: r.action, spec_json: r.spec_json, note: r.note,
        from_deploy_number: r.from_deploy_number, gmt_create: r.gmt_create,
      };
    },
    async findByWorkflowAndDeployNumber(workflowId: string, deployNumber: number) {
      const r = rows.find((x) => x.workflow_id === workflowId && x.deploy_number === deployNumber);
      if (!r) return null;
      return {
        deploy_number: r.deploy_number, version: r.version, tag_name: r.tag_name,
        action: r.action, spec_json: r.spec_json, note: r.note,
        from_deploy_number: r.from_deploy_number, gmt_create: r.gmt_create,
      };
    },
    async findActiveByWorkflowId(workflowId: string) {
      const r = rows
        .filter((x) => x.workflow_id === workflowId && x.is_active === 1)
        .sort((a, b) => b.deploy_number - a.deploy_number)[0];
      if (!r) return null;
      return {
        pack_id: r.pack_id, deploy_number: r.deploy_number, version: r.version,
        tag_name: r.tag_name, action: r.action, spec_json: r.spec_json,
        bot_id: r.bot_id, owner_id: r.owner_id, gmt_create: r.gmt_create, gmt_modified: r.gmt_modified,
      };
    },
    async setActive(workflowId: string, version: number) {
      const exists = rows.some((x) => x.workflow_id === workflowId && x.version === version);
      if (!exists) return false;
      for (const r of rows) {
        if (r.workflow_id === workflowId) r.is_active = 0;
      }
      const target = rows
        .filter((x) => x.workflow_id === workflowId && x.version === version)
        .sort((a, b) => b.deploy_number - a.deploy_number)[0];
      if (target) target.is_active = 1;
      return true;
    },
  } as unknown as WorkflowDeployHistoryRepository;
}

function row(overrides: Partial<HistoryRow> = {}): HistoryRow {
  return {
    id: 1,
    pack_id: "tech-research",
    workflow_id: "tech-research",
    deploy_number: 1,
    version: 1,
    tag_name: "deploy/tech-research/#1",
    action: "deploy",
    from_deploy_number: null,
    spec_json: "id: tech-research\nversion: 1\n",
    note: null,
    bot_id: "botA",
    owner_id: "ownerA",
    is_active: 0,
    gmt_create: 1719000000,
    gmt_modified: 1719000000,
    ...overrides,
  };
}

let app: express.Express;
let server: http.Server;
let baseUrl: string;

function startApp(repo: WorkflowDeployHistoryRepository | null) {
  return new Promise<void>((resolve) => {
    server = http.createServer((app = express()));
    app.use(express.json());
    // Only wfdhRepo is exercised by the history routes; pass null for the rest.
    app.use("/api/workflows", createWorkflowsRouter(null, null, null, null, repo, null));
    server.listen(0, () => {
      const addr = server.address();
      baseUrl = `http://127.0.0.1:${typeof addr === "object" && addr ? addr.port : 0}`;
      resolve();
    });
  });
}

async function stopApp() {
  await new Promise<void>((resolve) => server.close(() => resolve()));
}

describe("workflow version history (read-only)", () => {
  afterEach(async () => {
    if (server.listening) await stopApp();
  });

  describe("GET /api/workflows/:wf/history", () => {
    beforeEach(async () => {
      await startApp(makeFakeRepo([
        row({ deploy_number: 5, version: 3, action: "edit", gmt_create: 1719100000 }),
        row({ deploy_number: 3, version: 2, action: "deploy", gmt_create: 1719000000 }),
      ]));
    });

    it("returns history ordered by deploy_number desc with expected fields", async () => {
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history`);
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body.workflowId).toBe("tech-research");
      expect(body.history).toHaveLength(2);
      expect(body.history[0].deployNumber).toBe(5);
      expect(body.history[0].version).toBe(3);
      expect(body.history[0].action).toBe("edit");
      expect(body.history[1].deployNumber).toBe(3);
      // spec_json never leaked in the list payload
      expect(body.history[0].specJson).toBeUndefined();
      expect(body.history[0].spec_json).toBeUndefined();
    });

    it("returns 503 when wfdhRepo is unavailable (NoOp)", async () => {
      await stopApp();
      await startApp(null);
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history`);
      expect(res.status).toBe(503);
    });
  });

  describe("GET /api/workflows/:wf/history/:version", () => {
    beforeEach(async () => {
      await startApp(makeFakeRepo([row({ deploy_number: 3, version: 2, action: "deploy", spec_json: "id: x\nversion: 2\n" })]));
    });

    it("returns the full snapshot for an existing version", async () => {
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history/2`);
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body.version).toBe(2);
      expect(body.deployNumber).toBe(3);
      expect(body.specJson).toContain("version: 2");
    });

    it("returns 404 for a missing version", async () => {
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history/99`);
      expect(res.status).toBe(404);
    });
  });

  describe("GET /api/workflows/:wf/history/diff", () => {
    beforeEach(async () => {
      await startApp(makeFakeRepo([
        row({ deploy_number: 3, version: 2, action: "deploy", spec_json: "id: tech\nversion: 2\ntimeout: 30\n" }),
        row({ deploy_number: 5, version: 3, action: "edit", spec_json: "id: tech\nversion: 3\ntimeout: 60\n" }),
        // A 'pull' record at version 3 — must be ignored when diffing by version
        row({ deploy_number: 6, version: 3, action: "pull", spec_json: "STALE_PULL_CONTENT" }),
      ]));
    });

    it("returns both specs for valid from/to versions (legacy by-version)", async () => {
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history/diff?from=2&to=3`);
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body.workflowId).toBe("tech-research");
      expect(body.from.version).toBe(2);
      expect(body.from.specJson).toContain("timeout: 30");
      expect(body.to.version).toBe(3);
      // 'pull' record at version 3 ignored; the edit record's content is used
      expect(body.to.specJson).toContain("timeout: 60");
      expect(body.to.specJson).not.toContain("STALE_PULL_CONTENT");
    });

    it("returns both specs for valid fromDeploy/toDeploy (precise by-deploy)", async () => {
      // Comparing deploy #3 (v2 deploy) against deploy #6 (v3 pull) — by-deploy the pull record
      // IS used, since the user explicitly picked that row.
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history/diff?fromDeploy=3&toDeploy=6`);
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body.from.deployNumber).toBe(3);
      expect(body.from.specJson).toContain("timeout: 30");
      expect(body.to.deployNumber).toBe(6);
      expect(body.to.specJson).toContain("STALE_PULL_CONTENT");
    });

    it("returns 404 when both from and fromDeploy are missing", async () => {
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history/diff?to=3`);
      expect(res.status).toBe(404);
    });

    it("returns 404 when from version does not exist", async () => {
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history/diff?from=99&to=3`);
      expect(res.status).toBe(404);
    });

    it("returns 404 when to version does not exist", async () => {
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history/diff?from=2&to=99`);
      expect(res.status).toBe(404);
    });

    it("returns 404 when fromDeploy does not exist", async () => {
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history/diff?fromDeploy=99&toDeploy=5`);
      expect(res.status).toBe(404);
    });
  });

  describe("GET /api/workflows/:wf/history/by-deploy/:deployNumber", () => {
    beforeEach(async () => {
      await startApp(makeFakeRepo([
        row({ deploy_number: 5, version: 3, action: "edit", spec_json: "id: tech\nversion: 3\n" }),
        // A rollback record at the same version 3 — by-deploy must target THIS exact row
        row({ deploy_number: 4, version: 3, action: "rollback", spec_json: "ROLLBACK_CONTENT" }),
      ]));
    });

    it("returns the exact deploy record (independent of action)", async () => {
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history/by-deploy/4`);
      expect(res.status).toBe(200);
      const body = await res.json();
      expect(body.deployNumber).toBe(4);
      expect(body.version).toBe(3);
      expect(body.action).toBe("rollback");
      expect(body.specJson).toContain("ROLLBACK_CONTENT");
    });

    it("returns 404 for a missing deploy number", async () => {
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history/by-deploy/99`);
      expect(res.status).toBe(404);
    });
  });

  describe("gmt_create formatting (Invalid Date regression)", () => {
    // mysql2 returns a JS Date object for TIMESTAMP columns; the list/diff/snapshot endpoints
    // must coerce it to epoch seconds rather than leaking a Date that JSON-serializes oddly.
    it("history list returns epoch-seconds gmtCreate for a Date object", async () => {
      const gmt = new Date("2026-07-20T10:00:00Z");
      await startApp(makeFakeRepo([row({ deploy_number: 1, version: 1, gmt_create: gmt as unknown as number })]));
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history`);
      const body = await res.json();
      expect(typeof body.history[0].gmtCreate).toBe("number");
      expect(body.history[0].gmtCreate).toBe(Math.floor(gmt.getTime() / 1000));
    });

    it("snapshot returns epoch-seconds gmtCreate for a string", async () => {
      await startApp(makeFakeRepo([row({ deploy_number: 1, version: 1, gmt_create: "2026-07-20 10:00:00" })]));
      const res = await fetch(`${baseUrl}/api/workflows/tech-research/history/1`);
      const body = await res.json();
      expect(typeof body.gmtCreate).toBe("number");
      expect(body.gmtCreate).toBeGreaterThan(0);
    });
  });
});