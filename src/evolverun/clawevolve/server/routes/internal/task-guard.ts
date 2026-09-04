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

type SuggestionApplyProgressHistoryItem = {
  phase: SuggestionApplyPhase;
  message: string;
  updatedAtMs: number;
};

function sanitizeProgressMessage(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const message = value.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
  return message ? message.slice(0, 160) : fallback;
}

function readApplicationProgress(outputJson: string | null): {
  phase: SuggestionApplyPhase | null;
  history: SuggestionApplyProgressHistoryItem[];
  value?: Record<string, unknown>;
} {
  try {
    const output = JSON.parse(outputJson ?? "null") as { applicationProgress?: unknown } | null;
    const rawValue = output?.applicationProgress;
    if (!rawValue || typeof rawValue !== "object" || Array.isArray(rawValue)) return { phase: null, history: [] };
    const progress = rawValue as Record<string, unknown>;
    const phase = typeof progress.phase === "string" && PHASES.includes(progress.phase as SuggestionApplyPhase)
      ? progress.phase as SuggestionApplyPhase
      : null;
    let history = Array.isArray(progress.history)
      ? progress.history.flatMap((entry): SuggestionApplyProgressHistoryItem[] => {
        if (!entry || typeof entry !== "object" || Array.isArray(entry)) return [];
        const item = entry as Record<string, unknown>;
        if (typeof item.phase !== "string" || !PHASES.includes(item.phase as SuggestionApplyPhase)
          || typeof item.message !== "string" || !Number.isFinite(Number(item.updatedAtMs))) return [];
        return [{
          phase: item.phase as SuggestionApplyPhase,
          message: sanitizeProgressMessage(item.message, PHASE_MESSAGES[item.phase as SuggestionApplyPhase]),
          updatedAtMs: Number(item.updatedAtMs),
        }];
      }).slice(-10)
      : [];
    const message = phase && typeof progress.message === "string"
      ? sanitizeProgressMessage(progress.message, PHASE_MESSAGES[phase])
      : phase ? PHASE_MESSAGES[phase] : null;
    const updatedAtMs = Number(progress.updatedAtMs);
    const elapsedMs = Number(progress.elapsedMs);
    if (phase && message && history.length === 0 && Number.isFinite(updatedAtMs)) {
      history = [{ phase, message, updatedAtMs }];
    }
    const canonicalValue = phase && message && Number.isFinite(updatedAtMs) && Number.isFinite(elapsedMs)
      ? { phase, message, updatedAtMs, elapsedMs, history }
      : undefined;
    return { phase, history, ...(canonicalValue ? { value: canonicalValue } : {}) };
  } catch {
    return { phase: null, history: [] };
  }
}

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
    const previousProgress = readApplicationProgress(step.output_json).value;
    const settledOutput = output || previousProgress
      ? { ...(output ?? {}), ...(previousProgress ? { applicationProgress: previousProgress } : {}) }
      : undefined;
    const suggestionIds = Array.isArray(config.suggestionIds)
      ? config.suggestionIds.map(String).filter(Boolean)
      : [String(config.suggestionId ?? "")].filter(Boolean);
    const workflowId = String(config.workflowId ?? "");
    const revisions = Array.isArray(config.suggestionRevisions)
      ? config.suggestionRevisions.flatMap((item): Array<{ suggestionId: string; proposalDigest: string | null }> => {
        if (!item || typeof item !== "object" || Array.isArray(item)) return [];
        const revision = item as Record<string, unknown>;
        const suggestionId = String(revision.suggestionId ?? "");
        if (!suggestionId) return [];
        return [{
          suggestionId,
          proposalDigest: typeof revision.proposalDigest === "string" ? revision.proposalDigest : null,
        }];
      })
      : [];
    const settlement = await repo.tryFinalizeSuggestionApplication(stepId, {
      source: "callback",
      status,
      summary,
      ...(settledOutput ? { output: settledOutput } : {}),
      ...(succeeded ? {} : {
        errorCode: String(error?.code ?? "SUGGESTION_APPLY_FAILED"),
        errorMessage: String(error?.message ?? summary),
        retryable: error?.retryable === true,
      }),
      suggestionIds,
      workflowId,
      revisions,
      actor: botId,
    });
    if (!settlement.settled) {
      const current = await repo.findStep(stepId);
      res.json({ ok: true, duplicate: true, status: current?.status ?? step.status });
      return;
    }
    res.json({
      ok: true,
      duplicate: false,
      status,
      suggestionIds,
      supersededSuggestionIds: settlement.supersededSuggestionIds,
    });
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

    const previous = readApplicationProgress(step.output_json);
    const previousPhase = previous.phase;
    if (previousPhase && PHASES.indexOf(previousPhase) > PHASES.indexOf(phase)) {
      res.json({ ok: true, ignored: true, phase: previousPhase }); return;
    }

    const updatedAtMs = Date.now();
    const message = sanitizeProgressMessage(req.body?.message, PHASE_MESSAGES[phase]);
    const history = [...previous.history];
    const last = history.at(-1);
    if (last?.phase === phase && last.message === message) {
      history[history.length - 1] = { ...last, updatedAtMs };
    } else {
      history.push({ phase, message, updatedAtMs });
    }
    const progress = {
      phase,
      message,
      elapsedMs: Math.max(0, updatedAtMs - timestampMs(task.gmt_create)),
      updatedAtMs,
      history: history.slice(-10),
    };
    if (!await repo.tryUpdateSuggestionApplyProgress(stepId, progress.message, { applicationProgress: progress })) {
      res.json({ ok: true, ignored: true, terminal: true });
      return;
    }
    res.json({ ok: true, progress });
  });

  return router;
}
