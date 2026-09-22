import { afterEach, describe, expect, it } from "vitest";
import express from "express";
import type { Server } from "node:http";
import { createInternalApprovalCardsRouter } from "../internal/approval-cards";
import { ApprovalCardRepository } from "../../repositories/approval-card-repository";

const servers: Server[] = [];

afterEach(async () => {
  await Promise.all(
    servers.splice(0).map(
      (s) => new Promise<void>((resolve, reject) => s.close((err) => (err ? reject(err) : resolve()))),
    ),
  );
});

type InsertCall = {
  sql: string;
  params: unknown[];
};

function makeRepo(opts: {
  existingPending?: { id: number; flow_id: string; node_id: string };
  insertId?: number;
}) {
  const inserts: InsertCall[] = [];
  const queries: InsertCall[] = [];

  const db = {
    query: async (sql: string, params: unknown[]) => {
      queries.push({ sql, params });
      if (
        opts.existingPending &&
        sql.includes("flow_id = ?") &&
        sql.includes("node_id = ?") &&
        sql.includes("status = 'pending'")
      ) {
        return [{
          id: opts.existingPending.id,
          flow_id: opts.existingPending.flow_id,
          node_id: opts.existingPending.node_id,
          workflow_id: "wf-1",
          workflow_title: null,
          approval_type: null,
          message: null,
          card_fields_json: null,
          approver_ids: "reviewer",
          approver_names: null,
          approval_policy: "any",
          approved_by: "",
          rejected_by: "",
          status: "pending",
          delivery_mode: "card-web",
          created_at: 100,
          resolved_at: null,
          comment: null,
        }];
      }
      return [];
    },
    exec: async (sql: string, params: unknown[]) => {
      inserts.push({ sql, params });
      return { insertId: opts.insertId ?? 42, affectedRows: 1 };
    },
  };

  return {
    repo: new ApprovalCardRepository(db as any),
    inserts,
    queries,
  };
}

async function createServer(repo: ApprovalCardRepository) {
  const app = express();
  app.use(express.json());
  app.use("/approval-cards", createInternalApprovalCardsRouter(repo));
  const server = app.listen(0, "127.0.0.1");
  servers.push(server);
  await new Promise<void>((resolve) => server.once("listening", resolve));
  const base = `http://127.0.0.1:${(server.address() as any).port}/approval-cards`;
  return { app, server, base };
}

const validPayload = {
  flow_id: "flow-1",
  node_id: "review",
  workflow_id: "wf-1",
  approver_ids: "reviewer",
};

describe("POST /approval-cards", () => {
  it("creates a new card when no pending card exists", async () => {
    const { repo, inserts, queries } = makeRepo({ insertId: 123 });
    const { base } = await createServer(repo);

    const res = await fetch(base, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(validPayload),
    });

    expect(res.status).toBe(201);
    expect(await res.json()).toEqual({ success: true, data: { id: 123 } });
    expect(queries.length).toBe(1);
    expect(inserts.length).toBe(1);
  });

  it("reuses an existing pending card instead of inserting a duplicate", async () => {
    const { repo, inserts, queries } = makeRepo({
      existingPending: { id: 777, flow_id: "flow-1", node_id: "review" },
    });
    const { base } = await createServer(repo);

    const res = await fetch(base, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(validPayload),
    });

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ success: true, data: { id: 777 } });
    expect(queries.length).toBe(1);
    expect(inserts.length).toBe(0);
  });

  it("returns 400 when required fields are missing", async () => {
    const { repo } = makeRepo({});
    const { base } = await createServer(repo);

    const res = await fetch(base, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flow_id: "flow-1", node_id: "review" }),
    });

    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.success).toBe(false);
    expect(body.error).toBe("Bad Request");
  });

  it("returns 503 when repository is not configured", async () => {
    const { base } = await createServer(null as any);

    const res = await fetch(base, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(validPayload),
    });

    expect(res.status).toBe(503);
    expect(await res.json()).toMatchObject({ success: false, error: "Service Unavailable" });
  });
});
