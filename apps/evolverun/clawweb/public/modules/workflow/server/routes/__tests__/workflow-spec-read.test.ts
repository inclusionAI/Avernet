import { afterEach, describe, expect, it, vi } from "vitest";
import express from "express";
import { once } from "node:events";
import type { WorkflowSpecRepository, WorkflowSpecRow } from "../../repositories/workflow-spec-repository.js";
import { createWorkflowsRouter } from "../workflows.js";

let server: ReturnType<express.Application["listen"]> | null = null;

afterEach(async () => {
  const active = server;
  server = null;
  if (active) await new Promise<void>((resolve) => active.close(() => resolve()));
});

/** Build a WorkflowSpecRow with a known spec_json containing no version field. */
function makeRow(version: number | null): WorkflowSpecRow {
  return {
    id: 1,
    workflow_id: "wf-1",
    pack_id: null,
    version,
    spec_json: JSON.stringify({ name: "wf-1", nodes: [] }),
    gmt_create: 1000,
    gmt_modified: 2000,
    title: "wf-1",
  };
}

async function start(rows: WorkflowSpecRow[]) {
  const workflowSpecRepo = {
    findByWorkflowId: vi.fn(async (_workflowId: string) => rows[0] ?? null),
  } as unknown as WorkflowSpecRepository;

  const app = express();
  app.use(express.json());
  app.use("/api/workflows", createWorkflowsRouter(workflowSpecRepo, null, null, null, null, null));
  const instance = app.listen(0, "127.0.0.1");
  await once(instance, "listening");
  server = instance;
  const address = instance.address();
  if (!address || typeof address === "string") throw new Error("test server did not bind");
  return `http://127.0.0.1:${address.port}`;
}

describe("GET /api/workflows/:workflowId — version propagation contract", () => {
  it("attaches the synced deploy version to the returned spec", async () => {
    const baseUrl = await start([makeRow(3)]);
    const res = await fetch(`${baseUrl}/api/workflows/wf-1`);
    expect(res.status).toBe(200);
    const body = await res.json();
    // spec_json itself has no version; the synced version must come from the row.
    expect(body.version).toBe(3);
    expect(body.name).toBe("wf-1");
  });

  it("omits version when the row version is null", async () => {
    const baseUrl = await start([makeRow(null)]);
    const res = await fetch(`${baseUrl}/api/workflows/wf-1`);
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.version).toBeUndefined();
  });

  it("returns 404 when the workflow is not found", async () => {
    const baseUrl = await start([]);
    const res = await fetch(`${baseUrl}/api/workflows/wf-missing`);
    expect(res.status).toBe(404);
  });
});