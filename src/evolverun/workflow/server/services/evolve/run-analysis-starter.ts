import { randomUUID } from "node:crypto";
import type { IDatabase } from "../../db.js";
import { getClawWebPublicBaseUrl } from "../../env.js";
import type { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import type { EvolveRepository } from "../../repositories/evolve-repository.js";
import { WorkflowEvolutionRepository } from "../../repositories/workflow-evolution-repository.js";
import { dispatchEvolveCommand, type EvolveDispatchInput } from "../evolve-dispatcher.js";
import {
  digestCanonicalJson,
  WORKFLOW_EVOLUTION_ANALYSIS_VERSION,
} from "../evolution/contracts.js";

type Dispatch = (input: EvolveDispatchInput) => Promise<{
  runId: string | null;
  sessionId: string | null;
  platformResponse: unknown;
}>;

export type RunAnalysisStartInput = {
  flowId: string;
  userId: string;
  botId?: string;
  botEnv?: string;
  force?: boolean;
};

export type RunAnalysisStartResult = {
  ok: true;
  analysisId?: string;
  flowId: string;
  taskId: string;
  stepId: string;
  status: string;
  duplicate?: boolean;
  diagnosisCount?: number;
  suggestionCount?: number;
};

export class RunAnalysisStartError extends Error {
  constructor(
    readonly statusCode: number,
    readonly code: string,
    message: string,
    readonly details?: Record<string, unknown>,
  ) {
    super(message);
  }
}

export type RunAnalysisStarter = {
  start(input: RunAnalysisStartInput): Promise<RunAnalysisStartResult>;
};

function timestampMinute(now = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(now);
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}${value("month")}${value("day")}${value("hour")}${value("minute")}`;
}

function taskId(): string {
  return `EV-${timestampMinute()}-${randomUUID().slice(0, 8).toUpperCase()}`;
}

function analysisMessage(analysisId: string, flowId: string): string {
  return [
    "ClawWeb 发起了一个运行日志进化分析任务，请使用你的 workflow_engine_dispatch 工具处理。",
    "",
    "请调用 workflow_engine_dispatch，command 必须精确为：",
    `analyze ${flowId} --analysis-id ${analysisId}`,
    "",
    "分析结果和任务终态由插件通过签名内部 API 自动回写。不要自行 curl/fetch，也不要额外发送 HTTP 报告。",
  ].join("\n");
}

export function createRunAnalysisStarter(input: {
  repo: EvolveRepository;
  db: IDatabase;
  dispatch?: Dispatch;
  publicBaseUrl?: string;
}): RunAnalysisStarter {
  const { repo, db } = input;
  const dispatch = input.dispatch ?? dispatchEvolveCommand;
  const workflowEvolutionRepo = new WorkflowEvolutionRepository(db);

  return {
    async start(request): Promise<RunAnalysisStartResult> {
      const flowId = request.flowId.trim();
      const userId = request.userId.trim();
      if (!flowId) throw new RunAnalysisStartError(400, "INVALID_FLOW_ID", "flowId 为必填项");
      if (!userId) throw new RunAnalysisStartError(401, "UNAUTHENTICATED", "未登录");

      const workflowId = await repo.getWorkflowIdByFlowId(flowId);
      if (!workflowId) throw new RunAnalysisStartError(404, "FLOW_NOT_FOUND", "未找到该 flow 对应的工作流");

      const existingTask = await repo.findRunningRunAnalysisTask(flowId);
      const runAnalysisTimeoutMs = 30 * 60 * 1000;
      const dispatchedStallMs = 5 * 60 * 1000;
      if (existingTask) {
        const createdAt = typeof existingTask.gmt_create === "number"
          ? existingTask.gmt_create * 1000
          : new Date(existingTask.gmt_create).getTime();
        const stuckMs = Date.now() - createdAt;
        const inFlightStatuses = ["created", "dispatching", "pending", "running", "analyzing"];
        if (existingTask.status === "dispatched" && stuckMs < dispatchedStallMs) {
          return { ok: true, flowId, taskId: existingTask.task_id, stepId: existingTask.step_id, status: existingTask.status, duplicate: true };
        }
        if (inFlightStatuses.includes(existingTask.status) && stuckMs < runAnalysisTimeoutMs) {
          return { ok: true, flowId, taskId: existingTask.task_id, stepId: existingTask.step_id, status: existingTask.status, duplicate: true };
        }
        await repo.updateStepStatus(existingTask.step_id, {
          status: "failed",
          errorCode: "RUN_ANALYSIS_TIMEOUT",
          errorMessage: stuckMs >= runAnalysisTimeoutMs
            ? "Analysis task timed out; user re-triggered"
            : "User re-triggered analysis; previous dispatched task canceled",
        });
      }

      const run = await repo.getFlowRun(flowId);
      const originBotId = run?.origin_bot_id ? String(run.origin_bot_id).split(":")[0].trim() : null;
      let botId = request.botId?.trim() ?? "";
      let botEnv = request.botEnv;
      const eligible = await repo.listEligibleBotsForAnalyze(userId, workflowId);
      const isEligible = (id: string, env?: string) => eligible.some((bot) => bot.botId === id && (env == null || bot.env === env));

      if (!botId && originBotId && isEligible(originBotId)) {
        botId = originBotId;
        botEnv = undefined;
      }
      if (!botId) {
        for (const bot of eligible) {
          const candidate = await repo.resolveEvolveBotRuntime(userId, bot.botId, bot.env ?? undefined);
          if (candidate && (!candidate.activeEngine || candidate.activeEngine.toLowerCase() === "openclaw")) {
            botId = bot.botId;
            botEnv = bot.env ?? undefined;
            break;
          }
        }
      }
      if (!botId) {
        throw new RunAnalysisStartError(400, "BOT_REQUIRED", "botId 为必填项，且未找到可用的 OpenClaw Bot");
      }
      if (!isEligible(botId, botEnv)) {
        throw new RunAnalysisStartError(403, "BOT_FORBIDDEN", "所选 Bot 没有该 workflow 的分析/执行权限");
      }
      const runtime = await repo.resolveEvolveBotRuntime(userId, botId, botEnv);
      if (!runtime) throw new RunAnalysisStartError(404, "BOT_RUNTIME_NOT_FOUND", "无法解析 Bot 运行时");
      if (runtime.activeEngine && runtime.activeEngine.toLowerCase() !== "openclaw") {
        throw new RunAnalysisStartError(422, "EVOLVE_ENGINE_UNSUPPORTED", `当前分析任务仅支持 OpenClaw 引擎，所选 Bot 为 ${runtime.activeEngine}`, {
          activeEngine: runtime.activeEngine,
        });
      }

      const nextTaskId = taskId();
      const stepId = `${nextTaskId}-step-analyze`;
      const analysisId = `AN-${randomUUID().replaceAll("-", "").slice(0, 20).toUpperCase()}`;
      const force = request.force === true;
      const started = await repo.startFlowAnalysis(flowId);
      if (!started) {
        const latest = await repo.findRunningRunAnalysisTask(flowId);
        if (latest) {
          return { ok: true, flowId, taskId: latest.task_id, stepId: latest.step_id, status: latest.status, duplicate: true };
        }
        throw new RunAnalysisStartError(409, "ANALYSIS_STATE_CONFLICT", "该运行正在分析中或已分析完成");
      }

      try {
        await workflowEvolutionRepo.createAnalysisRun({
          analysisId,
          requestKey: digestCanonicalJson({ analysisId, flowId, workflowId }),
          scopeType: "single_run",
          scope: { flowIds: [flowId] },
          flowId,
          workflowId,
          analysisVersion: WORKFLOW_EVOLUTION_ANALYSIS_VERSION,
          requestedBy: userId,
          requestedAtMs: Date.now(),
          taskId: nextTaskId,
          stepId,
        });
      } catch (error) {
        await repo.failFlowAnalysis(flowId);
        throw new RunAnalysisStartError(503, "ANALYSIS_STORAGE_UNAVAILABLE", error instanceof Error ? error.message : String(error));
      }

      await repo.createTaskWithStep({
        task: {
          taskId: nextTaskId,
          taskType: "run_analysis",
          userId,
          botId,
          taskName: `运行日志分析：${flowId}`,
          configJson: JSON.stringify({ analysisId, flowId, workflowId, botId, botEnv: botEnv ?? null, force }),
          createdBy: userId,
        },
        step: { stepId, stepType: "run_analysis", stepNo: 1, command: `[run-analysis] ${flowId}` },
      });

      try {
        const callbackUrl = `${input.publicBaseUrl ?? getClawWebPublicBaseUrl()}/api/evolve/internal/tasks/${encodeURIComponent(nextTaskId)}/steps/${encodeURIComponent(stepId)}/bot-callback`;
        const dispatched = await dispatch({
          taskId: nextTaskId,
          stepPk: 0,
          stepId,
          stepType: "run_analysis",
          userId,
          botId,
          command: analysisMessage(analysisId, flowId),
          mode: "message",
          callbackUrl,
          runtime,
          forceMessage: true,
        });
        await repo.markDispatched(stepId, dispatched.runId, dispatched.sessionId, dispatched.platformResponse);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        await repo.updateStepStatus(stepId, { status: "failed", errorCode: "DISPATCH_FAILED", errorMessage: message });
        await repo.failFlowAnalysis(flowId);
        await workflowEvolutionRepo.failAnalysisRun(analysisId, "DISPATCH_FAILED", Date.now()).catch(() => undefined);
        throw new RunAnalysisStartError(502, "DISPATCH_FAILED", message);
      }

      return {
        ok: true,
        analysisId,
        flowId,
        taskId: nextTaskId,
        stepId,
        status: "analyzing",
        diagnosisCount: await repo.countDiagnosesByFlow(flowId),
        suggestionCount: 0,
      };
    },
  };
}

export type RunTerminalObserver = (input: { flowId: string; status: string }) => void | Promise<void>;

function enabledWorkflowSet(value: string | string[] | undefined): Set<string> {
  const items = Array.isArray(value) ? value : String(value ?? "").split(",");
  return new Set(items.map((item) => item.trim()).filter(Boolean));
}

export function createFailedRunAutoAnalysisObserver(input: {
  flowRunRepo: Pick<FlowRunRepository, "findByFlowId">;
  starter: RunAnalysisStarter;
  enabledWorkflows?: string | string[];
  isWorkflowEnabled?: (workflowId: string) => Promise<boolean>;
}): RunTerminalObserver {
  const enabled = enabledWorkflowSet(input.enabledWorkflows ?? process.env.TASK_GUARD_AUTO_ANALYZE_FAILED_WORKFLOWS);
  return async ({ flowId, status }) => {
    if (status !== "failed") return;
    const run = await input.flowRunRepo.findByFlowId(flowId);
    if (!run) return;
    const isEnabled = input.isWorkflowEnabled
      ? await input.isWorkflowEnabled(run.workflow_id)
      : enabled.has("*") || enabled.has(run.workflow_id);
    if (!isEnabled) return;
    const originParts = String(run.origin_bot_id ?? "").split(":");
    const userId = String(run.user_id ?? originParts[1] ?? "").trim();
    if (!userId) {
      console.warn(`[task-guard][auto-analysis] skipped flow=${flowId}: run user is missing`);
      return;
    }
    try {
      await input.starter.start({ flowId, userId });
    } catch (error) {
      console.warn(`[task-guard][auto-analysis] failed flow=${flowId}: ${error instanceof Error ? error.message : String(error)}`);
    }
  };
}
