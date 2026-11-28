// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import type { Server } from "node:http";
import Database from "better-sqlite3";
import express from "express";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { ApprovalCardRepository } from "../../repositories/approval-card-repository.js";
import { createInternalApprovalCardsRouter, invalidateResolvedCache } from "../internal/approval-cards.js";

type Card = { id: number; flow_id: string; node_id: string; status: string };
const live: Array<Awaited<ReturnType<typeof openServer>>> = [];
const directories: string[] = [];

afterEach(async () => {
  for (const instance of live) await instance.close();
  live.length = 0;
  invalidateResolvedCache();
  vi.restoreAllMocks();
  for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
});

async function openServer(file: string) {
  const raw = new Database(file);
  const db = new SqliteDatabase(raw);
  const repo = new ApprovalCardRepository(db);
  const app = express();
  app.use(express.json());
  app.use("/approval-cards", createInternalApprovalCardsRouter(repo));
  const server = await new Promise<Server>(resolve => {
    const listening = app.listen(0, "127.0.0.1", () => resolve(listening));
  });
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/approval-cards`;
  let closed = false;
  const instance = { raw, db, repo, base, async close() {
    if (closed) return;
    closed = true;
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    await db.close();
    // A real server restart also loses process-local query caches.
    invalidateResolvedCache();
  } };
  live.push(instance);
  return instance;
}

async function seed(decision: string) {
  const directory = mkdtempSync(join(tmpdir(), "approval-legacy-restart-"));
  directories.push(directory);
  const file = join(directory, "approvals.sqlite");
  const instance = await openServer(file);
  instance.raw.exec(`
    CREATE TABLE approval_cards (
      id INTEGER PRIMARY KEY, flow_id TEXT NOT NULL, node_id TEXT DEFAULT 'review',
      status TEXT NOT NULL, delivery_mode TEXT DEFAULT 'card-web',
      approved_by TEXT DEFAULT '', rejected_by TEXT DEFAULT '', comment TEXT DEFAULT 'keep audit',
      created_at INTEGER DEFAULT 100, resolved_at INTEGER DEFAULT 200,
      processed_at INTEGER, gmt_modified INTEGER DEFAULT 100
    );
    CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, origin_bot_id TEXT, status TEXT);
    INSERT INTO flow_runs VALUES ('waiting-flow', 'bot-a:owner', 'waiting'),
      ('finished-flow', 'bot-a:owner', 'succeeded');
  `);
  const insert = instance.raw.prepare(`INSERT INTO approval_cards
    (id, flow_id, status, approved_by, rejected_by) VALUES (?, ?, ?, ?, ?)`);
  insert.run(1, "waiting-flow", decision, decision === "approved" ? "reviewer" : "", decision === "rejected" ? "reviewer" : "");
  insert.run(2, "finished-flow", decision, "", "");
  return { file, instance };
}

async function resolved(base: string, query: string): Promise<Card[]> {
  const response = await fetch(`${base}/resolved${query}`);
  expect(response.status).toBe(200);
  const body = await response.json();
  expect(body.success).toBe(true);
  return body.data;
}

async function acknowledge(base: string, id: number, body: object) {
  const response = await fetch(`${base}/${id}/processed`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  expect(response.status).toBe(200);
  expect(await response.json()).toEqual({ success: true });
}

/** Wire-contract model of the old API client, not a copy of an external engine.
 * It sends the SAME empty ACK on success and on its third callback failure,
 * then suppresses that card locally until the poller restarts. Actual router,
 * repository, HTTP and file-backed persistence are exercised without mocks.
 */
function legacyPoller(base: string, query: string, resume: (card: Card) => Promise<boolean>) {
  const localProcessed = new Set<number>();
  const failures = new Map<number, number>();
  return async () => {
    for (const card of await resolved(base, query)) {
      if (localProcessed.has(card.id)) continue;
      const success = await resume(card);
      const attempts = (failures.get(card.id) ?? 0) + 1;
      if (success || attempts >= 3) {
        localProcessed.add(card.id);
        failures.delete(card.id);
        await acknowledge(base, card.id, {});
      } else {
        failures.set(card.id, attempts);
      }
    }
  };
}

describe("legacy failed-callback ACKs remain recoverable across server and poller restarts", () => {
  for (const query of ["", "?botId=bot-a"]) {
    it.each(["approved", "rejected"])(`redelivers %s after three failures and an empty ACK (${query || "legacy query"})`, async decision => {
      const { file, instance } = await seed(decision);
      const history = instance.raw.prepare("SELECT * FROM approval_cards ORDER BY id").all();
      const failures = vi.fn<(card: Card) => Promise<boolean>>().mockResolvedValue(false);
      const firstLifetime = legacyPoller(instance.base, query, failures);
      expect((await resolved(instance.base, query)).map(card => card.id)).toEqual([1]);
      for (let attempt = 0; attempt < 3; attempt++) await firstLifetime();
      expect(failures).toHaveBeenCalledTimes(3);
      expect(failures).toHaveBeenLastCalledWith(expect.objectContaining({ id: 1, status: decision }));
      expect(await instance.repo.findById(1)).toMatchObject({ processed_at: null });
      expect(instance.raw.prepare("SELECT * FROM approval_cards ORDER BY id").all()).toEqual(history);
      await firstLifetime();
      expect(failures).toHaveBeenCalledTimes(3); // old process-local give-up behavior
      await instance.close();

      // A new server/router/repository/SQLite connection and poller have no
      // memory from the failed lifetime; the approval must still be delivered.
      const restarted = await openServer(file);
      const recovery = vi.fn<(card: Card) => Promise<boolean>>().mockResolvedValue(true);
      await legacyPoller(restarted.base, query, recovery)();
      expect(recovery).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({ id: 1, status: decision }));
      expect(restarted.raw.prepare("SELECT * FROM approval_cards ORDER BY id").all()).toEqual(history);
      expect((await resolved(restarted.base, query)).map(card => card.id)).toEqual([1]);
      await restarted.close();

      // An upgraded client's explicit success-only acknowledgement is durable.
      const capable = await openServer(file);
      const rows = await resolved(capable.base, query);
      expect(rows.map(card => card.id)).toEqual([1]);
      const consumed = vi.fn<(card: Card) => Promise<boolean>>().mockResolvedValue(true);
      expect(await consumed(rows[0])).toBe(true);
      await acknowledge(capable.base, 1, { consumed: true });
      const timestamp = (await capable.repo.findById(1))?.processed_at;
      expect(timestamp).toEqual(expect.any(Number));
      expect(await resolved(capable.base, query)).toEqual([]);
      await capable.close();
      const finalLifetime = await openServer(file);
      expect(await resolved(finalLifetime.base, query)).toEqual([]);
      expect(await finalLifetime.repo.findById(1)).toMatchObject({ processed_at: timestamp });
      expect(await finalLifetime.repo.findById(2)).toMatchObject({ processed_at: null });
    });
  }

  it.each(["succeeded", "failed", "cancelled", "canceled", "completed"])("still excludes a legacy-acknowledged run after it becomes %s", async terminal => {
    const { file, instance } = await seed("approved");
    await acknowledge(instance.base, 1, {});
    expect(await instance.repo.findById(1)).toMatchObject({ processed_at: null });
    instance.raw.prepare("UPDATE flow_runs SET status = ? WHERE flow_id = 'waiting-flow'").run(terminal);
    await instance.close();
    const restarted = await openServer(file);
    for (const query of ["", "?botId=bot-a"]) expect(await resolved(restarted.base, query)).toEqual([]);
    expect(await restarted.repo.findById(1)).toMatchObject({ status: "approved", processed_at: null });
  });
});
