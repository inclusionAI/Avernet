import Database from "better-sqlite3";
import express from "express";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import { SessionRecoveryTaskRepository } from "../../../../repositories/repair/session-recovery-task-repository.js";
import { AgentToolCallRepository } from "../../../../repositories/repair/agent-tool-call-repository.js";
import { createSessionRecoveryRouter } from "../../../../routes/session-recovery.js";
import { SessionRecoveryTaskService } from "../task-service.js";
import { SessionRecoveryAgentService } from "../agent-service.js";
import { parseRecoverySimulationBatch } from "../simulation.js";
import type { RecoveryStoredTask, RecoveryPlan } from "../task-contracts.js";

const actor = { userId: "owner", isAdmin: false };
const request = { event_id: "historical-event", delivery_key: "historical-delivery", diagnosis: "MCP 调用失败", tc_fault_label: "TC.MCP.CALL",
  target: { entity_id: "owner", bot_id: "default", env: "prod", engine: "OC" }, session_key: "agent:main:original" };
const historicalCase = { request: { ...request, session_id: 'original' }, cutoff_at_ms: 10000, cutoff_node: { message_id: 'M100' } };
const batch = () => structuredClone({ batch_id: "handbook-v1", cases: [historicalCase] });
const plan: RecoveryPlan = { summary: "恢复原任务所需环境", basis: "历史工具失败及关联配置证据", scope: "已确认的局部文件", verification: "实际执行时确认失败步骤成功并继续原任务",
  actions: [{ actionId: "fix", operationScope: { operation: "file.write", target: "/workspace/memory.md" }, summary: "修正已确认的局部配置", risk: "局部内容变化", rollback: "恢复本次修改前内容", verification: "核对原任务实际执行" }], owner_cooperation: [] };
let db: SqliteDatabase, repo: EvolveRepository, store: SessionRecoveryTaskRepository, service: SessionRecoveryTaskService, agent: SessionRecoveryAgentService;
let runtime: any, targets: any, trajectory: any, notifier: any, actionNotifier: any, reporter: any, ais: any, artifacts: ConstructorParameters<typeof SessionRecoveryTaskRepository>[2];
const current = async (id: string) => (await store.find(id))!;
beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:")); await runMigrations(db, "sqlite"); repo = new EvolveRepository(db);
  const objects = new Map<string, Buffer>();
  artifacts = { putObject: async (key, content) => { objects.set(key, Buffer.from(content)); return { etag: null }; },
    getObject: async key => ({ content: objects.get(key)!, contentType: "application/json", etag: null }) };
  store = new SessionRecoveryTaskRepository(db, repo, artifacts);
  const unavailable = () => { throw new Error("live target must never be accessed"); };
  runtime = { sendMessage: vi.fn(unavailable), request: vi.fn(unavailable), executeShell: vi.fn(unavailable) };
  targets = { resolve: vi.fn(unavailable) };
  trajectory = { supportsHistoricalBoundary: true, read: vi.fn().mockResolvedValue({ source: "aivision_tclog", traces: [] }) };
  notifier = { notify: vi.fn() }; actionNotifier = { after: vi.fn() }; reporter = { report: vi.fn() };
  ais = { execute: vi.fn().mockResolvedValue("job-simulation"), stopExecution: vi.fn(), getJobStatus: vi.fn(), getJobStatusDetail: vi.fn().mockResolvedValue({ status: "running" }) };
  agent = new SessionRecoveryAgentService({ controlPlaneEnvironment: "pre", tasks: store, repo,
    audit: new AgentToolCallRepository(db, "session_recovery"), runtime, targets, trajectory, ais, business: () => service,
    publicBaseUrl: "https://service.invalid", snapshotId: 1, nasRoots: ["/nas"], leaseSeconds: 120, launchGraceSeconds: 1800, observationMs: 3600000, actionNotifier });
  service = new SessionRecoveryTaskService({ controlPlaneEnvironment: "pre", tasks: store, agent, notifier, reporter, actionNotifier,
    observationMs: 3600000, resolveTarget: targets.resolve });
});
afterEach(() => db.close());
async function create(input: unknown = batch()): Promise<RecoveryStoredTask> {
  const ack = await service.createSimulations(input, actor);
  await vi.waitFor(async () => expect((await current(ack.items[0].task_id)).execution?.jobId).toBe("job-simulation"));
  return current(ack.items[0].task_id);
}
const read = (task: RecoveryStoredTask) => agent.sessionRead(task, { clientRequestId: "history-1", purpose: "读取原始失败步骤", from_line: 1, limit: 2,
  nas_evidence: { source: '/nas/prod/prod_staff_owner_openclaw_default/.openclaw/agents/main/sessions/original.jsonl', session_id: 'original', delivery_key: request.delivery_key,
    messages: [{ line_no: 2, record: { id: 'M100', type: 'message', timestamp: 9000, message: { role: 'assistant', content: '原始工具调用失败' } } }],
    failure_context: [], historical_boundary: { message_id: 'M100', cutoff_at_ms: 10000, end_line: 2 }, next_line: null } });
function noExternalEffects() {
  for (const fn of [runtime.request, runtime.executeShell, runtime.sendMessage, targets.resolve, notifier.notify, actionNotifier.after, reporter.report]) expect(fn).not.toHaveBeenCalled();
}

it("starts without resolving the current runtime and persists the bound original node", async () => {
  const task = await create();
  const raw = JSON.parse((await repo.findTask(task.taskId))!.config_json);
  expect(raw.simulation).toEqual({ batchId: "handbook-v1", cutoffAtMs: 10000, cutoffNode: { messageId: 'M100' }, inputDigest: task.simulation!.inputDigest });
  const restored = (await new SessionRecoveryTaskRepository(db, repo, artifacts).find(task.taskId))!;
  const bootstrap = await agent.bootstrap(restored);
  expect(bootstrap.task.simulation).toEqual(task.simulation);
  expect(bootstrap.tools).toMatchObject({ session_read: true, trajectory_read: true, runtime_inspect: true, retry_message: false, apply_action: false });
  expect(JSON.parse(ais.execute.mock.calls[0][1]["${clawevolve_params}"]).input.simulation).toBe(true);
  noExternalEffects();
});

it("audits existing bounded session reads and records the proposed message without a delivery receipt", async () => {
  let task = await create(); const receipt = await read(task);
  expect(receipt).toMatchObject({ status: "succeeded", result: { next_line: null, historical_boundary: { message_id: 'M100', cutoff_at_ms: 10000 } } });
  task = await current(task.taskId);
  await agent.report(task, { kind: "assessment", phase: "assess", decision: "message", summary: "原 Bot 可核对调用后继续原任务", final_summary: "历史任务因调用格式受阻，建议纠正参数后从失败步骤继续。未执行消息干预。", evidence_ids: [receipt.evidenceId], message: "请核对原调用参数，从失败步骤继续；不要重复已完成的操作。",
    knowledge: [{ path: "clawevolve-recovery-handbook/mcp/handbook.md", sha256: "a".repeat(64) }] });
  task = await current(task.taskId);
  expect(task).toMatchObject({ status: "completed", phase: "assess", final_summary: expect.stringContaining("未执行消息干预"), judgment: { effect: "not_intervened" }, pending_results: [] });
  expect(task.delivery).toBeUndefined();
  expect(task.timeline.find(event => event.kind === "assessment")!.payload).toMatchObject({ message: expect.stringContaining("从失败步骤继续"), knowledge: [{ sha256: "a".repeat(64) }] });
  await service.reportResults(); noExternalEffects();
});

it("uses the existing complete plan contract, terminates before apply, and cannot approve or resume it", async () => {
  let task = await create(); const receipt = await read(task); task = await current(task.taskId);
  await agent.report(task, { kind: "assessment", phase: "assess", decision: "environment", summary: "需要环境处理", final_summary: "不应保存初步摘要", evidence_ids: [receipt.evidenceId] });
  task = await current(task.taskId); expect(task.phase).toBe("plan");
  expect(task.final_summary).toBeUndefined();
  await agent.report(task, { kind: "plan", phase: "plan", plan, final_summary: "已形成完整拟议方案及必要配合事项，未执行环境修改。" });
  task = await current(task.taskId);
  expect(task).toMatchObject({ status: "completed", phase: "plan", final_summary: expect.stringContaining("未执行环境修改"), plan: { approvedAt: null }, judgment: { effect: "not_intervened" }, pending_results: [] });
  await expect(service.decide(task.taskId, task.plan!.digest, true, actor, true)).rejects.toMatchObject({ status: 409 });
  await expect(service.resume(task.taskId)).rejects.toMatchObject({ status: 409 });
  noExternalEffects();
});

it("accepts a missing historical scene as an inconclusive simulation, including during plan", async () => {
  const input = batch();
  let task = await create(input);
  await agent.report(task, { kind: "assessment", phase: "assess", decision: "insufficient_evidence", summary: "仅有诊断线索，无法确认当时的实际操作", evidence_ids: [] });
  expect(await current(task.taskId)).toMatchObject({ status: "completed", judgment: { effect: "not_intervened" }, pending_results: [] });
  const second = batch(); second.batch_id = "handbook-v2"; task = await create(second);
  const receipt = await read(task); task = await current(task.taskId);
  await agent.report(task, { kind: "assessment", phase: "assess", decision: "environment", summary: "需要核查环境依赖", evidence_ids: [receipt.evidenceId] });
  task = await current(task.taskId);
  await agent.report(task, { kind: "assessment", phase: "plan", decision: "insufficient_evidence", summary: "缺少历史配置，无法确定环境动作", evidence_ids: [receipt.evidenceId] });
  expect(await current(task.taskId)).toMatchObject({ status: "completed", phase: "plan", plan: null });
  noExternalEffects();
});

it("rejects intervention tools and fabricated execution or effect reports", async () => {
  const task = await create();
  for (const method of ["apply", "retryMessage", "reconcileWrite"] as const) {
    await expect(agent[method](task, { clientRequestId: "attempt", purpose: "不允许的调用" })).rejects.toMatchObject({ status: 403 });
  }
  for (const kind of ["apply", "observation"]) await expect(agent.report(task, { kind, phase: "assess" })).rejects.toMatchObject({ status: 403 });
  await expect(agent.report(task, { kind: "assessment", phase: "assess", decision: "message", summary: "缺正文", evidence_ids: [] })).rejects.toMatchObject({ status: 422 });
  noExternalEffects();
});

it("keeps batch retries idempotent and rejects altered inputs without creating another execution", async () => {
  const task = await create();
  const ack = await service.createSimulations(batch(), actor); expect(ack.items[0].task_id).toBe(task.taskId);
  expect(ais.execute).toHaveBeenCalledOnce();
  const altered = batch(); altered.cases[0].cutoff_node.message_id = "M101";
  await expect(service.createSimulations(altered, actor)).rejects.toMatchObject({ status: 409 });
  noExternalEffects();
});

it("does not accept an unavailable session read as evidence for intervention", async () => {
  let task = await create();
  const receipt = await agent.sessionRead(task, { clientRequestId: 'missing', purpose: '读取已不可用的历史现场' });
  expect(receipt.status).toBe('failed'); task = await current(task.taskId);
  await expect(agent.report(task, { kind: 'assessment', phase: 'assess', decision: 'message', summary: '不能把材料缺失说成故障', evidence_ids: [receipt.evidenceId], message: '不应发送' })).rejects.toMatchObject({ status: 422 });
  expect(runtime.sendMessage).not.toHaveBeenCalled();
});

it("allows readonly current environment investigation and labels its temporal context", async () => {
  let task = await create();
  targets.resolve.mockResolvedValue({ environment: 'prod', ownerId: 'owner', botId: 'default', activeEngine: 'openclaw', provider: 'fixture', bindingId: 'binding', deviceId: 'device' });
  runtime.executeShell.mockResolvedValue({ status: 'success', stdout: 'current files', stderr: '', exitCode: 0 });
  const result = await agent.inspect(task, { clientRequestId: 'environment', purpose: '核查当前工作区仍适用的配置', operation: 'fs_list', path: '/workspace' });
  expect(result).toMatchObject({ status: 'succeeded', result: { temporal_context: 'current_environment', observed_at_ms: expect.any(Number) } });
  for (const body of [{ operation: 'shell_exec', command: 'anything' }, { operation: 'fs_read', path: '/home/admin/.openclaw/agents/main/sessions/original.jsonl' }, { operation: 'http_get', url: 'http://localhost' }]) {
    await expect(agent.inspect(task, { clientRequestId: 'bypass', purpose: '不允许绕过历史边界', ...body })).rejects.toMatchObject({ status: 403 });
  }
  expect(runtime.sendMessage).not.toHaveBeenCalled(); expect(notifier.notify).not.toHaveBeenCalled();
});

it.each(["observe", "user_continued"])("records %s as a completed proposal without observing current Bot progress", async decision => {
  let task = await create(); const receipt = await read(task); task = await current(task.taskId);
  await agent.report(task, { kind: "assessment", phase: "assess", decision, summary: "依据截止时点内的历史记录，无需发起干预", evidence_ids: [receipt.evidenceId] });
  expect(await current(task.taskId)).toMatchObject({ status: "completed", phase: "assess", judgment: { effect: "not_intervened" }, pending_results: [] });
  noExternalEffects();
});

it("preserves cancellation against a late assessment and never queues online callbacks", async () => {
  let task = await create(); const receipt = await read(task); task = await current(task.taskId);
  await service.terminate(task.taskId, actor, "停止仿真");
  await expect(agent.report(task, { kind: "assessment", phase: "assess", decision: "message", summary: "迟到的判断", message: "不可发送", evidence_ids: [receipt.evidenceId] })).rejects.toMatchObject({ status: 409 });
  expect(await current(task.taskId)).toMatchObject({ status: "canceled", pending_results: [] });
  await service.reportResults(); noExternalEffects();
});

it("validates every case identity and timestamp before persisting the batch", async () => {
  const input = batch(); input.cases.push({ ...structuredClone(historicalCase), request: { ...request, event_id: "other", target: { ...request.target, entity_id: "other-owner" } } });
  await expect(service.createSimulations(input, actor)).rejects.toMatchObject({ status: 403 });
  expect(await db.query("SELECT task_id FROM ce_tasks")).toEqual([]);
  input.cases[0].cutoff_at_ms = Date.now() + 60000;
  expect(() => parseRecoverySimulationBatch(input, Date.now())).toThrow("cutoff_at_ms");
  expect(() => parseRecoverySimulationBatch({ ...batch(), cases: Array(11).fill(historicalCase) }, Date.now())).toThrow();
  noExternalEffects();
});

it("exposes batch creation through the existing authenticated router", async () => {
  let principal: typeof actor | null = null;
  const app = express(); app.use(createSessionRecoveryRouter({ service, tasks: service, agent, resolveActor: async () => principal }));
  const server = await new Promise<ReturnType<typeof app.listen>>(resolve => { const opened = app.listen(0, "127.0.0.1", () => resolve(opened)); });
  try {
    const url = `http://127.0.0.1:${(server.address() as { port: number }).port}/monitoring/session-recoveries/simulations`;
    const call = () => fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(batch()) });
    expect((await call()).status).toBe(401);
    principal = actor;
    const response = await call(); expect(response.status).toBe(202);
    const ack = await response.json(); expect(ack).toMatchObject({ batch_id: "handbook-v1", items: [{ event_id: "historical-event" }] });
    await vi.waitFor(() => expect(ais.execute).toHaveBeenCalledOnce());
    noExternalEffects();
  } finally { await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve())); }
});

it("passes original TE identity and the immutable boundary to the existing reader without resolving a container", async () => {
  const task = await create({ batch_id: 'te-history', cases: [{ ...historicalCase,
    request: { ...request, target: { ...request.target, engine: 'TE' } }, cutoff_node: { trace_id: 'anchor', observation_id: 'fault' } }] });
  await agent.trajectoryRead(task, { clientRequestId: 'trajectory', purpose: '读取原轨迹失败事件', trace_id: 'anchor', field: 'output' });
  expect(trajectory.read).toHaveBeenCalledWith(expect.objectContaining({ target: { environment: 'prod', ownerId: 'owner', botId: 'default' },
    sessionKey: request.session_key, until: { atMs: 10000, traceId: 'anchor', observationId: 'fault', sessionId: undefined } }));
  expect(trajectory.read.mock.calls[0][0]).not.toHaveProperty('messageMarker');
  trajectory.supportsHistoricalBoundary = false;
  await expect(agent.trajectoryRead(task, { clientRequestId: 'unsafe', purpose: '读取未支持边界的来源' })).rejects.toMatchObject({ status: 503 });
  noExternalEffects();
});

it("rejects a wrapper receipt whose page or continuation exceeds the stored session boundary", async () => {
  const task = await create();
  const receipt = await agent.sessionRead(task, { clientRequestId: 'outside-boundary', purpose: '检查越界读取不会进入判断证据',
    nas_evidence: { source: '/nas/prod/prod_staff_owner_openclaw_default/.openclaw/agents/main/sessions/original.jsonl',
      session_id: 'original', delivery_key: request.delivery_key, historical_boundary: { message_id: 'M100', cutoff_at_ms: 10000, end_line: 2 },
      messages: [{ line_no: 3, record: { id: 'future', timestamp: 20000 } }], next_line: 4 } });
  expect(receipt).toMatchObject({ status: 'failed', result: { dispatched: false } });
  expect(JSON.stringify(receipt.result)).not.toContain('"id":"future"');
  noExternalEffects();
});
