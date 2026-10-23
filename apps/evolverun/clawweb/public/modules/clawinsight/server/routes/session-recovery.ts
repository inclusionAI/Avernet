import { AgentToolCallIdempotencyConflictError, AgentToolCallWorkloadConflictError } from "../repositories/repair/agent-tool-call-record.js";
import { resolveRepairRequestActor } from "../services/repair/common/request-actor.js";
import { parseAgentExecutionTicket } from "../services/repair/common/agent-execution.js";
import { AgentToolError } from "../services/repair/common/agent-tools/errors.js";
import type { RecoveryActor } from "../services/repair/session-recovery/contracts.js";
import { Router, json, type Request } from "express";
import { SessionRecoveryError } from "../services/repair/session-recovery/errors.js";
import { BotRuntimeError } from "@avernet/clawweb-shared/server/services/bot-runtime";
import { redactPersistableText } from "@avernet/clawweb-shared/server/services/redaction";
import type { SessionRecoveryRuntime } from "../services/repair/session-recovery/contracts.js";
export type SessionRecoveryRouterOptions = SessionRecoveryRuntime & {
  resolveActor?: (req: Request) => Promise<RecoveryActor | null>;
};
export function createSessionRecoveryRouter(supplied?: SessionRecoveryRouterOptions): Router {
  const router = Router();
  const runtime = () => {
    const value = supplied;
    if (!value) throw new SessionRecoveryError(503, "recovery_not_ready", "自愈发送服务未配置");
    return value;
  };
  router.post("/internal/monitoring/session-recoveries", json({ limit: "64kb", inflate: false }), async (req, res, next) => {
    try {
      const deps = runtime();
      res.set("Cache-Control", "no-store");
      res.status(202).json(await deps.service.create(req.body, { userId: "session-recovery-service", isAdmin: true }));
    } catch (error) { next(error); }
  });
  const actor = async (req: Request): Promise<RecoveryActor> => {
    const resolved = supplied?.resolveActor ? await supplied.resolveActor(req)
      : await resolveRepairRequestActor(req).then(value => value ? { userId: value.userId, isAdmin: req.isAdmin === true } : null);
    if (!resolved) throw new SessionRecoveryError(401, "recovery_login_required", "需要登录");
    return resolved;
  };
  const tasks = () => {
    if (!supplied?.tasks) throw new SessionRecoveryError(503, "recovery_not_ready", "自愈任务服务未配置");
    return supplied.tasks;
  };
  router.get("/monitoring/session-recoveries", async (req, res, next) => {
    try { res.json({ items: await tasks().list(String(req.query.event_id ?? ""), await actor(req)) }); }
    catch (error) { next(error); }
  });
  router.post("/monitoring/session-recoveries/simulations", json({ limit: "1mb", inflate: false }), async (req, res, next) => {
    try { res.set("Cache-Control", "no-store").status(202).json(await tasks().createSimulations(req.body, await actor(req))); }
    catch (error) { next(error); }
  });
  router.get("/monitoring/session-recoveries/:taskId", async (req, res, next) => {
    try { res.set("Cache-Control", "no-store").json(await tasks().detail(String(req.params.taskId), await actor(req))); }
    catch (error) { next(error); }
  });
  router.patch("/monitoring/session-recoveries/:taskId/share", json({ limit: "8kb", inflate: false }), async (req, res, next) => {
    try { res.set("Cache-Control", "no-store").json(await tasks().setTaskShared(String(req.params.taskId), req.body?.shared, await actor(req))); }
    catch (error) { next(error); }
  });
  router.post("/monitoring/session-recoveries/:taskId/terminate", json({ limit: "8kb", inflate: false }), async (req, res, next) => {
    try { res.set("Cache-Control", "no-store").json(await tasks().terminate(String(req.params.taskId), await actor(req), req.body?.reason)); }
    catch (error) { next(error); }
  });
  router.get("/monitoring/session-recoveries/:taskId/tool-calls/:toolCallId", async (req, res, next) => {
    try {
      const task = await tasks().detail(String(req.params.taskId), await actor(req));
      const call = await worker().toolGet(task, String(req.params.toolCallId));
      res.set("Cache-Control", "no-store").json({ toolCallId: call.callId, status: call.status, request: call.request, result: call.result });
    } catch (error) { next(error); }
  });
  router.post("/monitoring/session-recoveries/:taskId/decision", json({ limit: "64kb", inflate: false }), async (req, res, next) => {
    try {
      if (typeof req.body?.plan_digest !== "string" || typeof req.body?.approved !== "boolean") {
        throw new SessionRecoveryError(422, "invalid_session_recovery", "需要 plan_digest 和 approved");
      }
      await tasks().decide(String(req.params.taskId), req.body.plan_digest, req.body.approved, await actor(req), req.body.cooperation_done === true, req.body.feedback ?? "");
      res.json({ ok: true });
    } catch (error) { next(error); }
  });
  router.post("/monitoring/session-recoveries/:taskId/annotations", json({ limit: "64kb", inflate: false }), async (req, res, next) => {
    try { await tasks().annotate(String(req.params.taskId), await actor(req), req.body); res.json({ ok: true }); }
    catch (error) { next(error); }
  });
  router.post("/internal/monitoring/session-recoveries/:taskId/continue", async (req, res, next) => {
    try {
      await tasks().resume(String(req.params.taskId)); res.status(202).json({ status: "accepted", task_id: String(req.params.taskId) });
    } catch (error) { next(error); }
  });
  const workerPath = "/internal/monitoring/session-recoveries/:taskId/executions/:executionId";
  const worker = () => { if (!supplied?.agent) throw new SessionRecoveryError(503, "recovery_not_ready", "自愈执行服务未配置"); return supplied.agent; };
  const verifyWorker = (req: Request, control = false) => worker().verify(String(req.params.taskId), String(req.params.executionId), parseAgentExecutionTicket(req.header("authorization")), control);
  router.get(`${workerPath}/bootstrap`, async (req, res, next) => {
    try { res.set("Cache-Control", "no-store").json(await worker().bootstrap(await verifyWorker(req, true))); } catch (error) { next(error); }
  });
  router.post(`${workerPath}/heartbeat`, json({ limit: "8kb", inflate: false }), async (req, res, next) => {
    try { res.json(await worker().heartbeat(await verifyWorker(req, true), req.body?.ccSessionId)); } catch (error) { next(error); }
  });
  const toolRoutes = {
    "/tools/session/read": "sessionRead", "/tools/session/trajectories": "trajectoryRead", "/tools/runtime/inspect": "inspect", "/tools/logs/search": "logs",
    "/tools/apply-action": "apply", "/tools/retry-message": "retryMessage", "/tools/record-conclusion": "conclusion", "/tools/reconcile-write": "reconcileWrite", "/report": "report",
  } as const;
  for (const [path, method] of Object.entries(toolRoutes)) router.post(`${workerPath}${path}`, json({ limit: "128kb", inflate: false }), async (req, res, next) => {
    try {
      if (!req.body || typeof req.body !== "object" || Array.isArray(req.body)) throw new SessionRecoveryError(422, "invalid_session_recovery", "需要 JSON 对象");
      res.json(await worker()[method as typeof toolRoutes[keyof typeof toolRoutes]](await verifyWorker(req), req.body));
    } catch (error) { next(error); }
  });
  router.get(`${workerPath}/tools/tool-calls/:toolCallId`, async (req, res, next) => {
    try { res.json(await worker().toolGet(await verifyWorker(req), String(req.params.toolCallId))); } catch (error) { next(error); }
  });
  router.use((error: unknown, req: Request, res: import("express").Response, next: import("express").NextFunction) => {
    if (!/^\/(?:internal\/)?monitoring\/session-recoveries(?:\/|$)/.test(req.path)) { next(error); return; }
    const failure = error instanceof SessionRecoveryError || error instanceof BotRuntimeError || error instanceof AgentToolError ? error
      : error instanceof AgentToolCallIdempotencyConflictError ? new SessionRecoveryError(409, "recovery_request_conflict", "同一工具请求标识不能用于不同内容")
      : error instanceof AgentToolCallWorkloadConflictError ? new SessionRecoveryError(409, "recovery_execution_conflict", "执行授权已变化，不能继续当前操作")
      : (error as { type?: string })?.type === "entity.too.large" ? new SessionRecoveryError(413, "invalid_session_recovery", "请求过大")
      : (error as { type?: string })?.type === "entity.parse.failed" ? new SessionRecoveryError(422, "invalid_session_recovery", "JSON 格式无效")
      : new SessionRecoveryError(503, "recovery_unavailable", "自愈发送服务暂不可用；发送结果可能未知");
    if (failure.status >= 500) console.error("[session-recovery] request failed", JSON.stringify({
      method: req.method, path: redactPersistableText(req.path, 1024),
      error: redactPersistableText(error instanceof Error ? error.stack ?? error.message : String(error), 4096),
    }));
    res.status(failure.status).json({ error: failure.code, message: failure.message });
  });
  return router;
}
