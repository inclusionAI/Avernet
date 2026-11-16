import express from 'express';
import { expect, it, vi } from 'vitest';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { EvolveRepository } from '@avernet/clawevolve/server/repositories/evolve-repository';
import { SessionRecoveryTaskRepository, type RecoveryArtifactStore } from '../../../../repositories/repair/session-recovery-task-repository.js';
import { createSessionRecoveryRouter } from '../../../../routes/session-recovery.js';
import { SessionRecoveryGroupNotifier } from '../notification.js';
import type { SessionRecoveryAgentService } from '../agent-service.js';
import type { SessionRecoveryTaskService } from '../task-service.js';
import type { RecoveryStoredTask } from '../task-contracts.js';
import type { RecoveryActionNotifier, RecoveryActor } from '../contracts.js';
import type { BotRuntime } from '@avernet/clawweb-shared/server/services/bot-runtime';
import { notificationBotConfig } from './bot-config-fixture.js';

type Fixture = { request: { event_id: string }; runtime: BotRuntime; create: () => Promise<RecoveryStoredTask>; current: (id: string) => Promise<RecoveryStoredTask>;
  inspect: (task: RecoveryStoredTask, key?: string) => ReturnType<SessionRecoveryAgentService['sessionRead']>;
  actionNotifier: RecoveryActionNotifier; agent: SessionRecoveryAgentService; tasks: SessionRecoveryTaskRepository;
  repo: EvolveRepository; db: IDatabase; artifactStore: RecoveryArtifactStore; service: SessionRecoveryTaskService; actor: RecoveryActor };
/** Register notification/sharing integration cases against the existing closed-loop fixture. */
export function registerNotificationPersistenceTests(get: () => Fixture) {
it("persists deduplicated group delivery facts without changing the business version or exposing them to the Agent", async () => {
  const { create, current, actionNotifier, agent, tasks, repo } = get();
  const task = await create();
  const sent = await agent.retryMessage(task, { clientRequestId: "group-advice", purpose: "纠正原操作", message: "请按真实工具参数继续" });
  expect(actionNotifier.after).toHaveBeenCalledWith({ taskId: task.taskId, eventId: sent.toolCallId, kind: "message" });
  const before = await current(task.taskId);
  let finish!: () => void;
  const sender = { send: vi.fn(() => new Promise<void>(resolve => { finish = resolve; })) };
  const deps = { botConfigurations: notificationBotConfig(), controlPlaneEnvironment: "pre" as const, tasks, sender, publicBaseUrl: "https://service.invalid",
    config: { findByKey: async () => ({ enabled: 1, config_json: JSON.stringify({ routes: { test: { webhook: "https://robot.invalid/send", timeout_seconds: 10 } } }) }) } };
  const notice = { taskId: task.taskId, eventId: sent.toolCallId, kind: "message" as const };
  new SessionRecoveryGroupNotifier(deps).after(notice);
  new SessionRecoveryGroupNotifier(deps).after(notice);
  await vi.waitFor(() => expect(sender.send).toHaveBeenCalledOnce());
  expect((await current(task.taskId)).version).toBe(before.version);
  await agent.report(await current(task.taskId), { kind: "assessment", phase: "assess", decision: "message",
    summary: "已有现场证据，已发出指引", evidence_ids: [sent.evidenceId] });
  expect((await current(task.taskId)).phase).toBe("observe");
  finish();
  await vi.waitFor(async () => expect((await current(task.taskId)).timeline.some(event => event.payload.channel === "group_webhook" && event.payload.sent === true)).toBe(true));
  const after = await current(task.taskId);
  expect(after.version).toBe(before.version + 1);
  expect((await agent.bootstrap(after)).task.timeline.some(event => event.payload.channel === "group_webhook")).toBe(false);
  expect((await repo.findTask(task.taskId))!.config_json).not.toContain("https://robot.invalid");
});
it("persists sharing and authorizes read-only task links without granting Owner actions", async () => {
  const { request, runtime, create, current, inspect, agent, repo, db, artifactStore, service, actor } = get();
  const task = await create();
  const read = await inspect(task, "share-evidence");
  let principal: typeof actor | null = actor;
  const app = express();
  app.use(createSessionRecoveryRouter({ service, tasks: service, agent, resolveActor: async () => principal }));
  const server = await new Promise<ReturnType<typeof app.listen>>(resolve => { const opened = app.listen(0, "127.0.0.1", () => resolve(opened)); });
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/monitoring/session-recoveries`;
  const call = async (path: string, method = "GET", body?: unknown) => {
    const response = await fetch(base + path, { method, ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }) });
    return { status: response.status, body: await response.json() };
  };
  try {
    const version = (await current(task.taskId)).version;
    const scope = `/${task.taskId}`;
    expect(await call(scope + "/share", "PATCH", { shared: true })).toMatchObject({ status: 200, body: { shared: true, canOperate: true } });
    const restored = new SessionRecoveryTaskRepository(db, new EvolveRepository(db), artifactStore);
    expect(await restored.find(task.taskId)).toMatchObject({ shared: true, version: version + 1, phase: "assess", status: "running", execution: task.execution });
    const raw = JSON.parse((await repo.findTask(task.taskId))!.config_json);
    expect(raw.shared).toBe(true);
    principal = { userId: "reader", isAdmin: false };
    expect(await call(`?event_id=${request.event_id}`)).toMatchObject({ status: 200, body: { items: [{ task_id: task.taskId }] } });
    const visible = await call(scope);
    expect(visible).toMatchObject({ status: 200, body: { shared: true, canOperate: false, canManageShare: false } });
    expect(visible.body.execution).toBeUndefined(); expect(visible.body.aisExecution).toBeUndefined();
    expect((await call(scope + `/tool-calls/${read.toolCallId}`)).status).toBe(200);
    for (const [suffix, method, body] of [
      ["/share", "PATCH", { shared: false }],
      ["/decision", "POST", { plan_digest: "digest", approved: true, cooperation_done: true }],
      ["/annotations", "POST", { layer: "effect", correct: true, reason: "read only", evidence_ids: [] }],
      ["/terminate", "POST", { reason: "read only" }],
    ] as const) expect((await call(scope + suffix, method, body)).status).toBe(403);
    principal = null;
    expect((await call(scope)).status).toBe(401);
    principal = actor;
    expect((await call(scope + "/share", "PATCH", { shared: "true" })).status).toBe(422);
    expect((await call(scope + "/share", "PATCH", { shared: false })).status).toBe(200);
    principal = { userId: "reader", isAdmin: false };
    expect((await call(scope)).status).toBe(403);
    expect((await call(scope + `/tool-calls/${read.toolCallId}`)).status).toBe(403);
    expect(await call(`?event_id=${request.event_id}`)).toMatchObject({ status: 200, body: { items: [] } });
    expect(runtime.sendMessage).not.toHaveBeenCalled();
  } finally { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); }
});
}
