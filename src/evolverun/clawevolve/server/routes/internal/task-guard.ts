import { createHash, timingSafeEqual } from "node:crypto";
import { Router, type Request, type Response } from "express";
import type { EvolveRepository } from "../../repositories/evolve-repository.js";

const PHASES = [
  "task_received",
  "reading_workflow",
  "planning_change",
  "editing_workflow",
  "deploying",
] as const;

type SuggestionApplyPhase = typeof PHASES[number];

const PHASE_MESSAGES: Record<SuggestionApplyPhase, string> = {
  task_received: "任务已派发，等待 Bot 开始处理",
  reading_workflow: "正在读取完整 Workflow",
  planning_change: "正在结合建议生成修改方案",
  editing_workflow: "正在修改 Workflow",
  deploying: "正在部署 Workflow",
};

function timestampMs(value: number | string): number {
  if (typeof value === "number" && Number.isFinite(value)) return value < 1e12 ? value * 1000 : value;
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric > 0) return numeric < 1e12 ? numeric * 1000 : numeric;
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : Date.now();
}

export function createInternalTaskGuardRouter(repo: EvolveRepository | null): Router {
  const router = Router();

  router.post("/suggestion-applications/:taskId/steps/:stepId/claim", async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "task_guard_repository_unavailable" }); return; }
    const taskId = String(req.params.taskId ?? "").trim();
    const stepId = String(req.params.stepId ?? "").trim();
    const botId = String(req.body?.botId ?? "").trim();
    const claimToken = String(req.body?.claimToken ?? "").trim();
    const [task, step] = await Promise.all([repo.findTask(taskId), repo.findStep(stepId)]);
    if (!task || !step || step.task_id !== taskId) {
      res.status(404).json({ error: "suggestion_application_not_found" }); return;
    }
    if (task.task_type !== "suggestion_apply" || step.step_type !== "suggestion_apply") {
      res.status(409).json({ error: "not_suggestion_application" }); return;
    }
    if (!botId || task.bot_id !== botId) {
      res.status(403).json({ error: "suggestion_application_bot_mismatch" }); return;
    }
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(task.config_json) as Record<string, unknown>;
    } catch {
      res.status(409).json({ error: "suggestion_application_config_invalid" }); return;
    }
    const expectedDigest = String(config.claimTokenDigest ?? "");
    const actualDigest = createHash("sha256").update(claimToken).digest("hex");
    const expected = Buffer.from(expectedDigest, "utf8");
    const actual = Buffer.from(actualDigest, "utf8");
    if (!claimToken || expected.length !== actual.length || !timingSafeEqual(expected, actual)) {
      res.status(403).json({ error: "suggestion_application_claim_invalid" }); return;
    }
    const input = config.applicationInput as Record<string, unknown> | undefined;
    if (!input || typeof input.workflowId !== "string" || typeof input.spec !== "string" || typeof input.deploy !== "boolean") {
      res.status(409).json({ error: "suggestion_application_input_invalid" }); return;
    }
    if (!await repo.claimSuggestionApplyStep(taskId, stepId)) {
      res.status(409).json({ error: "suggestion_application_already_claimed" }); return;
    }
    res.json({ ok: true, input });
  });

  router.post("/suggestion-applications/:taskId/steps/:stepId/report", async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "task_guard_repository_unavailable" }); return; }
    const taskId = String(req.params.taskId ?? "").trim();
    const stepId = String(req.params.stepId ?? "").trim();
    const botId = String(req.body?.botId ?? "").trim();
    const claimToken = String(req.body?.claimToken ?? "").trim();
    const status = String(req.body?.status ?? "").trim();
    if (status !== "succeeded" && status !== "failed") {
      res.status(400).json({ error: "invalid_suggestion_application_status" }); return;
    }
    const [task, step] = await Promise.all([repo.findTask(taskId), repo.findStep(stepId)]);
    if (!task || !step || step.task_id !== taskId) {
      res.status(404).json({ error: "suggestion_application_not_found" }); return;
    }
    if (task.task_type !== "suggestion_apply" || step.step_type !== "suggestion_apply") {
      res.status(409).json({ error: "not_suggestion_application" }); return;
    }
    if (!botId || task.bot_id !== botId) {
      res.status(403).json({ error: "suggestion_application_bot_mismatch" }); return;
    }
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(task.config_json) as Record<string, unknown>;
    } catch {
      res.status(409).json({ error: "suggestion_application_config_invalid" }); return;
    }
    const expectedDigest = String(config.claimTokenDigest ?? "");
    const actualDigest = createHash("sha256").update(claimToken).digest("hex");
    const expected = Buffer.from(expectedDigest, "utf8");
    const actual = Buffer.from(actualDigest, "utf8");
    if (!claimToken || expected.length !== actual.length || !timingSafeEqual(expected, actual)) {
      res.status(403).json({ error: "suggestion_application_claim_invalid" }); return;
    }
    if (["succeeded", "failed", "canceled"].includes(step.status)) {
      res.json({ ok: true, duplicate: true, status: step.status }); return;
    }
    if (step.status !== "running") {
      res.status(409).json({ error: "suggestion_application_not_claimed" }); return;
    }

    const succeeded = status === "succeeded";
    const summary = String(req.body?.summary ?? (succeeded ? "Workflow 修复已完成" : "Workflow 修复失败"));
    const output = req.body?.output && typeof req.body.output === "object"
      ? req.body.output as Record<string, unknown>
      : undefined;
    const error = req.body?.error && typeof req.body.error === "object"
      ? req.body.error as Record<string, unknown>
      : undefined;
    const settled = await repo.trySettleSuggestionApplyStep(stepId, {
      status,
      summary,
      ...(output ? { output } : {}),
      ...(succeeded ? {} : {
        errorCode: String(error?.code ?? "SUGGESTION_APPLY_FAILED"),
        errorMessage: String(error?.message ?? summary),
        retryable: error?.retryable === true,
      }),
    });
    if (!settled) {
      const current = await repo.findStep(stepId);
      res.json({ ok: true, duplicate: true, status: current?.status ?? step.status });
      return;
    }

    const suggestionIds = Array.isArray(config.suggestionIds)
      ? config.suggestionIds.map(String).filter(Boolean)
      : [String(config.suggestionId ?? "")].filter(Boolean);
    const workflowId = String(config.workflowId ?? "");
    const revisions = Array.isArray(config.suggestionRevisions)
      ? config.suggestionRevisions.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
      : [];
    const supersededSuggestionIds: string[] = [];
    for (const suggestionId of suggestionIds) {
      const current = await repo.findSuggestionById(suggestionId);
      const revision = revisions.find((item) => String(item.suggestionId ?? "") === suggestionId);
      const expectedProposalDigest = typeof revision?.proposalDigest === "string" ? revision.proposalDigest : null;
      const superseded = succeeded && expectedProposalDigest !== (current?.proposal_digest ?? null);
      if (superseded) supersededSuggestionIds.push(suggestionId);
      const suggestion = succeeded
        ? superseded
          ? current
          : await repo.markSuggestionAppliedUnverified(suggestionId, { actor: botId, note: summary })
        : await repo.updateSuggestionStatus(suggestionId, "failed", {
          action: "failed", actor: botId, note: summary, timestamp: new Date().toISOString(),
        });
      await repo.recordSuggestionOutcome({
        suggestionId,
        workflowId,
        nodeId: suggestion?.node_id ?? null,
        action: "suggestion_apply",
        applied: succeeded && !superseded,
        succeeded,
        verdict: succeeded
          ? superseded ? "application_succeeded_superseded" : "application_succeeded"
          : "application_failed",
        note: summary,
        sourceTaskId: taskId,
        sourceStepId: stepId,
        createdBy: botId,
      });
    }
    if (succeeded) await repo.completeTask(taskId);
    res.json({ ok: true, duplicate: false, status, suggestionIds, supersededSuggestionIds });
  });

  router.post("/suggestion-applications/:taskId/steps/:stepId/progress", async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "task_guard_repository_unavailable" }); return; }
    const taskId = String(req.params.taskId ?? "").trim();
    const stepId = String(req.params.stepId ?? "").trim();
    const botId = String(req.body?.botId ?? "").trim();
    const claimToken = String(req.body?.claimToken ?? "").trim();
    const phase = String(req.body?.phase ?? "").trim() as SuggestionApplyPhase;
    const [task, step] = await Promise.all([repo.findTask(taskId), repo.findStep(stepId)]);
    if (!task || !step || step.task_id !== taskId) {
      res.status(404).json({ error: "suggestion_application_not_found" }); return;
    }
    if (task.task_type !== "suggestion_apply" || step.step_type !== "suggestion_apply") {
      res.status(409).json({ error: "not_suggestion_application" }); return;
    }
    if (!botId || task.bot_id !== botId) {
      res.status(403).json({ error: "suggestion_application_bot_mismatch" }); return;
    }
    let config: Record<string, unknown>;
    try {
      config = JSON.parse(task.config_json) as Record<string, unknown>;
    } catch {
      res.status(409).json({ error: "suggestion_application_config_invalid" }); return;
    }
    const expectedDigest = String(config.claimTokenDigest ?? "");
    const actualDigest = createHash("sha256").update(claimToken).digest("hex");
    const expected = Buffer.from(expectedDigest, "utf8");
    const actual = Buffer.from(actualDigest, "utf8");
    if (!claimToken || expected.length !== actual.length || !timingSafeEqual(expected, actual)) {
      res.status(403).json({ error: "suggestion_application_claim_invalid" }); return;
    }
    if (!PHASES.includes(phase) || phase === "task_received") {
      res.status(400).json({ error: "invalid_suggestion_application_phase" }); return;
    }
    if (["succeeded", "failed", "canceled"].includes(step.status)) {
      res.json({ ok: true, ignored: true, terminal: true }); return;
    }
    if (step.status !== "running") {
      res.status(409).json({ error: "suggestion_application_not_claimed" }); return;
    }

    let previousPhase: SuggestionApplyPhase | null = null;
    try {
      const output = JSON.parse(step.output_json ?? "null") as { applicationProgress?: { phase?: unknown } } | null;
      const value = output?.applicationProgress?.phase;
      if (typeof value === "string" && PHASES.includes(value as SuggestionApplyPhase)) {
        previousPhase = value as SuggestionApplyPhase;
      }
    } catch { /* malformed historical output is replaced by the current safe progress */ }
    if (previousPhase && PHASES.indexOf(previousPhase) > PHASES.indexOf(phase)) {
      res.json({ ok: true, ignored: true, phase: previousPhase }); return;
    }

    const updatedAtMs = Date.now();
    const progress = {
      phase,
      message: PHASE_MESSAGES[phase],
      elapsedMs: Math.max(0, updatedAtMs - timestampMs(task.gmt_create)),
      updatedAtMs,
    };
    if (!await repo.tryUpdateSuggestionApplyProgress(stepId, progress.message, { applicationProgress: progress })) {
      res.json({ ok: true, ignored: true, terminal: true });
      return;
    }
    res.json({ ok: true, progress });
  });

  return router;
}
