import { createHash, randomBytes, randomUUID } from "node:crypto";
import { Router, type Request, type Response } from "express";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";import { getClawWebPublicBaseUrl } from "../env.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { EvolveBotRuntime, EvolvePackRow, EvolveStepRow, EvolveRepository, EvolveSuggestionRow, EvolveTaskRow } from "../repositories/evolve-repository.js";
import type { BenchDomainRepository } from "../repositories/bench-domain-repository.js";
import type { BenchTemplateRepository } from "../repositories/bench-template-repository.js";
import type { BenchRunRepository } from "../repositories/bench-run-repository.js";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { WorkflowEvolutionRepository } from "../repositories/workflow-evolution-repository.js";
import {
  digestCanonicalJson,
  validateWorkflowEvolutionAnalysisResult,
  validateWorkflowPatchProposal,
  WORKFLOW_EVOLUTION_ANALYSIS_VERSION,
} from "../services/evolution/contracts.js";
import { presentEvidence } from "../services/evolution/evidence-presentation.js";
import { requireWorkflowAccess } from "@avernet/clawweb-shared/server/services/workflow-access";
import type {
  InsightImprovementPort,
  InsightTaskCreatorPort,
  InsightTaskSourcePort,
} from "../internal/module-api.js";
import {
  cancelEvolveExecution,
  dispatchEvolveCommand,
  dispatchEvolveTaskLogArchive,
  parseArcaRunnerCallback,
  resolveEvolveTransport,
} from "../services/evolve-dispatcher.js";
import {
  parseNodeCommandYamls,
  normalizeDiagnoseIntent,
  normalizeEvolutionGoal,
  quoteCommandArgument,
  readDiagnoseJudgeBackend,
  readNodeCommandOption,
  renderCommand,
  resolveDiagnoseJudgeBackend,
  resolveOpenClawExecutionMode,
  withoutDiagnoseApiKey,
  type NodeCommandYamls,
} from "../services/evolve/command.js";
import {
  EVOLVE_NODE_REGISTRY, EVOLVE_TASK_REGISTRY, defaultNodeCommand,
  taskNodeKeys,
} from "../services/evolve/task-registry.js";
import { parseEvolveArtifactRef, validatePackArtifact } from "../services/evolve/artifact-ref.js";
import {
  EVOLVE_ARTIFACT_URL_TTL_SECONDS,
  objectKeyFromFrozenPack,
  restoreManifestLocation,
  uploadArtifactLocation,
  taskLogArchiveLocation,
} from "../services/evolve/artifact-url.js";
import { getArtifactBucket, UnavailableObjectStore, type ObjectStore } from "../services/object-storage/oss-object-store.js";

type InsightBoundaryError = Error & {
  code: string;
  category?: "validation" | "auth" | "forbidden" | "not_found" | "conflict" | "source";
  stage?: string;
  retryable?: boolean;
};

function isInsightBoundaryError(error: unknown): error is InsightBoundaryError {
  return error instanceof Error && typeof (error as { code?: unknown }).code === "string";
}

function textOrNull(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  const s = String(value).trim();
  return s.length > 0 ? s : null;
}
import { createEvolveKnowledgeRouter } from "./evolve-knowledge.js";
import {
  dispatchPendingBusinessStep,
  startInitialEvolveStep,
} from "../services/evolve/task-start.js";
import {
  createRunAnalysisStarter,
  RunAnalysisStartError,
  type RunAnalysisStarter,
} from "../services/evolve/run-analysis-starter.js";

type Dispatch = typeof dispatchEvolveCommand;
type DispatchTaskLogArchive = typeof dispatchEvolveTaskLogArchive;
type CancelExecution = typeof cancelEvolveExecution;
export type EvolveRouterDeps = {
  db?: IDatabase;
  dispatch?: Dispatch;
  dispatchTaskLogArchive?: DispatchTaskLogArchive;
  cancelExecution?: CancelExecution;
  improvementRepo?: InsightImprovementPort | null;
  taskSourceService?: InsightTaskSourcePort | null;
  insightTaskService?: InsightTaskCreatorPort | null;
  /** @deprecated ClawInsight owns auto-repair composition. */
  autoRepairRepo?: unknown;
  /** @deprecated ClawInsight owns governance composition. */
  ruleProvider?: unknown;
  benchDomainRepo?: BenchDomainRepository | null;
  benchTemplateRepo?: BenchTemplateRepository | null;
  benchRunRepo?: BenchRunRepository | null;
  artifactStore?: ObjectStore;
  /** Backward-compatible signing-only dependency used by an embedding host. */
  artifactUrlStore?: Pick<ObjectStore, "createSignedUrl">;
  botWorkflowPermissionRepo?: BotWorkflowPermissionRepository | null;
  runAnalysisStarter?: RunAnalysisStarter | null;
};
type BenchDomains = { trainBenchDomainId: string; testBenchDomainId: string };
const DIAGNOSE_MODELS = new Set(["GLM-5.1", "GLM-5.2"]);

const TERMINAL_STATUSES = new Set(["succeeded", "failed", "canceled"]);
const ALLOWED_STATUSES = new Set(["running", ...TERMINAL_STATUSES]);

async function rejectUnsupportedBotEngine(
  repo: EvolveRepository,
  res: Response,
  userId: string,
  botId: string,
  botEnv?: string,
): Promise<boolean> {
  const runtime = await repo.resolveEvolveBotRuntime(userId, botId, botEnv);
  if (runtime?.activeEngine && runtime.activeEngine.toLowerCase() !== "openclaw") {
    res.status(422).json({
      code: "EVOLVE_ENGINE_UNSUPPORTED",
      error: `当前进化流程仅支持 OpenClaw 引擎，所选 Bot 为 ${runtime.activeEngine}`,
      activeEngine: runtime.activeEngine,
    });
    return true;
  }
  return false;
}
function taskBotEnv(task: { config_json: string }): string {
  const config = parseJson(task.config_json);
  return isRecord(config) ? String(config.botEnv ?? "") : "";
}

function timestampMinute(now = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(now);
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}${value("month")}${value("day")}${value("hour")}${value("minute")}`;
}
function id(prefix: string): string {
  return `${prefix}-${timestampMinute()}-${randomUUID().slice(0, 8).toUpperCase()}`;
}
function evolveTaskId(): string {
  return `EV-${timestampMinute()}-${randomUUID().slice(0, 8).toUpperCase()}`;
}
async function registerStepPacks(
  repo: EvolveRepository,
  task: EvolveTaskRow,
  step: EvolveStepRow,
  output: unknown,
): Promise<void> {
  if (!isRecord(output)) return;
  const candidates = [
    { value: output.pack, kind: task.task_type === "pack" ? "snapshot" as const : "round" as const,
      round: task.task_type === "pack" ? 0 : Number(step.round_no ?? 0) },
    { value: output.baselineArtifact, kind: "baseline" as const, round: 0 },
  ];
  for (const candidate of candidates) {
    if (!isRecord(candidate.value) || candidate.value.status !== "available" || !isRecord(candidate.value.artifact)) continue;
    try {
      // baselineArtifact is the immutable task-initial Pack only when it is
      // reported by an Optimize Round 1 Step. Later rounds carry their current
      // Baseline as provenance and must never backfill a missing Initial Pack.
      if (candidate.kind === "baseline"
        && (step.step_type !== "optimize" || Number(step.round_no ?? 0) !== 1)) {
        continue;
      }
      const artifact = validatePackArtifact(candidate.value.artifact);
      if (candidate.kind === "baseline") {
        const existing = (await repo.listPacks(task.user_id, task.bot_id)).find((pack) =>
          pack.source_task_id === task.task_id && pack.source_kind === "baseline");
        // Later rounds report their current accepted Baseline as provenance.
        // Once the immutable task-initial Pack exists, it must not be compared
        // with or overwrite that initial version.
        if (existing) {
          continue;
        }
      }
      await repo.registerPack({
        pack_id: id("PACK"), user_id: task.user_id, bot_id: task.bot_id,
        source_task_id: task.task_id, source_step_id: step.step_id,
        source_kind: candidate.kind, source_round: candidate.round,
        artifact_ref: String(artifact.ref), artifact_size: Number(artifact.size),
        artifact_sha256: String(artifact.sha256), artifact_content_type: String(artifact.contentType),
      });
    } catch (error) {
      // Pack registry is a control-plane index. A single candidate registry
      // failure must not prevent another valid artifact from being recorded or
      // reverse an already completed Bot-side Optimize result.
      console.warn("[clawweb][evolve] Pack registration warning", {
        taskId: task.task_id, stepId: step.step_id, kind: candidate.kind,
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
}
function parseJson(value: string | null): unknown {
  if (!value) return null;
  try { return JSON.parse(value); } catch { return null; }
}
function validateSpec(spec: unknown): string | null {
  if (!spec || typeof spec !== "object" || Array.isArray(spec)) {
    return "spec 必须是包含 version、content_type 和 content 的 JSON 对象";
  }
  const value = spec as Record<string, unknown>;
  if (typeof value.version !== "string" || !value.version.trim()) {
    return "spec.version 必须是非空字符串，例如 v0";
  }
  if (value.content_type !== "text") {
    return "spec.content_type 当前只支持 text";
  }
  if (typeof value.content !== "string" || !value.content.trim()) {
    return "content_type=text 时 spec.content 必须是非空字符串";
  }
  return null;
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
function containsKey(value: unknown, keys: Set<string>): boolean {
  if (Array.isArray(value)) return value.some((item) => containsKey(item, keys));
  if (!isRecord(value)) return false;
  return Object.entries(value).some(([key, child]) => keys.has(key) || containsKey(child, keys));
}
function nonEmptyString(value: unknown): boolean {
  return typeof value === "string" && Boolean(value.trim());
}
function dbText(value: unknown): string {
  if (value == null) return "";
  if (Buffer.isBuffer(value)) return value.toString("utf8");
  if (typeof value === "object") {
    const serialized = value as { type?: unknown; data?: unknown };
    if (serialized.type === "Buffer" && Array.isArray(serialized.data)) {
      return Buffer.from(serialized.data as number[]).toString("utf8");
    }
  }
  return String(value);
}
function safeBenchCommandValue(name: string, value: unknown): string {
  const text = String(value ?? "").trim();
  if (!/^[A-Za-z0-9._/@:+-]{1,255}$/.test(text)) {
    throw new Error(`${name} 包含不支持的命令参数字符`);
  }
  return text;
}
function validateDiagnoseCases(cases: unknown): string | null {
  if (!isRecord(cases)) return "Diagnose Output cases 必须是 JSON 对象";
  if (cases.benchTemplate != null) return "Diagnose 阶段尚未产生 Bench，不允许上报 cases.benchTemplate";
  if (!Array.isArray(cases.items)) return "Diagnose Output cases.items 必须是数组";
  for (const item of cases.items) {
    if (!isRecord(item) || !nonEmptyString(item.caseId)) {
      return "Diagnose Output cases.items[].caseId 必须是非空诊断 Case ID";
    }
    if (item.type !== "good" && item.type !== "bad") {
      return "Diagnose Output cases.items[].type 只能是 good 或 bad";
    }
    if (item.benchRef != null) {
      return "Diagnose 阶段尚未产生 Bench，不允许上报 cases.items[].benchRef";
    }
  }
  if (containsKey(cases, new Set(["benchRunId", "resultId"]))) {
    return "Diagnose 阶段尚未产生 Bench，不允许上报 benchRunId 或 resultId";
  }
  return null;
}
function validatePlanBenchCases(benchCases: unknown): string | null {
  if (!isRecord(benchCases) || !Array.isArray(benchCases.items)) {
    return "Plan Output benchCases.items 必须是数组";
  }
  for (const item of benchCases.items) {
    if (!isRecord(item) || !nonEmptyString(item.sourceCaseId) || !nonEmptyString(item.taskId)) {
      return "Plan Output benchCases.items[] 必须包含 sourceCaseId 和 taskId";
    }
    if (item.split !== "train" && item.split !== "test") {
      return "Plan Output benchCases.items[].split 只能是 train 或 test";
    }
    const template = item.template;
    if (!isRecord(template) || !nonEmptyString(template.ownerUserId)
      || !nonEmptyString(template.domainId) || !nonEmptyString(template.templateName)) {
      return "Plan Output benchCases.items[].template 必须包含 ownerUserId、domainId 和 templateName";
    }
  }
  if (containsKey(benchCases, new Set(["benchRunId", "resultId"]))) {
    return "Plan 只处理 Bench 模板，不允许上报 benchRunId 或 resultId";
  }
  return null;
}
function validateBaselineRole(role: "train" | "test", value: unknown): string | null {
  if (!isRecord(value) || value.role !== role || !nonEmptyString(value.producerStepId)
    || !nonEmptyString(value.ownerUserId) || !nonEmptyString(value.domainId)
    || !nonEmptyString(value.benchRunId) || !isRecord(value.metrics)) {
    return `${role} Baseline 必须包含 role、producerStepId、ownerUserId、domainId、benchRunId 和 metrics`;
  }
  if (value.source !== "generated" && value.source !== "reused") {
    return `${role} Baseline source 只能是 generated 或 reused`;
  }
  return null;
}
function validateBaseline(value: unknown): string | null {
  if (!isRecord(value)) return "Baseline 必须是 JSON 对象";
  return validateBaselineRole("train", value.train) || validateBaselineRole("test", value.test);
}
function validateStepOutput(stepType: string, output: unknown): string | null {
  if (!output || typeof output !== "object" || Array.isArray(output)) return "succeeded 必须携带 JSON 对象 output";
  const value = output as Record<string, unknown>;
  if (stepType === "skill_init") {
    if (value.schemaVersion !== "clawevolve.skill-init.v1"
      || !new Set(["installed", "unchanged"]).has(String(value.result))
      || !/^[A-Za-z0-9._-]{1,128}$/.test(String(value.releaseVersion ?? ""))
      || value.user !== "admin"
      || value.transport !== "arca_message_exec") {
      return "Skill 初始化 Output 必须符合 clawevolve.skill-init.v1 契约";
    }
  }
  if (stepType === "diagnose") {
    if (!value.diagnosis || !value.cases) return "Diagnose Output 必须包含 diagnosis 和 cases";
    const casesError = validateDiagnoseCases(value.cases);
    if (casesError) return casesError;
  }
  if (stepType === "plan") {
    const domains = value.benchDomains as Record<string, unknown> | undefined;
    if (!value.goal || !value.spec || !value.benchCases
      || !domains?.trainBenchDomainId || !domains?.testBenchDomainId) {
      return "Plan Output 必须包含 goal、spec、benchCases 和完整 benchDomains";
    }
    const specError = validateSpec(value.spec);
    if (specError) return `Plan Output ${specError}`;
    const benchCasesError = validatePlanBenchCases(value.benchCases);
    if (benchCasesError) return benchCasesError;
  }
  if (stepType === "optimize") {
    if (!value.diff || !Array.isArray(value.metrics) || !value.roundDecision || !value.baseline) {
      return "Optimize Output 必须包含 diff、metrics、baseline 和 roundDecision";
    }
    const baselineError = validateBaseline(value.baseline);
    if (baselineError) return `Optimize Output ${baselineError}`;
    for (const metric of value.metrics) {
      if (!isRecord(metric) || !nonEmptyString(metric.benchRunId)
        || !nonEmptyString(metric.ownerUserId) || !nonEmptyString(metric.domainId)) {
        return "Optimize Output metrics[] 必须包含 benchRunId、ownerUserId 和 domainId";
      }
    }
    if (value.spec != null) {
      const specError = validateSpec(value.spec);
      if (specError) return `Optimize Output ${specError}`;
    }
  }
  if (stepType === "bench") {
    if (!nonEmptyString(value.benchRunId) || !nonEmptyString(value.domainId)
      || !isRecord(value.metrics) || !nonEmptyString(value.detailUrl)) {
      return "Bench Output 必须包含 benchRunId、domainId、metrics 和 detailUrl";
    }
  }
  if (stepType === "bench_plan") {
    const baseline = value.baseline as Record<string, unknown> | undefined;
    const objective = value.objective as Record<string, unknown> | undefined;
    const spec = value.spec as Record<string, unknown> | undefined;
    const baselineError = validateBaseline(baseline);
    if (baselineError || !nonEmptyString(objective?.text) || !nonEmptyString(objective?.path)
      || spec?.version !== "v0" || !nonEmptyString(spec?.path)) {
      return "Bench Plan Output 必须包含目标、Spec v0 和完整 Train/Test Baseline";
    }
    const specError = validateSpec(spec);
    if (specError) return `Bench Plan Output ${specError}`;
  }
  if (stepType === "pack" && !isRecord(value.pack)) return "Pack Output 必须包含 pack";
  if (stepType === "restore" && !isRecord(value.restore)) return "Restore Output 必须包含 restore";
  return null;
}

type OptimizeReportWarning = {
  code: string;
  message: string;
  details?: Record<string, unknown>;
};

function addOptimizeWarning(
  warnings: OptimizeReportWarning[],
  code: string,
  message: string,
  details?: Record<string, unknown>,
): void {
  warnings.push({ code, message, ...(details ? { details } : {}) });
}

async function collectOptimizeReportWarnings(
  repo: EvolveRepository,
  benchRunRepo: BenchRunRepository | null,
  step: EvolveStepRow,
  output: unknown,
): Promise<OptimizeReportWarning[]> {
  const warnings: OptimizeReportWarning[] = [];
  const outputError = validateStepOutput("optimize", output);
  if (outputError) addOptimizeWarning(warnings, "OPTIMIZE_OUTPUT_CONTRACT_WARNING", outputError);
  if (!isRecord(output)) return warnings;

  if (typeof (output.roundDecision as { stop?: unknown } | undefined)?.stop !== "boolean") {
    addOptimizeWarning(
      warnings,
      "OPTIMIZE_ROUND_DECISION_MISSING",
      "Optimize Output 未提供明确的 roundDecision.stop，本次不会自动创建下一轮",
    );
  }

  const taskSteps = await repo.listSteps(step.task_id);
  const stepById = new Map(taskSteps.map((item) => [item.step_id, item]));
  const baseline = isRecord(output.baseline) ? output.baseline : {};
  if (!benchRunRepo) {
    addOptimizeWarning(
      warnings,
      "OPTIMIZE_BENCH_LOOKUP_UNAVAILABLE",
      "Bench Run 数据库不可用，已跳过 Optimize 结果关联检查",
    );
  } else {
    for (const role of ["train", "test"] as const) {
      const reported = isRecord(baseline[role]) ? baseline[role] : null;
      if (!reported) continue;
      const producerStepId = String(reported.producerStepId ?? "");
      const benchRunId = String(reported.benchRunId ?? "");
      const producer = stepById.get(producerStepId);
      const run = benchRunId ? await benchRunRepo.findByBenchRunId(benchRunId) : null;
      const runConfig = run ? (parseJson(run.run_config_json) as {
        evolveTaskId?: string; evolveStepId?: string; role?: string;
      } | null) : null;
      const expectedSource = producer?.step_id === step.step_id ? "generated" : "reused";
      const expectedRunRole = `baseline_${role}`;
      const issues = [
        producer?.step_type === "bench_plan" || producer?.step_type === "optimize" ? "" : "producer_step",
        run?.status === "succeeded" ? "" : "bench_run_status",
        reported.source === expectedSource ? "" : "source",
        dbText(run?.owner_user_id) === String(reported.ownerUserId ?? "") ? "" : "owner",
        dbText(run?.domain_id) === String(reported.domainId ?? "") ? "" : "domain",
        runConfig?.evolveTaskId === step.task_id ? "" : "task",
        runConfig?.evolveStepId === producerStepId ? "" : "step",
        runConfig?.role === expectedRunRole ? "" : "historical_role",
      ].filter(Boolean);
      if (issues.length) {
        addOptimizeWarning(
          warnings,
          "OPTIMIZE_BASELINE_RUN_WARNING",
          `${role} Baseline 的关联信息与 ClawWeb 当前记录不完全一致`,
          {
            role, benchRunId, producerStepId, issues,
            expectedHistoricalRole: expectedRunRole,
            actualHistoricalRole: runConfig?.role ?? null,
          },
        );
      }
    }

    const metrics = Array.isArray(output.metrics) ? output.metrics : [];
    for (const metric of metrics) {
      if (!isRecord(metric)) continue;
      const role = metric.role === "candidate_train" ? "train"
        : metric.role === "candidate_test" ? "test" : null;
      const benchRunId = String(metric.benchRunId ?? "");
      if (!role) {
        addOptimizeWarning(
          warnings,
          "OPTIMIZE_CANDIDATE_ROLE_WARNING",
          "Optimize Candidate 指标包含未知角色",
          { role: metric.role ?? null, benchRunId },
        );
        continue;
      }
      const run = benchRunId ? await benchRunRepo.findByBenchRunId(benchRunId) : null;
      const runConfig = run ? (parseJson(run.run_config_json) as {
        evolveTaskId?: string; evolveStepId?: string; role?: string;
      } | null) : null;
      const issues = [
        run?.status === "succeeded" ? "" : "bench_run_status",
        dbText(run?.owner_user_id) === String(metric.ownerUserId ?? "") ? "" : "owner",
        dbText(run?.domain_id) === String(metric.domainId ?? "") ? "" : "domain",
        runConfig?.evolveTaskId === step.task_id ? "" : "task",
        runConfig?.evolveStepId === step.step_id ? "" : "step",
        runConfig?.role === `candidate_${role}` ? "" : "historical_role",
      ].filter(Boolean);
      if (issues.length) {
        addOptimizeWarning(
          warnings,
          "OPTIMIZE_CANDIDATE_RUN_WARNING",
          `${role} Candidate 的关联信息与 ClawWeb 当前记录不完全一致`,
          { role, benchRunId, issues },
        );
      }
    }
  }

  try {
    const round = Number(step.round_no || 0);
    const diff = isRecord(output.diff) ? output.diff : {};
    const roundArtifacts = isRecord(output.roundArtifacts) ? output.roundArtifacts : {};
    if (diff.artifact != null) {
      parseEvolveArtifactRef(diff.artifact, { taskId: step.task_id, round, kind: "diff" });
    }
    if (roundArtifacts.manifestRef != null) {
      parseEvolveArtifactRef({ ref: roundArtifacts.manifestRef }, { taskId: step.task_id, round, kind: "manifest" });
    }
    const pack = isRecord(output.pack) ? output.pack : null;
    if (pack?.status === "available") {
      parseEvolveArtifactRef(pack.artifact, { taskId: step.task_id, round, kind: "pack" });
    } else if (pack?.status === "unchanged" && isRecord(pack.effectiveArtifact)) {
      validatePackArtifact(pack.effectiveArtifact);
    }
  } catch (error) {
    addOptimizeWarning(
      warnings,
      "OPTIMIZE_ARTIFACT_WARNING",
      error instanceof Error ? error.message : String(error),
    );
  }

  return warnings;
}
function botCallbackUrl(req: Request, taskId: string, stepId: string): string {
  return `${req.protocol}://${req.get("host")}/api/evolve/internal/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepId)}/bot-callback`;
}
function resolveRequestUserId(req: Request): string | null {
  const cookies = req.cookies as Record<string, string> | undefined;
  const userId = [
    req.header("X-Staff-Id"),
    req.header("staff_id"),
    req.header("X-User-Id"),
    cookies?.staff_id,
  ].map((value) => value?.trim()).find(Boolean);
  if (userId) return userId;
  const host = req.get("host") ?? "";
  return host.includes("localhost") || host.includes("127.0.0.1") ? "dev_local" : null;
}
function stepView(row: EvolveStepRow, includeOutput = false) {
  return {
    stepId: row.step_id,
    taskId: row.task_id,
    stepType: row.step_type,
    stepNo: row.step_no,
    roundNo: row.round_no,
    command: row.command,
    status: row.status,
    botRunId: row.bot_run_id,
    botSessionId: row.bot_session_id,
    botResponse: parseJson(row.bot_response_json),
    startedAt: row.started_at,
    completedAt: row.completed_at,
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
    summary: row.summary,
    ...(includeOutput ? { output: parseJson(row.output_json) } : {}),
    error: row.error_code || row.error_message ? {
      code: row.error_code, message: row.error_message, retryable: row.retryable == null ? null : Boolean(row.retryable),
    } : null,
  };
}
function publicTask(task: Record<string, unknown>, steps?: EvolveStepRow[], includeStepOutput = false) {
  const config = parseJson(String(task.config_json ?? "{}"));
  const rest = { ...task };
  delete rest.config_json;
  return { ...rest, config, steps: steps?.map((item) => stepView(item, includeStepOutput)) };
}
function isTaskShared(task: { config_json: string }): boolean {
  const config = parseJson(task.config_json) as Record<string, unknown> | null;
  return config?.shared === true;
}
function canReadTask(req: Request, task: EvolveTaskRow): boolean {
  const actor = resolveRequestUserId(req);
  return Boolean(actor && (actor === "dev_local" || task.user_id === actor || task.created_by === actor
    || req.isClawEvolveAdmin || isTaskShared(task)));
}
function canManageTask(req: Request, task: EvolveTaskRow): boolean {
  const actor = resolveRequestUserId(req);
  return Boolean(actor && (actor === "dev_local" || task.created_by === actor
    || req.isClawEvolveAdmin));
}
function canManageTaskLogs(req: Request, task: EvolveTaskRow): boolean {
  const actor = resolveRequestUserId(req);
  return Boolean(actor && (actor === "dev_local" || actor === task.user_id || actor === task.created_by
    || req.isClawEvolveAdmin));
}
function taskLogArchiveView(row: import("../repositories/evolve-repository.js").EvolveTaskLogArchiveRow) {
  return {
    archiveId: row.archive_id,
    taskId: row.task_id,
    status: row.status,
    requestedBy: row.requested_by,
    transport: row.transport,
    artifact: row.artifact_ref ? {
      ref: row.artifact_ref, size: Number(row.artifact_size ?? 0), sha256: row.artifact_sha256,
      contentType: row.artifact_content_type,
    } : null,
    metadata: parseJson(row.metadata_json),
    error: row.error_code || row.error_message ? { code: row.error_code, message: row.error_message } : null,
    startedAt: row.started_at,
    completedAt: row.completed_at,
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
  };
}
async function withTaskSource(
  view: Record<string, unknown>,
  task: { task_id: string; config_json: string },
  taskSourceService: InsightTaskSourcePort | null,
) {
  if (!taskSourceService || !isInsightImprovementTask(task)) return view;
  const source = await taskSourceService.findView(task.task_id);
  return source ? { ...view, source } : view;
}
function isInsightImprovementTask(task: { config_json: string }): boolean {
  const config = parseJson(task.config_json) as { input?: { type?: string } } | null;
  return config?.input?.type === "insight_improvement";
}

async function markInsightTaskApplied(
  repo: EvolveRepository,
  improvementRepo: InsightImprovementPort | null,
  taskId: string,
): Promise<void> {
  if (!improvementRepo) return;
  const task = await repo.findTask(taskId);
  if (!task || !isInsightImprovementTask(task)) return;

  const link = await improvementRepo.findEvolveLinkByTaskId(taskId);
  if (!link) {
    console.warn("[clawweb][evolve][insight-apply] completed Insight Task has no improvement link", { taskId });
    return;
  }

  const result = await improvementRepo.resolveFromApply({
    improvementId: link.improvement_id,
    applyTaskId: taskId,
    requestId: link.request_id,
    appliedBy: "claw-evolve",
  });
  if (result.outcome === "NOT_FOUND") {
    console.warn("[clawweb][evolve][insight-apply] improvement not found", {
      taskId, improvementId: link.improvement_id,
    });
    return;
  }
  if (result.outcome === "STATE_CONFLICT") {
    console.warn("[clawweb][evolve][insight-apply] improvement state does not allow apply confirmation", {
      taskId, improvementId: link.improvement_id, currentStatus: result.currentStatus,
    });
    return;
  }
  console.info("[clawweb][evolve][insight-apply] improvement moved to verification", {
    taskId,
    improvementId: link.improvement_id,
    idempotent: result.outcome === "IDEMPOTENT",
  });
}

async function createPlanStep(
  req: Request,
  repo: EvolveRepository,
  dispatch: Dispatch,
  step: EvolveStepRow,
  task: { task_id: string; user_id: string; bot_id: string; config_json: string },
) {
  const taskConfig = parseJson(task.config_json) as {
    dispatchMode?: "message" | "run"; nodeCommands?: NodeCommandYamls; forceMessage?: boolean; runtimeMaintenance?: boolean; clawwebUrl?: string;
    goal?: string;
  } | null;
  const dispatchMode = taskConfig?.dispatchMode
    ?? await repo.resolveBotDispatchMode(task.user_id, task.bot_id, taskBotEnv(task));
  const nextStepId = id("STEP");
  const systemArgs: Array<[string, string | number]> = [
    ["task-id", task.task_id], ["step-id", nextStepId],
    ["owner-id", task.user_id],
    ["bot-id", task.bot_id],
    ["clawweb-url", taskConfig?.clawwebUrl ?? getClawWebPublicBaseUrl()],
  ];
  if (taskConfig?.goal) systemArgs.push(["goal", quoteCommandArgument(taskConfig.goal)]);
  const nextCommand = renderCommand(
    taskConfig?.nodeCommands?.plan ?? "/clawevolve-plan",
    {},
    systemArgs,
  );
  const next = await repo.createStep({
    stepId: nextStepId, taskId: task.task_id, stepType: "plan",
    stepNo: step.step_no + 1, command: nextCommand,
  });
  try {
    const runtime = await repo.resolveEvolveBotRuntime(task.user_id, task.bot_id, taskBotEnv(task));
    const result = await dispatch({
      taskId: task.task_id, stepPk: next.id, stepId: nextStepId,
      stepType: "plan", userId: task.user_id, botId: task.bot_id,
      command: nextCommand, mode: dispatchMode,
      runtime,
      forceMessage: taskConfig?.forceMessage === true,
      runtimeMaintenance: taskConfig?.runtimeMaintenance !== false,
      callbackUrl: botCallbackUrl(req, task.task_id, nextStepId),
    });
    await repo.markDispatched(nextStepId, result.runId, result.sessionId, result.platformResponse);
  } catch (dispatchError) {
    await repo.markDispatchFailed(nextStepId, dispatchError instanceof Error ? dispatchError.message : String(dispatchError));
  }
  return { stepId: nextStepId, stepType: "plan" as const };
}

async function createInitialPlanStep(
  req: Request,
  repo: EvolveRepository,
  dispatch: Dispatch,
  task: EvolveTaskRow,
  runtime: EvolveBotRuntime | null,
) {
  const config = (parseJson(task.config_json) as {
    dispatchMode?: "message" | "run"; nodeCommands?: NodeCommandYamls; forceMessage?: boolean;
    runtimeMaintenance?: boolean; clawwebUrl?: string; goal?: string;
  } | null) ?? {};
  const stepId = id("STEP");
  const command = renderCommand(
    config.nodeCommands?.plan ?? "/clawevolve-plan",
    {},
    [
      ["task-id", task.task_id], ["step-id", stepId], ["owner-id", task.user_id],
      ["bot-id", task.bot_id],
      ["clawweb-url", config.clawwebUrl ?? getClawWebPublicBaseUrl()],
      ["goal", quoteCommandArgument(config.goal ?? "")],
    ],
  );
  const step = await repo.createStep({
    stepId, taskId: task.task_id, stepType: "plan", stepNo: 1, command,
  });
  await startInitialEvolveStep({
    repo, dispatch,
    task: { task_id: task.task_id, user_id: task.user_id, bot_id: task.bot_id },
    businessStep: step, runtime,
    clawwebUrl: config.clawwebUrl ?? getClawWebPublicBaseUrl(),
    callbackUrl: (createdStepId) => botCallbackUrl(req, task.task_id, createdStepId),
    businessDispatch: {
      taskId: task.task_id, stepId, stepType: "plan", userId: task.user_id, botId: task.bot_id,
      command, mode: config.dispatchMode ?? "message", callbackUrl: botCallbackUrl(req, task.task_id, stepId),
      runtime, forceMessage: config.forceMessage === true,
      runtimeMaintenance: config.runtimeMaintenance !== false,
    },
  });
  return step;
}
async function createOptimizeStep(
  req: Request,
  repo: EvolveRepository,
  dispatch: Dispatch,
  task: { task_id: string; user_id: string; bot_id: string; config_json: string },
  roundNo: number,
  initialTaskStep = false,
) {
  const existingSteps = await repo.listSteps(task.task_id);
  const existingRound = existingSteps.find((item) =>
    item.step_type === "optimize" && Number(item.round_no) === roundNo);
  if (existingRound) {
    return {
      stepId: existingRound.step_id,
      stepType: "optimize" as const,
      roundNo,
    };
  }
  const config = parseJson(task.config_json) as {
    dispatchMode?: "message" | "run"; trainBenchDomainId?: string; testBenchDomainId?: string;
    ownerUserId?: string; nodeCommands?: NodeCommandYamls; forceMessage?: boolean; runtimeMaintenance?: boolean; clawwebUrl?: string; openclawExecutionMode?: "local" | "gateway";
  } | null;
  const dispatchMode = config?.dispatchMode ?? await repo.resolveBotDispatchMode(task.user_id, task.bot_id, taskBotEnv(task));
  const stepId = id("STEP");
  const nodeOptimize = config?.nodeCommands?.optimize;
  const command = nodeOptimize
    ? renderCommand(nodeOptimize, {},
      [["task-id", task.task_id], ["step-id", stepId], ["round", roundNo],
       ["train-bench-domain-id", config?.trainBenchDomainId ?? ""],
       ["test-bench-domain-id", config?.testBenchDomainId ?? ""],
       ["clawweb-url", config?.clawwebUrl ?? getClawWebPublicBaseUrl()],
       ["openclaw-execution-mode", config?.openclawExecutionMode ?? "local"]])
    : `/clawevolve-workflow --stage optimize --task-id ${task.task_id} --step-id ${stepId} --round ${roundNo} --train-bench-domain-id ${config?.trainBenchDomainId} --test-bench-domain-id ${config?.testBenchDomainId} --clawweb-url ${config?.clawwebUrl ?? getClawWebPublicBaseUrl()} --openclaw-execution-mode ${config?.openclawExecutionMode ?? "local"}`;
  const ownerUserId = safeBenchCommandValue("ownerId", config?.ownerUserId ?? task.user_id);
  const commandWithOwner = `${command} --owner-id ${ownerUserId}`;
  const stepNo = Math.max(0, ...existingSteps.map((item) => item.step_no)) + 1;
  const step = await repo.createStep({
    stepId, taskId: task.task_id, stepType: "optimize",
    stepNo, roundNo, command: commandWithOwner,
  });
  const runtime = await repo.resolveEvolveBotRuntime(task.user_id, task.bot_id, taskBotEnv(task));
  if (initialTaskStep) {
    await startInitialEvolveStep({
      repo, dispatch, task, businessStep: step, runtime,
      clawwebUrl: config?.clawwebUrl ?? getClawWebPublicBaseUrl(),
      callbackUrl: (createdStepId) => botCallbackUrl(req, task.task_id, createdStepId),
      businessDispatch: {
        taskId: task.task_id, stepId, stepType: "optimize",
        userId: task.user_id, botId: task.bot_id, command: commandWithOwner, mode: dispatchMode,
        callbackUrl: botCallbackUrl(req, task.task_id, stepId), runtime,
        forceMessage: config?.forceMessage === true,
        runtimeMaintenance: config?.runtimeMaintenance !== false,
        optimizeArgs: {
          round: roundNo,
          trainBenchDomainId: config?.trainBenchDomainId,
          testBenchDomainId: config?.testBenchDomainId,
        },
      },
    });
  } else {
    try {
      const result = await dispatch({
        taskId: task.task_id, stepPk: step.id, stepId: stepId, stepType: "optimize",
        userId: task.user_id, botId: task.bot_id, command: commandWithOwner, mode: dispatchMode,
        callbackUrl: botCallbackUrl(req, task.task_id, stepId),
        runtime,
        forceMessage: config?.forceMessage === true,
        runtimeMaintenance: config?.runtimeMaintenance !== false,
        optimizeArgs: {
          round: roundNo,
          trainBenchDomainId: config?.trainBenchDomainId,
          testBenchDomainId: config?.testBenchDomainId,
        },
      });
      await repo.markDispatched(stepId, result.runId, result.sessionId, result.platformResponse);
    } catch (error) {
      await repo.markDispatchFailed(stepId, error instanceof Error ? error.message : String(error));
    }
  }
  return { stepId: stepId, stepType: "optimize" as const, roundNo };
}

async function advanceOptimizeTask(
  req: Request,
  repo: EvolveRepository,
  dispatch: Dispatch,
  step: EvolveStepRow,
  output: unknown,
  improvementRepo: InsightImprovementPort | null,
) {
  const task = await repo.findTask(step.task_id);
  if (!task) throw new Error("step 关联任务不存在");
  if (["canceled", "failed"].includes(task.status)) return null;
  if (task.status === "completed") {
    // A duplicate final report can safely repair an older completed task whose
    // Apply callback was lost between Evolve and Insight Center.
    await markInsightTaskApplied(repo, improvementRepo, step.task_id);
    return null;
  }

  const config = parseJson(task.config_json) as { maxRounds?: number } | null;
  const configuredMaxRounds = Number(config?.maxRounds ?? 1);
  const maxRounds = Number.isSafeInteger(configuredMaxRounds) && configuredMaxRounds > 0
    ? configuredMaxRounds : 1;
  const roundNo = Number(step.round_no ?? 0);
  const explicitContinue = isRecord(output)
    && isRecord(output.roundDecision)
    && output.roundDecision.stop === false;

  if (explicitContinue && roundNo < maxRounds) {
    return createOptimizeStep(req, repo, dispatch, task, roundNo + 1);
  }
  await markInsightTaskApplied(repo, improvementRepo, step.task_id);
  // Mark the Evolve Task completed only after the linked Insight item has
  // entered verification. If the Apply write fails, the task remains retryable
  // instead of exposing a completed task with a stale improvement item.
  await repo.completeTask(step.task_id);
  return null;
}

async function createBenchEvolutionOptimizeStep(
  req: Request, repo: EvolveRepository, dispatch: Dispatch,
  task: { task_id: string; user_id: string; bot_id: string; config_json: string },
) {
  const existingSteps = await repo.listSteps(task.task_id);
  const existing = existingSteps.find((item) => item.step_type === "optimize" && item.round_no === 1);
  if (existing) return { stepId: existing.step_id, stepType: "optimize" as const, roundNo: 1 };
  const config = parseJson(task.config_json) as {
    dispatchMode?: "message" | "run"; trainBenchDomainId?: string; testBenchDomainId?: string;
    ownerUserId?: string; nodeCommands?: NodeCommandYamls; forceMessage?: boolean; runtimeMaintenance?: boolean; clawwebUrl?: string; openclawExecutionMode?: "local" | "gateway";
  } | null;
  const stepId = id("STEP");
  const command = renderCommand(config?.nodeCommands?.optimize ?? "/clawevolve-workflow --stage optimize", {
    train_bench_domain_id: config?.trainBenchDomainId,
    test_bench_domain_id: config?.testBenchDomainId,
  }, [
    ["task-id", task.task_id], ["step-id", stepId], ["round", 1],
    ["owner-id", config?.ownerUserId ?? task.user_id],
    ["train-bench-domain-id", config?.trainBenchDomainId ?? ""],
    ["test-bench-domain-id", config?.testBenchDomainId ?? ""],
    ["clawweb-url", config?.clawwebUrl ?? getClawWebPublicBaseUrl()],
    ["openclaw-execution-mode", config?.openclawExecutionMode ?? "local"],
  ]);
  const stepNo = Math.max(0, ...existingSteps.map((item) => item.step_no)) + 1;
  const step = await repo.createStep({ stepId, taskId: task.task_id, stepType: "optimize", stepNo, roundNo: 1, command });
  console.info("[clawweb][evolve][bench-plan] progressing to optimize", {
    taskId: task.task_id, createdOptimizeStepId: stepId,
  });
  try {
    const runtime = await repo.resolveEvolveBotRuntime(task.user_id, task.bot_id, taskBotEnv(task));
    const result = await dispatch({
      taskId: task.task_id, stepPk: step.id, stepId, stepType: "optimize",
      userId: task.user_id, botId: task.bot_id, command,
      mode: config?.dispatchMode ?? await repo.resolveBotDispatchMode(task.user_id, task.bot_id, taskBotEnv(task)),
      callbackUrl: botCallbackUrl(req, task.task_id, stepId), runtime,
      forceMessage: config?.forceMessage === true,
      runtimeMaintenance: config?.runtimeMaintenance !== false,
      optimizeArgs: { round: 1, trainBenchDomainId: config?.trainBenchDomainId, testBenchDomainId: config?.testBenchDomainId },
    });
    await repo.markDispatched(stepId, result.runId, result.sessionId, result.platformResponse);
  } catch (error) {
    await repo.markDispatchFailed(stepId, error instanceof Error ? error.message : String(error));
  }
  return { stepId, stepType: "optimize" as const, roundNo: 1 };
}


function buildSuggestionApplyMessage(input: {
  taskId: string;
  stepId: string;
  workflowId: string;
  applicationSpec: string;
  proposal?: Record<string, unknown>;
  claimToken: string;
}): string {
  return `[clawmind-task-guard-apply:v1]\n请调用一次 workflow_engine_dispatch，完整参数如下：\n${JSON.stringify({
    command: `apply-suggestion ${input.workflowId}`,
    spec: input.applicationSpec,
    ...(input.proposal ? { proposal: input.proposal } : {}),
    deploy: true,
    taskContext: {
      taskId: input.taskId,
      stepId: input.stepId,
      claimToken: input.claimToken,
    },
  })}`;
}

type SuggestionDiagnosisContext = {
  schemaVersion: "task-guard-diagnosis-context/v1";
  readOnly: true;
  diagnoses: Array<{
    diagnosisId: string;
    analysisId?: string;
    flowIds: string[];
    nodeId: string | null;
    failureSignature: string;
    failureMode: string | null;
    conclusion: string;
    problemSources: Array<{ eventId: string; eventType: string; summary: string }>;
  }>;
};

function storedStringArray(value: string | null | undefined): string[] {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value) as unknown;
    return Array.isArray(parsed)
      ? parsed.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
      : [];
  } catch {
    return [];
  }
}

async function buildSuggestionDiagnosisContext(
  suggestions: EvolveSuggestionRow[],
  workflowEvolutionRepo: WorkflowEvolutionRepository | null,
  repo: EvolveRepository,
): Promise<SuggestionDiagnosisContext | undefined> {
  const diagnosisIds = [...new Set(suggestions.flatMap((suggestion) => storedStringArray(suggestion.source_diagnosis_ids)))]
    .slice(-8);
  if (diagnosisIds.length === 0) return undefined;

  try {
    const projected = workflowEvolutionRepo
      ? (await workflowEvolutionRepo.listProjectedDiagnoses({
          workflowId: suggestions[0]?.workflow_id,
          limit: 200,
        })).rows
      : [];
    const projectedById = new Map(projected.flatMap((item) => {
      const diagnosisId = typeof item.diagnosis_id === "string" ? item.diagnosis_id : "";
      return diagnosisId ? [[diagnosisId, item] as const] : [];
    }));
    const selectedProjected = diagnosisIds.flatMap((diagnosisId) => {
      const item = projectedById.get(diagnosisId);
      return item ? [item] : [];
    });
    const citedEventIds = selectedProjected.flatMap((item) => Array.isArray(item.evidence_event_ids)
      ? item.evidence_event_ids.filter((eventId): eventId is string => typeof eventId === "string")
      : []);
    const evidenceRows = workflowEvolutionRepo
      ? await workflowEvolutionRepo.listEvidenceByEventIds(citedEventIds.slice(0, 40))
      : [];
    const evidenceById = new Map(evidenceRows.map((row) => [row.event_id, row]));
    const diagnoses: SuggestionDiagnosisContext["diagnoses"] = [];

    for (const diagnosisId of diagnosisIds) {
      const item = projectedById.get(diagnosisId);
      if (item) {
        const flowIds = Array.isArray(item.flow_ids)
          ? item.flow_ids.filter((flowId): flowId is string => typeof flowId === "string").slice(0, 10)
          : typeof item.flow_id === "string" ? [item.flow_id] : [];
        const eventIds = Array.isArray(item.evidence_event_ids)
          ? item.evidence_event_ids.filter((eventId): eventId is string => typeof eventId === "string").slice(0, 5)
          : [];
        diagnoses.push({
          diagnosisId,
          ...(typeof item.analysis_id === "string" ? { analysisId: item.analysis_id } : {}),
          flowIds,
          nodeId: typeof item.node_id === "string" ? item.node_id : null,
          failureSignature: String(item.failure_signature ?? "").slice(0, 512),
          failureMode: typeof item.failure_mode === "string" ? item.failure_mode : null,
          conclusion: String(item.reasoning ?? item.failure_signature ?? "").slice(0, 1_000),
          problemSources: presentEvidence(eventIds, evidenceById).map((source) => ({
            eventId: source.eventId,
            eventType: source.eventType,
            summary: source.summary,
          })),
        });
        continue;
      }

      const legacy = await repo.findDiagnosis(diagnosisId);
      if (!legacy) continue;
      diagnoses.push({
        diagnosisId,
        flowIds: [legacy.flow_id].filter(Boolean).slice(0, 10),
        nodeId: legacy.node_id,
        failureSignature: legacy.failure_signature,
        failureMode: legacy.failure_mode,
        conclusion: (legacy.error_text || legacy.failure_signature).slice(0, 1_000),
        problemSources: [],
      });
    }

    return diagnoses.length > 0
      ? { schemaVersion: "task-guard-diagnosis-context/v1", readOnly: true, diagnoses }
      : undefined;
  } catch (error) {
    console.warn(`[task-guard] suggestion diagnosis context unavailable: ${error instanceof Error ? error.message : String(error)}`);
    return undefined;
  }
}

function diffSuggestionProposals(previous: Record<string, unknown>, current: Record<string, unknown>): Record<string, unknown> {
  const operations = (value: Record<string, unknown>) => Array.isArray(value.operations)
    ? value.operations.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item))
    : [];
  const key = (item: Record<string, unknown>) => `${String(item.nodeId ?? "")}\u0000${String(item.path ?? "")}`;
  const before = new Map(operations(previous).map((item) => [key(item), item]));
  const after = new Map(operations(current).map((item) => [key(item), item]));
  return {
    added: [...after].filter(([operationKey]) => !before.has(operationKey)).map(([, item]) => item),
    changed: [...after].filter(([operationKey, item]) => before.has(operationKey) && JSON.stringify(before.get(operationKey)) !== JSON.stringify(item)).map(([, item]) => item),
    removed: [...before].filter(([operationKey]) => !after.has(operationKey)).map(([, item]) => item),
  };
}



function buildRunAnalysisMessage(input: {
  analysisId: string;
  flowId: string;
}): string {
  return [
    `ClawWeb 发起了一个运行日志进化分析任务，请使用你的 workflow_engine_dispatch 工具处理。`,
    ``,
    `请调用 workflow_engine_dispatch，command 必须精确为：`,
    `analyze ${input.flowId} --analysis-id ${input.analysisId}`,
    ``,
    `分析结果和任务终态由插件通过签名内部 API 自动回写。不要自行 curl/fetch，也不要额外发送 HTTP 报告。`,
  ].join("\n");
}
export function createEvolveRouter(repo: EvolveRepository | null, deps: EvolveRouterDeps = {}): Router {
  const router = Router();
  const db = deps.db;
  const workflowEvolutionRepo = db ? new WorkflowEvolutionRepository(db) : null;
  const dispatch = deps.dispatch ?? dispatchEvolveCommand;
  const dispatchTaskLogArchive = deps.dispatchTaskLogArchive ?? dispatchEvolveTaskLogArchive;
  const cancelExecution = deps.cancelExecution ?? cancelEvolveExecution;
  const improvementRepo = deps.improvementRepo ?? null;
  const taskSourceService = deps.taskSourceService ?? null;
  const insightTaskService = deps.insightTaskService ?? null;
  const benchDomainRepo = deps.benchDomainRepo ?? null;
  const benchTemplateRepo = deps.benchTemplateRepo ?? null;
  const benchRunRepo = deps.benchRunRepo ?? null;
  const unavailableArtifactStore = new UnavailableObjectStore();
  const artifactStore = deps.artifactStore ?? unavailableArtifactStore;
  const artifactUrlStore = deps.artifactUrlStore ?? deps.artifactStore ?? unavailableArtifactStore;
  const botWorkflowPermissionRepo = deps.botWorkflowPermissionRepo ?? null;
  const runAnalysisStarter = deps.runAnalysisStarter
    ?? (repo && db ? createRunAnalysisStarter({ repo, db, dispatch }) : null);

  router.get("/task-definitions", (_req, res) => {
    res.json({
      tasks: Object.values(EVOLVE_TASK_REGISTRY).map((definition) => ({
        type: definition.type,
        label: definition.label,
        nodes: definition.nodes.map((key) => ({ ...EVOLVE_NODE_REGISTRY[key] })),
      })),
      variants: {
        insight_improvement: taskNodeKeys("full", "insight_improvement").map((key) => ({ ...EVOLVE_NODE_REGISTRY[key] })),
      },
    });
  });

  router.post("/benches", asyncHandler(async (req: Request, res: Response) => {
    if (!repo || !benchDomainRepo || !benchTemplateRepo) {
      res.status(503).json({ error: "Bench 或 Evolve 数据库不可用" }); return;
    }
    const {
      taskName, remark, userId, botId, botEnv, benchDomainId, templateName = "", templateVersion = null,
      model = "antchat/GLM-5.1", suite = "all", scene = "claw-evolve-bench", judge,
      openclawExecutionMode: rawOpenClawExecutionMode,
      nodeCommandYamls, forceMessage: rawForceMessage, runtimeMaintenance: rawRuntimeMaintenance,
    } = req.body ?? {};
    if (!String(taskName ?? "").trim() || !userId || !botId || !String(benchDomainId ?? "").trim()) {
      res.status(400).json({ error: "taskName、userId、botId、benchDomainId 为必填项" }); return;
    }
    if (String(taskName).trim().length > 128 || String(remark ?? "").length > 1000) {
      res.status(400).json({ error: "任务名称不能超过128字，备注不能超过1000字" }); return;
    }
    const ownerUserId = String(userId);
    const actorUserId = resolveRequestUserId(req);
    if (!actorUserId) { res.status(401).json({ error: "无法识别当前登录用户" }); return; }
    if (actorUserId !== ownerUserId) {
      res.status(403).json({ error: "Bench 任务只能在当前登录用户空间发起" }); return;
    }
    if (await rejectUnsupportedBotEngine(repo, res, ownerUserId, String(botId), String(botEnv ?? ""))) return;
    const selectedDomainId = String(benchDomainId).trim();
    const domain = await benchDomainRepo.findByOwnerAndDomainId(ownerUserId, selectedDomainId);
    if (!domain || domain.status !== "active") {
      res.status(422).json({ error: "Bench Domain 不存在、无权访问或已归档" }); return;
    }
    const templates = await benchTemplateRepo.listAll({ ownerUserId, domainId: selectedDomainId });
    const requestedTemplateName = String(templateName).trim();
    const selectedTemplates = requestedTemplateName
      ? templates.filter((item) => item.template_name === requestedTemplateName)
      : templates.filter((item) => item.status === "published" && item.published_version != null);
    if (!selectedTemplates.length || (requestedTemplateName
      && selectedTemplates.some((item) => item.status !== "published" || item.published_version == null))) {
      res.status(422).json({ error: "所选 Bench Domain/Template 必须至少包含一个已发布模板" }); return;
    }
    if (templateVersion != null && (!Number.isSafeInteger(Number(templateVersion)) || Number(templateVersion) < 1)) {
      res.status(400).json({ error: "templateVersion 必须是正整数" }); return;
    }
    if (String(templateName).trim() && templateVersion != null
      && Number(templateVersion) !== selectedTemplates[0]?.published_version) {
      res.status(422).json({ error: "templateVersion 必须是当前 published version" }); return;
    }
    let nodeCommands: NodeCommandYamls;
    try { nodeCommands = parseNodeCommandYamls(nodeCommandYamls, [...taskNodeKeys("bench")]); }
    catch (error) { res.status(400).json({ error: error instanceof Error ? error.message : String(error) }); return; }
    const taskId = evolveTaskId();
    const stepId = id("STEP");
    const clawwebUrl = getClawWebPublicBaseUrl();
    const forceMessage = rawForceMessage === true;
    const runtimeMaintenance = rawRuntimeMaintenance !== false;
    let openclawExecutionMode: "local" | "gateway";
    try { openclawExecutionMode = resolveOpenClawExecutionMode(rawOpenClawExecutionMode); }
    catch (error) { res.status(400).json({ error: error instanceof Error ? error.message : String(error) }); return; }
    const dispatchMode = await repo.resolveBotDispatchMode(ownerUserId, String(botId), String(botEnv ?? ""));
    const judgeInput = isRecord(judge) ? judge : {};
    if ("apiKey" in judgeInput || "apiKeyRef" in judgeInput || "baseUrlRef" in judgeInput) {
      res.status(400).json({ error: "judge credential 由服务端环境配置，不能从请求传入" }); return;
    }
    const defaultBenchCommand = `${defaultNodeCommand("bench")}`
      .replace("antchat/GLM-5.1", safeBenchCommandValue("model", model))
      .replace("--suite all", `--suite ${safeBenchCommandValue("suite", suite)}`);
    const benchCommand = nodeCommands.bench ?? defaultBenchCommand;
    const commandModel = readNodeCommandOption(benchCommand, "model") ?? String(model);
    const commandSuite = readNodeCommandOption(benchCommand, "suite") ?? String(suite);
    const commandJudge = readNodeCommandOption(benchCommand, "judge")
      ?? (typeof judge === "string" ? judge : (nonEmptyString(judgeInput.model) ? String(judgeInput.model) : ""));
    for (const [name, value] of [["model", commandModel], ["suite", commandSuite], ["judge", commandJudge]] as const) {
      if (value) safeBenchCommandValue(name, value);
    }
    const bench = {
      domainId: selectedDomainId, ownerUserId,
      templateName: String(templateName).trim(),
      templateVersion: templateVersion == null ? null : Number(templateVersion),
      model: commandModel, suite: commandSuite, scene: String(scene),
      judge: {
        model: commandJudge,
        baseUrlRef: "env:CLAWBENCH_JUDGE_BASE_URL",
        apiKeyRef: "env:CLAWBENCH_JUDGE_API_KEY",
      },
    };
    const config = {
      bench, versionPolicy: { policy: "warn" }, reportConfig: { enabled: true },
      nodeCommands: { bench: benchCommand }, dispatchMode, forceMessage, runtimeMaintenance, clawwebUrl, openclawExecutionMode, botEnv: String(botEnv ?? ""),
      pinnedTemplates: selectedTemplates.map((item) => ({
        templateName: item.template_name, templateVersion: item.published_version,
      })),
    };
    const template = config.nodeCommands.bench;
    let command: string;
    try {
      const systemArgs: Array<[string, string | number]> = [
        ["task-id", taskId], ["step-id", stepId],
        ["domain-id", safeBenchCommandValue("domainId", selectedDomainId)],
        ["owner-id", safeBenchCommandValue("ownerId", ownerUserId)],
        ["scene", safeBenchCommandValue("scene", scene)],
        ["clawweb-url", clawwebUrl],
        ["openclaw-execution-mode", openclawExecutionMode],
      ];
      if (requestedTemplateName) systemArgs.push(["template-name", safeBenchCommandValue("templateName", requestedTemplateName)]);
      if (selectedTemplates.length === 1) systemArgs.push(["template-version", Number(selectedTemplates[0].published_version)]);
      if (bench.judge.model && !readNodeCommandOption(template, "judge")) {
        systemArgs.push(["judge", safeBenchCommandValue("judge", bench.judge.model)]);
      }
      command = renderCommand(template, { domainId: selectedDomainId }, systemArgs);
    } catch (error) {
      res.status(400).json({ error: error instanceof Error ? error.message : String(error) }); return;
    }
    await repo.createTask({
      taskId, taskType: "bench", userId: ownerUserId, botId: String(botId),
      taskName: String(taskName).trim(), remark: String(remark ?? "").trim() || null,
      configJson: JSON.stringify(config), createdBy: String(req.header("X-User-Id") || ownerUserId),
    });
    const step = await repo.createStep({ stepId, taskId, stepType: "bench", stepNo: 1, command });
    const runtime = await repo.resolveEvolveBotRuntime(ownerUserId, String(botId), String(botEnv ?? ""));
    await startInitialEvolveStep({
      repo, dispatch,
      task: { task_id: taskId, user_id: ownerUserId, bot_id: String(botId) },
      businessStep: step, runtime, clawwebUrl,
      callbackUrl: (createdStepId) => botCallbackUrl(req, taskId, createdStepId),
      businessDispatch: {
        taskId, stepId, stepType: "bench", userId: ownerUserId,
        botId: String(botId), command, mode: dispatchMode,
        callbackUrl: botCallbackUrl(req, taskId, stepId), runtime, forceMessage, runtimeMaintenance,
      },
    });
    const task = await repo.findTask(taskId);
    res.status(201).json(publicTask(task as unknown as Record<string, unknown>, await repo.listSteps(taskId)));
  }));

  router.post(["/diagnoses", "/tasks"], asyncHandler(async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const discriminatedInput = isRecord(req.body?.input) ? req.body.input : null;
    if (discriminatedInput?.type === "insight_improvement") {
      if (!insightTaskService) {
        res.status(503).json({ error: "Insight Evolve Source 服务不可用" }); return;
      }
      try {
        const result = await insightTaskService.create({
          taskType: req.body?.taskType,
          taskName: req.body?.taskName,
          remark: req.body?.remark,
          userId: req.body?.userId,
          botId: req.body?.botId,
          botEnv: req.body?.botEnv,
          improvementId: discriminatedInput.improvementId,
          crossBotConfirmed: discriminatedInput.crossBotConfirmed,
          maxRounds: req.body?.maxRounds ?? 3,
          nodeCommandYamls: req.body?.nodeCommandYamls,
          forceMessage: req.body?.forceMessage,
          runtimeMaintenance: req.body?.runtimeMaintenance,
          openclawExecutionMode: req.body?.openclawExecutionMode,
          idempotencyKey: String(req.header("Idempotency-Key") ?? ""),
          actorUserId: resolveRequestUserId(req),
          persistAutoRepairGrant: discriminatedInput.persistAutoRepairGrant,
          autoExecuteAfterConsent: discriminatedInput.adminAutoExecute,
          adminConsentToken: discriminatedInput.adminConsentToken,
          callbackUrl: (taskId, stepId) => botCallbackUrl(req, taskId, stepId),
        });
        res.status(result.created ? 201 : 200).json({
          ...publicTask(
            result.task as unknown as Record<string, unknown>,
            result.steps,
          ),
          ...(result.source ? { source: result.source } : {}),
          ...(result.idempotent ? { idempotent: true } : {}),
        });
      } catch (error) {
        if (isInsightBoundaryError(error) && error.category) {
          const status = error.category === "validation" ? 400
            : error.category === "auth" ? 401
              : error.category === "forbidden" ? 403
              : error.category === "not_found" ? 404
                : error.category === "conflict" ? 409 : 422;
          res.status(status).json({
            code: error.code,
            error: error.message,
            stage: error.stage,
            retryable: error.retryable,
          });
          return;
        }
        throw error;
      }
      return;
    }
    const {
      taskType: requestedTaskType, taskName, remark, userId, botId, botEnv,
      apiKey: rawApiKey, judgeBackend: rawJudgeBackend,
      model = "GLM-5.1", diagnoseIntent: rawDiagnoseIntent, maxSessions: rawMaxSessions = 10, maxRounds = 3,
      startDate, endDate, goal: rawGoal, inputMode: rawInputMode, nodeCommandYamls,
      sessionSource: rawSessionSource,
      forceMessage: rawForceMessage, runtimeMaintenance: rawRuntimeMaintenance,
      openclawExecutionMode: rawOpenClawExecutionMode,
      improvementId: rawImprovementId, improvementRequestId: rawImprovementRequestId,
    } = req.body ?? {};
    const taskType = requestedTaskType === "full" ? "full" : "diagnose";
    const inputMode = taskType === "full" ? String(rawInputMode ?? "diagnose_goal") : "diagnose_goal";
    if (!new Set(["diagnose_goal", "direct_goal"]).has(inputMode)) {
      res.status(400).json({ error: "inputMode 必须是 diagnose_goal 或 direct_goal" }); return;
    }
    const requiresDiagnose = taskType === "diagnose" || inputMode === "diagnose_goal";
    let sessionSourceMode = rawSessionSource == null || rawSessionSource === "local"
      ? "local"
      : rawSessionSource === "service_export" ? "service_export" : null;
    if (requiresDiagnose && !sessionSourceMode) {
      res.status(400).json({ error: "sessionSource 必须是 local 或 service_export" }); return;
    }
    let goal: string;
    try {
      goal = taskType === "full" ? normalizeEvolutionGoal(rawGoal) : "";
    } catch (error) {
      res.status(400).json({ error: error instanceof Error ? error.message : String(error) }); return;
    }
    if (taskType === "full" && inputMode === "direct_goal" && !goal) {
      res.status(400).json({ error: "按目标进化必须提供非空 goal" }); return;
    }
    const improvementIdRaw = String(rawImprovementId ?? "").trim();
    const improvementId = improvementIdRaw ? Number(improvementIdRaw) : null;
    const forceMessage = rawForceMessage === true;
    let openclawExecutionMode: "local" | "gateway";
    try { openclawExecutionMode = resolveOpenClawExecutionMode(rawOpenClawExecutionMode); }
    catch (error) { res.status(400).json({ error: error instanceof Error ? error.message : String(error) }); return; }
    const runtimeMaintenance = rawRuntimeMaintenance !== false;
    const improvementRequestId = String(rawImprovementRequestId ?? "").trim();
    const apiKey = String(rawApiKey ?? "").trim();
    let judgeBackend: "subagent" | "api";
    try {
      judgeBackend = requiresDiagnose ? resolveDiagnoseJudgeBackend(rawJudgeBackend, apiKey) : "subagent";
    } catch (error) {
      res.status(400).json({ error: error instanceof Error ? error.message : String(error) }); return;
    }
    if (!String(taskName ?? "").trim() || !userId || !botId) {
      res.status(400).json({ error: "taskName、userId、botId 为必填项" }); return;
    }
    if (requiresDiagnose && judgeBackend === "api" && !apiKey) {
      res.status(400).json({ error: "API Judge 模式必须提供 apiKey" }); return;
    }
    if (String(taskName).trim().length > 128 || String(remark ?? "").length > 1000) {
      res.status(400).json({ error: "任务名称不能超过128字，备注不能超过1000字" }); return;
    }
    const diagnoseModel = String(model);
    if (requiresDiagnose && (!diagnoseModel.trim() || diagnoseModel.length > 128 || /[\0\r\n\s]/.test(diagnoseModel))) {
      res.status(400).json({ error: "model 必须是 1 到 128 字符且不能包含空白字符" }); return;
    }
    if (requiresDiagnose && judgeBackend === "api" && !DIAGNOSE_MODELS.has(diagnoseModel)) {
      res.status(400).json({ error: "API Judge 的 model 必须是 GLM-5.1 或 GLM-5.2" }); return;
    }
    const rounds = Number(maxRounds);
    const maxSessions = Number(rawMaxSessions);
    let diagnoseIntent: string;
    try {
      diagnoseIntent = requiresDiagnose ? normalizeDiagnoseIntent(rawDiagnoseIntent) : "";
    } catch (error) {
      res.status(400).json({ error: error instanceof Error ? error.message : String(error) }); return;
    }
    if (taskType === "full" && (!Number.isSafeInteger(rounds) || rounds < 1 || rounds > 100)) {
      res.status(400).json({ error: "maxRounds 必须是 1 到 100 的整数" }); return;
    }
    if (requiresDiagnose && (!Number.isSafeInteger(maxSessions) || maxSessions < 1 || maxSessions > 1000)) {
      res.status(400).json({ error: "maxSessions 必须是 1 到 1000 的整数" }); return;
    }
    if (
      improvementIdRaw
      && (!/^\d+$/.test(improvementIdRaw) || !Number.isSafeInteger(Number(improvementId)) || Number(improvementId) <= 0)
    ) {
      res.status(400).json({ error: "improvementId 必须是正整数" }); return;
    }
    if (improvementRequestId && improvementId === null) {
      res.status(400).json({ error: "improvementRequestId 必须与 improvementId 一起提供" }); return;
    }
    if (await rejectUnsupportedBotEngine(repo, res, String(userId), String(botId), String(botEnv ?? ""))) return;
    const targetRuntime = await repo.resolveEvolveBotRuntime(String(userId), String(botId), String(botEnv ?? ""));
    const serviceRuntimeSelected = targetRuntime?.botType?.toLowerCase() === "service";
    if (requiresDiagnose && serviceRuntimeSelected) sessionSourceMode = "service_export";
    if (requiresDiagnose && sessionSourceMode === "service_export"
      && !serviceRuntimeSelected && !targetRuntime?.hasServiceBot) {
      res.status(422).json({
        code: "SERVICE_SESSION_SOURCE_UNAVAILABLE",
        error: "所选草稿 Bot 没有可导出的服务态 Session",
      });
      return;
    }
    if (requiresDiagnose && judgeBackend === "api"
      && targetRuntime?.provider?.toLowerCase() === "arca") {
      res.status(422).json({
        code: "ARCA_API_JUDGE_UNSUPPORTED",
        error: "ARCA 模式只支持 Agent Judge，不支持传入 API Key",
      });
      return;
    }
    let improvementActorUserId: string | null = null;
    if (improvementId !== null) {
      if (taskType !== "diagnose") {
        res.status(400).json({ error: "改进项当前只能用于发起诊断任务" }); return;
      }
      if (!improvementRequestId) {
        res.status(400).json({ error: "从改进项发起诊断时 improvementRequestId 为必填项" }); return;
      }
      if (improvementRequestId.length > 128) {
        res.status(400).json({ error: "请求幂等键过长" }); return;
      }
      if (!improvementRepo) {
        res.status(503).json({ error: "改进项服务不可用" }); return;
      }
      improvementActorUserId = resolveRequestUserId(req);
      if (!improvementActorUserId) {
        res.status(401).json({ error: "无法识别当前登录用户" }); return;
      }
      const improvement = await improvementRepo.findItem(improvementActorUserId, improvementId);
      if (!improvement) {
        res.status(404).json({ error: "改进项不存在" }); return;
      }
      const botOwnerUserId = improvement.bot_owner_user_id || improvement.owner_user_id;
      if (String(userId) !== botOwnerUserId) {
        res.status(403).json({ error: "进化任务用户空间必须与改进项的 Bot Owner 一致" }); return;
      }
      if (improvement.bot_id !== String(botId)) {
        res.status(422).json({ error: "目标 Bot 与改进项不一致" }); return;
      }
      const existingLink = await improvementRepo.findEvolveLinkByRequest(improvementId, improvementRequestId);
      if (existingLink) {
        const existingTask = await repo.findTask(existingLink.evolve_task_id);
        if (!existingTask) {
          res.status(409).json({ error: "改进项已关联进化任务，但任务记录不存在" }); return;
        }
        res.json({
          ...publicTask(
            existingTask as unknown as Record<string, unknown>,
            await repo.listSteps(existingTask.task_id),
          ),
          idempotent: true,
        });
        return;
      }
      const improvementStatus = improvement.status.toUpperCase();
      if (improvementStatus !== "ACTIVE") {
        const message = improvementStatus === "IN_PROGRESS"
          ? "改进项已在处理中，请查看已有进化任务"
          : improvementStatus === "ARCHIVED"
            ? "改进项已废弃，请先恢复处理"
            : improvementStatus === "RESOLVED"
              ? "改进项已处理完成，无需再次发起诊断"
              : `改进项当前状态不允许发起诊断: ${improvementStatus}`;
        res.status(409).json({ code: "IMPROVEMENT_STATE_CONFLICT", error: message }); return;
      }
    }
    const taskId = evolveTaskId();
    const stepId = id("STEP");
    const clawwebUrl = getClawWebPublicBaseUrl();
    let nodeCommands: NodeCommandYamls;
    try {
      nodeCommands = parseNodeCommandYamls(
        nodeCommandYamls,
        taskType === "full" && inputMode === "direct_goal" ? ["plan", "optimize"] : [...taskNodeKeys(taskType)],
      );
    } catch (error) {
      res.status(400).json({ error: error instanceof Error ? error.message : String(error) }); return;
    }
    if (apiKey && Object.values(nodeCommandYamls ?? {}).some((value) => typeof value === "string" && value.includes(apiKey))) {
      res.status(400).json({ error: "YAML 中不能写入真实 API Key，请使用 {{api_key}} 占位符" }); return;
    }
    if (requiresDiagnose && (startDate || endDate) && (!startDate || !endDate || String(startDate) > String(endDate))) {
      res.status(400).json({ error: "开始和结束日期必须同时提供，且开始日期不能晚于结束日期" }); return;
    }
    const dispatchMode = await repo.resolveBotDispatchMode(String(userId), String(botId), String(botEnv ?? ""));
    const diagnoseTemplate = nodeCommands.diagnose ?? defaultNodeCommand("diagnose");
    const config = {
      ...(taskType === "full" ? { inputMode } : {}),
      ...(requiresDiagnose ? { model: diagnoseModel, diagnoseIntent, maxSessions } : {}),
      ...(requiresDiagnose ? { sessionSource: { mode: sessionSourceMode } } : {}),
      maxRounds: rounds,
      ...(taskType === "full" && goal ? { goal } : {}),
      ...(requiresDiagnose && startDate ? { startDate: String(startDate) } : {}),
      ...(requiresDiagnose && endDate ? { endDate: String(endDate) } : {}),
      nodeCommands: {
        ...(requiresDiagnose ? { diagnose: diagnoseTemplate } : {}),
        plan: nodeCommands.plan ?? defaultNodeCommand("plan"),
        ...(taskType === "full" ? { optimize: nodeCommands.optimize ?? defaultNodeCommand("optimize") } : {}),
      },
      dispatchMode, forceMessage, runtimeMaintenance, clawwebUrl, openclawExecutionMode, botEnv: String(botEnv ?? ""), ...(dispatchMode === "run" ? { lifecycleStage: "draft" } : {}),
    };
    const diagnoseSystemArgs: Array<[string, string | number]> = [
      ["judge-backend", judgeBackend], ["max-sessions", maxSessions],
      ["task-id", taskId], ["step-id", stepId], ["clawweb-url", clawwebUrl],
    ];
    if (sessionSourceMode === "service_export") {
      diagnoseSystemArgs.push(
        ["source", "service_export"],
        ["source-user-id", String(userId)],
        ["source-bot-id", String(botId)],
        ["source-download-network", "office"],
      );
    }
    let publicCommand = renderCommand(diagnoseTemplate, {
      api_key: "******", model: diagnoseModel, diagnose_intent: quoteCommandArgument(diagnoseIntent),
      start_date: String(startDate ?? ""), end_date: String(endDate ?? ""),
    }, diagnoseSystemArgs);
    let dispatchCommand = renderCommand(diagnoseTemplate, {
      api_key: judgeBackend === "api" ? apiKey : "******", model: diagnoseModel, diagnose_intent: quoteCommandArgument(diagnoseIntent),
      start_date: String(startDate ?? ""), end_date: String(endDate ?? ""),
    }, diagnoseSystemArgs);
    if (judgeBackend === "subagent") {
      publicCommand = withoutDiagnoseApiKey(publicCommand);
      dispatchCommand = withoutDiagnoseApiKey(dispatchCommand);
    }
    await repo.createTask({
      taskId, taskType, userId: String(userId), botId: String(botId),
      taskName: String(taskName).trim(), remark: String(remark ?? "").trim() || null,
      configJson: JSON.stringify(config), createdBy: improvementActorUserId ?? String(req.header("X-User-Id") || userId),
    });
    if (taskType === "full" && inputMode === "direct_goal") {
      const task = await repo.findTask(taskId);
      if (!task) { res.status(500).json({ error: "创建目标进化任务失败" }); return; }
      await createInitialPlanStep(req, repo, dispatch, task, targetRuntime);
      res.status(201).json(publicTask(
        task as unknown as Record<string, unknown>,
        await repo.listSteps(taskId),
      ));
      return;
    }
    const step = await repo.createStep({
      stepId, taskId, stepType: "diagnose", stepNo: 1, command: publicCommand,
    });
    if (improvementId !== null && improvementRequestId && improvementActorUserId && improvementRepo) {
      try {
        await improvementRepo.linkEvolveTask({
          improvementId,
          ownerUserId: improvementActorUserId,
          evolveTaskId: taskId,
          requestId: improvementRequestId,
          createdBy: improvementActorUserId,
        });
      } catch (error) {
        await repo.deleteTask(taskId);
        if (isInsightBoundaryError(error) && error.code === "IMPROVEMENT_STATE_CONFLICT") {
          const existingLink = await improvementRepo.findEvolveLinkByRequest(
            improvementId,
            improvementRequestId,
          );
          const existingTask = existingLink
            ? await repo.findTask(existingLink.evolve_task_id)
            : null;
          if (existingTask) {
            res.json({
              ...publicTask(
                existingTask as unknown as Record<string, unknown>,
                await repo.listSteps(existingTask.task_id),
              ),
              idempotent: true,
            });
            return;
          }
          res.status(409).json({ code: error.code, error: error.message }); return;
        }
        throw error;
      }
    }
    const transport = resolveEvolveTransport({ stepType: "diagnose", runtime: targetRuntime, forceMessage });
    await startInitialEvolveStep({
      repo, dispatch,
      task: { task_id: taskId, user_id: String(userId), bot_id: String(botId) },
      businessStep: step,
      runtime: targetRuntime,
      clawwebUrl,
      callbackUrl: (createdStepId) => botCallbackUrl(req, taskId, createdStepId),
      businessDispatch: {
        taskId, stepId: stepId, stepType: "diagnose", botId: String(botId),
        userId: String(userId), mode: dispatchMode,
        callbackUrl: botCallbackUrl(req, taskId, stepId),
        command: transport === "baas_execute_command" && judgeBackend === "api"
          ? withoutDiagnoseApiKey(publicCommand)
          : dispatchCommand,
        runtime: targetRuntime,
        forceMessage,
        runtimeMaintenance,
        ...(transport === "baas_execute_command" && judgeBackend === "api" ? {
          secrets: { diagnoseApiKey: apiKey },
        } : {}),
      },
    });
    const task = await repo.findTask(taskId);
    res.status(201).json(publicTask(
      task as unknown as Record<string, unknown>,
      await repo.listSteps(taskId),
    ));
  }));

  router.post("/optimizations", asyncHandler(async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const {
      taskName, remark, userId, botId, botEnv, sourceDiagnosisTaskIds, maxRounds = 3, nodeCommandYamls,
      forceMessage: rawForceMessage, runtimeMaintenance: rawRuntimeMaintenance,
      openclawExecutionMode: rawOpenClawExecutionMode,
    } = req.body ?? {};
    const sourceIds = Array.isArray(sourceDiagnosisTaskIds)
      ? [...new Set(sourceDiagnosisTaskIds.map(String).filter(Boolean))]
      : [];
    const rounds = Number(maxRounds);
    const forceMessage = rawForceMessage === true;
    let openclawExecutionMode: "local" | "gateway";
    try { openclawExecutionMode = resolveOpenClawExecutionMode(rawOpenClawExecutionMode); }
    catch (error) { res.status(400).json({ error: error instanceof Error ? error.message : String(error) }); return; }
    let nodeCommands: NodeCommandYamls;
    try {
      nodeCommands = parseNodeCommandYamls(nodeCommandYamls, [...taskNodeKeys("optimize")]);
    } catch (error) {
      res.status(400).json({ error: error instanceof Error ? error.message : String(error) }); return;
    }
    if (!String(taskName ?? "").trim() || !userId || !botId) {
      res.status(400).json({ error: "taskName、userId、botId 为必填项" }); return;
    }
    if (String(taskName).trim().length > 128 || String(remark ?? "").length > 1000) {
      res.status(400).json({ error: "任务名称不能超过128字，备注不能超过1000字" }); return;
    }
    if (!Number.isSafeInteger(rounds) || rounds < 1 || rounds > 100) {
      res.status(400).json({ error: "maxRounds 必须是 1 到 100 的整数" }); return;
    }
    if (!sourceIds.length) {
      res.status(400).json({ error: "诊断进化必须选择一个已完成 Plan 的诊断任务" }); return;
    }
    if (await rejectUnsupportedBotEngine(repo, res, String(userId), String(botId), String(botEnv ?? ""))) return;
    let primaryBenchDomains: BenchDomains | null = null;
    for (const [index, sourceTaskId] of sourceIds.entries()) {
      const sourceTask = await repo.findTask(sourceTaskId);
      if (!sourceTask || sourceTask.user_id !== String(userId) || sourceTask.bot_id !== String(botId)) {
        res.status(422).json({ error: `诊断任务不存在或进化对象不一致: ${sourceTaskId}` }); return;
      }
      const sourceSteps = await repo.listSteps(sourceTaskId);
      if (!sourceSteps.some((item) => item.step_type === "diagnose" && item.status === "succeeded")
        || !sourceSteps.some((item) => item.step_type === "plan" && item.status === "succeeded")) {
        res.status(422).json({ error: `诊断任务尚未产出成功的 diagnose 和 plan: ${sourceTaskId}` }); return;
      }
      if (index === 0) {
        const plan = sourceSteps.filter((item) => item.step_type === "plan" && item.status === "succeeded").at(-1);
        const domains = (parseJson(plan?.output_json ?? null) as { benchDomains?: BenchDomains } | null)?.benchDomains;
        if (!domains?.trainBenchDomainId || !domains.testBenchDomainId) {
          res.status(422).json({ error: `主诊断任务 Plan 未上报完整 benchDomains: ${sourceTaskId}` }); return;
        }
        primaryBenchDomains = domains;
      }
    }
    const taskId = evolveTaskId();
    const clawwebUrl = getClawWebPublicBaseUrl();
    const dispatchMode = await repo.resolveBotDispatchMode(String(userId), String(botId), String(botEnv ?? ""));
    await repo.createTask({
      taskId, taskType: "optimize", userId: String(userId), botId: String(botId),
      taskName: String(taskName).trim(), remark: String(remark ?? "").trim() || null,
      configJson: JSON.stringify({
        sourceDiagnosisTaskIds: sourceIds,
        trainBenchDomainId: primaryBenchDomains?.trainBenchDomainId,
        testBenchDomainId: primaryBenchDomains?.testBenchDomainId,
        maxRounds: rounds, dispatchMode, forceMessage, runtimeMaintenance: rawRuntimeMaintenance !== false, clawwebUrl, openclawExecutionMode, botEnv: String(botEnv ?? ""),
        nodeCommands: { optimize: nodeCommands.optimize ?? defaultNodeCommand("optimize") },
      }),
      createdBy: String(req.header("X-User-Id") || userId),
    });
    const task = await repo.findTask(taskId);
    if (!task) { res.status(500).json({ error: "创建优化任务失败" }); return; }
    await createOptimizeStep(req, repo, dispatch, task, 1, true);
    res.status(201).json(publicTask(task as unknown as Record<string, unknown>, await repo.listSteps(taskId)));
  }));

  router.post("/bench-optimizations", asyncHandler(async (req: Request, res: Response) => {
    if (!repo || !benchDomainRepo || !benchTemplateRepo) {
      res.status(503).json({ error: "Bench 或 Evolve 数据库不可用" }); return;
    }
    const {
      taskName, remark, userId, botId, botEnv, objective, trainBenchDomainId, testBenchDomainId,
      maxRounds = 3, nodeCommandYamls, forceMessage: rawForceMessage, runtimeMaintenance: rawRuntimeMaintenance,
      openclawExecutionMode: rawOpenClawExecutionMode,
    } = req.body ?? {};
    const ownerUserId = String(userId ?? "").trim();
    const trainDomainId = String(trainBenchDomainId ?? "").trim();
    const testDomainId = String(testBenchDomainId ?? "").trim();
    const rounds = Number(maxRounds);
    const objectiveText = String(objective ?? "").trim();
    let openclawExecutionMode: "local" | "gateway";
    try { openclawExecutionMode = resolveOpenClawExecutionMode(rawOpenClawExecutionMode); }
    catch (error) { res.status(400).json({ error: error instanceof Error ? error.message : String(error) }); return; }
    if (!String(taskName ?? "").trim() || !ownerUserId || !botId || !objectiveText || !trainDomainId || !testDomainId) {
      res.status(400).json({ error: "taskName、userId、botId、objective、trainBenchDomainId、testBenchDomainId 为必填项" }); return;
    }
    if (Buffer.byteLength(objectiveText, "utf8") > 4096) {
      res.status(400).json({ error: "objective 不能超过 4 KiB" }); return;
    }
    if (!Number.isSafeInteger(rounds) || rounds < 1 || rounds > 100) {
      res.status(400).json({ error: "maxRounds 必须是 1 到 100 的整数" }); return;
    }
    const actorUserId = resolveRequestUserId(req);
    if (!actorUserId) { res.status(401).json({ error: "无法识别当前登录用户" }); return; }
    if (actorUserId !== ownerUserId) {
      res.status(403).json({ error: "Bench 进化只能在当前登录用户空间发起" }); return;
    }
    if (await rejectUnsupportedBotEngine(repo, res, ownerUserId, String(botId), String(botEnv ?? ""))) return;
    const pinnedDomains: Record<string, Array<{ templateName: string; templateVersion: number }>> = {};
    for (const domainId of [...new Set([trainDomainId, testDomainId])]) {
      const domain = await benchDomainRepo.findByOwnerAndDomainId(ownerUserId, domainId);
      if (!domain || domain.status !== "active") {
        res.status(422).json({ error: `Bench Domain 不存在、无权访问或已归档: ${domainId}` }); return;
      }
      const templates = (await benchTemplateRepo.listAll({ ownerUserId, domainId }))
        .filter((item) => item.status === "published" && item.published_version != null);
      if (!templates.length) {
        res.status(422).json({ error: `Bench Domain 没有已发布模板: ${domainId}` }); return;
      }
      pinnedDomains[domainId] = templates.map((item) => ({
        templateName: item.template_name, templateVersion: Number(item.published_version),
      }));
    }
    let nodeCommands: NodeCommandYamls;
    try { nodeCommands = parseNodeCommandYamls(nodeCommandYamls, [...taskNodeKeys("bench_optimize")]); }
    catch (error) { res.status(400).json({ error: error instanceof Error ? error.message : String(error) }); return; }
    const taskId = evolveTaskId();
    const stepId = id("STEP");
    const clawwebUrl = getClawWebPublicBaseUrl();
    const dispatchMode = await repo.resolveBotDispatchMode(ownerUserId, String(botId), String(botEnv ?? ""));
    const forceMessage = rawForceMessage === true;
    const runtimeMaintenance = rawRuntimeMaintenance !== false;
    const commandTemplate = nodeCommands.bench_plan ?? defaultNodeCommand("bench_plan");
    const command = renderCommand(commandTemplate, {
      train_bench_domain_id: trainDomainId, test_bench_domain_id: testDomainId,
    }, [
      ["task-id", taskId], ["step-id", stepId], ["owner-id", ownerUserId],
      ["train-domain-id", trainDomainId], ["test-domain-id", testDomainId],
      ["clawweb-url", clawwebUrl],
      ["openclaw-execution-mode", openclawExecutionMode],
    ]);
    await repo.createTask({
      taskId, taskType: "bench_optimize", userId: ownerUserId, botId: String(botId),
      taskName: String(taskName).trim(), remark: String(remark ?? "").trim() || null,
      configJson: JSON.stringify({
        objective: objectiveText, ownerUserId, trainBenchDomainId: trainDomainId, testBenchDomainId: testDomainId,
        pinnedBenchDomains: pinnedDomains, maxRounds: rounds,
        nodeCommands: {
          bench_plan: commandTemplate,
          optimize: nodeCommands.optimize ?? defaultNodeCommand("optimize"),
        },
        dispatchMode, forceMessage, runtimeMaintenance, clawwebUrl, openclawExecutionMode, botEnv: String(botEnv ?? ""),
      }),
      createdBy: actorUserId,
    });
    const step = await repo.createStep({ stepId, taskId, stepType: "bench_plan", stepNo: 1, command });
    const runtime = await repo.resolveEvolveBotRuntime(ownerUserId, String(botId), String(botEnv ?? ""));
    await startInitialEvolveStep({
      repo, dispatch,
      task: { task_id: taskId, user_id: ownerUserId, bot_id: String(botId) },
      businessStep: step, runtime, clawwebUrl,
      callbackUrl: (createdStepId) => botCallbackUrl(req, taskId, createdStepId),
      businessDispatch: {
        taskId, stepId, stepType: "bench_plan", userId: ownerUserId,
        botId: String(botId), command, mode: dispatchMode,
        callbackUrl: botCallbackUrl(req, taskId, stepId), runtime, forceMessage, runtimeMaintenance,
      },
    });
    const task = await repo.findTask(taskId);
    res.status(201).json(publicTask(task as unknown as Record<string, unknown>, await repo.listSteps(taskId)));
  }));

  router.post("/packs", asyncHandler(async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const { taskName, remark, userId, botId, botEnv, forceMessage: rawForceMessage } = req.body ?? {};
    const actor = resolveRequestUserId(req);
    if (!taskName || !userId || !botId) { res.status(400).json({ error: "taskName、userId、botId 为必填项" }); return; }
    if (!actor || actor !== String(userId)) { res.status(403).json({ error: "只能为自己的 Bot 创建 Pack" }); return; }
    if (await rejectUnsupportedBotEngine(repo, res, String(userId), String(botId), String(botEnv ?? ""))) return;
    const taskId = evolveTaskId(), stepId = id("STEP");
    const clawwebUrl = getClawWebPublicBaseUrl();
    const dispatchMode = await repo.resolveBotDispatchMode(String(userId), String(botId), String(botEnv ?? ""));
    const forceMessage = rawForceMessage === true;
    const command = `/clawevolve-pack --mode pack --task-id ${taskId} --step-id ${stepId} --clawweb-url ${clawwebUrl}`;
    await repo.createTask({ taskId, taskType: "pack", userId: String(userId), botId: String(botId), taskName: String(taskName).trim(), remark: String(remark ?? "").trim() || null, configJson: JSON.stringify({ dispatchMode, forceMessage, runtimeMaintenance: false, clawwebUrl, botEnv: String(botEnv ?? "") }), createdBy: actor });
    const step = await repo.createStep({ stepId, taskId, stepType: "pack", stepNo: 1, command });
    const runtime = await repo.resolveEvolveBotRuntime(String(userId), String(botId), String(botEnv ?? ""));
    await startInitialEvolveStep({
      repo, dispatch,
      task: { task_id: taskId, user_id: String(userId), bot_id: String(botId) },
      businessStep: step, runtime, clawwebUrl,
      callbackUrl: (createdStepId) => botCallbackUrl(req, taskId, createdStepId),
      businessDispatch: {
        taskId, stepId, stepType: "pack", userId: String(userId), botId: String(botId),
        command, mode: dispatchMode, callbackUrl: botCallbackUrl(req, taskId, stepId), runtime, forceMessage, runtimeMaintenance: false,
      },
    });
    const task = await repo.findTask(taskId);
    res.status(201).json(publicTask(task as unknown as Record<string, unknown>, await repo.listSteps(taskId)));
  }));

  router.post("/pack-restores", asyncHandler(async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const { taskName, remark, userId, botId, botEnv, packId, sourceTaskId, sourceKind, sourceRound, forceMessage: rawForceMessage } = req.body ?? {};
    const actor = resolveRequestUserId(req);
    const requestedPackId = String(packId ?? "").trim();
    const requestedSourceTaskId = String(sourceTaskId ?? "").trim();
    const requestedSourceKind = String(sourceKind ?? "").trim();
    if (!taskName || !userId || !botId || (!requestedPackId && !requestedSourceTaskId)) {
      res.status(400).json({ error: "恢复参数不合法" }); return;
    }
    if (!actor || actor !== String(userId)) { res.status(403).json({ error: "只能恢复自己的 Bot" }); return; }
    let registeredPack: EvolvePackRow | null | undefined;
    if (requestedPackId) {
      registeredPack = await repo.findPack(requestedPackId);
    } else {
      if (!new Set(["baseline", "snapshot", "round"]).has(requestedSourceKind)) {
        res.status(400).json({ error: "恢复参数不合法" }); return;
      }
      const requestedRound = requestedSourceKind === "round" ? Number(sourceRound) : 0;
      if (requestedSourceKind === "round" && (!Number.isSafeInteger(requestedRound) || requestedRound < 1)) {
        res.status(400).json({ error: "sourceRound 必须是正整数" }); return;
      }
      registeredPack = (await repo.listPacks(String(userId), String(botId))).find((pack) =>
        pack.source_task_id === requestedSourceTaskId && pack.source_kind === requestedSourceKind
          && pack.source_round === requestedRound);
    }
    if (!registeredPack || registeredPack.status !== "available") {
      res.status(422).json({ error: "Pack 未登记或不可用" }); return;
    }
    if (registeredPack.user_id !== String(userId) || registeredPack.bot_id !== String(botId)) {
      res.status(422).json({ error: "Pack 登记信息与恢复请求不一致" }); return;
    }
    if ((requestedSourceTaskId && registeredPack.source_task_id !== requestedSourceTaskId)
      || (requestedSourceKind && registeredPack.source_kind !== requestedSourceKind)
      || (sourceRound !== undefined && registeredPack.source_round !== Number(sourceRound))) {
      res.status(422).json({ error: "Pack 登记信息与恢复请求不一致" }); return;
    }
    const effectiveSourceTaskId = registeredPack.source_task_id;
    const effectiveSourceKind = registeredPack.source_kind;
    const effectiveSourceRound = registeredPack.source_round;
    const source = await repo.findTask(effectiveSourceTaskId);
    const sourceConfig = source
      ? ((parseJson(source.config_json) as { botEnv?: string } | null) ?? {})
      : {};
    const targetBotEnv = String(botEnv ?? sourceConfig.botEnv ?? "");
    const activeRestore = await repo.findActiveRestoreTask(String(userId), String(botId));
    if (activeRestore) {
      res.status(409).json({ code: "RESTORE_ALREADY_RUNNING", error: "该 Bot 已有恢复任务运行中", taskId: activeRestore.task_id }); return;
    }
    const selected = {
      stepId: registeredPack.source_step_id,
      artifact: {
        kind: effectiveSourceKind === "baseline" ? "baseline_pack" : "pack",
        ref: registeredPack.artifact_ref, size: Number(registeredPack.artifact_size),
        sha256: registeredPack.artifact_sha256, contentType: registeredPack.artifact_content_type,
      },
    };
    try { validatePackArtifact(selected.artifact); } catch (validationError) { res.status(422).json({ error: validationError instanceof Error ? validationError.message : String(validationError) }); return; }
    if (await rejectUnsupportedBotEngine(repo, res, String(userId), String(botId), targetBotEnv)) return;
    const taskId = evolveTaskId(), stepId = id("STEP");
    const clawwebUrl = getClawWebPublicBaseUrl();
    const dispatchMode = await repo.resolveBotDispatchMode(String(userId), String(botId), targetBotEnv); const forceMessage = rawForceMessage === true;
    const command = `/clawevolve-pack --mode restore --task-id ${taskId} --step-id ${stepId} --source-task-id ${effectiveSourceTaskId} --source-kind ${effectiveSourceKind}${effectiveSourceKind === "round" ? ` --source-round ${effectiveSourceRound}` : ""} --clawweb-url ${clawwebUrl}`;
    await repo.createTask({ taskId, taskType: "pack_restore", userId: String(userId), botId: String(botId), taskName: String(taskName).trim(), remark: String(remark ?? "").trim() || null, configJson: JSON.stringify({ packId: registeredPack.pack_id, sourceTaskId: effectiveSourceTaskId, sourceStepId: selected.stepId, sourceKind: effectiveSourceKind, sourceRound: effectiveSourceKind === "round" ? effectiveSourceRound : null, artifact: selected.artifact, dispatchMode, forceMessage, runtimeMaintenance: false, clawwebUrl, botEnv: targetBotEnv }), createdBy: actor });
    const step = await repo.createStep({ stepId, taskId, stepType: "restore", stepNo: 1, command });
    const runtime = await repo.resolveEvolveBotRuntime(String(userId), String(botId), targetBotEnv);
    await startInitialEvolveStep({
      repo, dispatch,
      task: { task_id: taskId, user_id: String(userId), bot_id: String(botId) },
      businessStep: step, runtime, clawwebUrl,
      callbackUrl: (createdStepId) => botCallbackUrl(req, taskId, createdStepId),
      businessDispatch: {
        taskId, stepId, stepType: "restore", userId: String(userId), botId: String(botId),
        command, mode: dispatchMode, callbackUrl: botCallbackUrl(req, taskId, stepId), runtime, forceMessage, runtimeMaintenance: false,
      },
    });
    const task = await repo.findTask(taskId);
    res.status(201).json(publicTask(task as unknown as Record<string, unknown>, await repo.listSteps(taskId)));
  }));

  router.post("/runtime-cleanups", asyncHandler(async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const { taskName, remark, userId, botId, botEnv, forceCleanup: rawForceCleanup } = req.body ?? {};
    const actor = resolveRequestUserId(req);
    const ownerUserId = String(userId ?? "").trim();
    const targetBotId = String(botId ?? "").trim();
    const targetBotEnv = String(botEnv ?? "").trim();
    const forceCleanup = rawForceCleanup === true;
    if (!String(taskName ?? "").trim() || !ownerUserId || !targetBotId) {
      res.status(400).json({ error: "taskName、userId、botId 为必填项" }); return;
    }
    if (!actor || actor !== ownerUserId) {
      res.status(403).json({ error: "只能清理自己的 Bot 进化运行记录" }); return;
    }
    if (String(taskName).trim().length > 128 || String(remark ?? "").length > 1000) {
      res.status(400).json({ error: "任务名称不能超过128字，备注不能超过1000字" }); return;
    }
    if (await rejectUnsupportedBotEngine(repo, res, ownerUserId, targetBotId, targetBotEnv)) return;

    const activeTasks = (await repo.listActiveBotEvolveTasks(ownerUserId, targetBotId))
      .filter((task) => !targetBotEnv || taskBotEnv(task) === targetBotEnv);
    if (activeTasks.length && !forceCleanup) {
      res.status(409).json({
        code: "EVOLVE_TASKS_STILL_RUNNING",
        error: "该 Bot 仍有进化任务运行中，请先停止任务；也可以确认后强制清理",
        activeTasks: activeTasks.map((task) => ({ taskId: task.task_id, taskName: task.task_name, status: task.status })),
      });
      return;
    }

    const taskId = evolveTaskId();
    const stepId = id("STEP");
    const clawwebUrl = getClawWebPublicBaseUrl();
    const dispatchMode = await repo.resolveBotDispatchMode(ownerUserId, targetBotId, targetBotEnv);
    const command = `/clawevolve-runtime-cleanup --task-id ${taskId} --step-id ${stepId} --clawweb-url ${clawwebUrl}${forceCleanup ? " --force-cleanup" : ""}`;
    await repo.createTask({
      taskId, taskType: "runtime_cleanup", userId: ownerUserId, botId: targetBotId,
      taskName: String(taskName).trim(), remark: String(remark ?? "").trim() || null,
      configJson: JSON.stringify({
        scope: "bot_history", forceCleanup, dispatchMode, runtimeMaintenance: false,
        clawwebUrl, botEnv: targetBotEnv,
      }),
      createdBy: actor,
    });
    const step = await repo.createStep({
      stepId, taskId, stepType: "runtime_cleanup", stepNo: 1, command,
    });
    const runtime = await repo.resolveEvolveBotRuntime(ownerUserId, targetBotId, targetBotEnv);
    await startInitialEvolveStep({
      repo, dispatch,
      task: { task_id: taskId, user_id: ownerUserId, bot_id: targetBotId },
      businessStep: step, runtime, clawwebUrl,
      callbackUrl: (createdStepId) => botCallbackUrl(req, taskId, createdStepId),
      businessDispatch: {
        taskId, stepId, stepType: "runtime_cleanup", userId: ownerUserId, botId: targetBotId,
        command, mode: dispatchMode, callbackUrl: botCallbackUrl(req, taskId, stepId),
        runtime, runtimeMaintenance: false,
      },
    });
    const task = await repo.findTask(taskId);
    res.status(201).json(publicTask(task as unknown as Record<string, unknown>, await repo.listSteps(taskId)));
  }));

  router.get("/packs", asyncHandler(async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const actor = resolveRequestUserId(req); const botId = String(req.query.botId ?? "").trim();
    if (!actor) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    const scope = String(req.query.scope ?? "mine") === "all" ? "all" : "mine";
    if (scope === "all" && !req.isClawEvolveAdmin) { res.status(403).json({ error: "Forbidden", message: "ClawEvolve 管理员权限不足" }); return; }
    const ownerUserId = String(req.query.ownerUserId ?? "").trim();
    const packs = await repo.listPacks(scope === "all" ? (ownerUserId || null) : actor, botId || undefined);
    const applicationCounts = await repo.countPackApplications(packs);
    res.json({ items: packs.map((pack) => ({
      packId: pack.pack_id, taskId: pack.source_task_id, stepId: pack.source_step_id,
      userId: pack.user_id, botId: pack.bot_id,
      sourceKind: pack.source_kind, sourceRound: pack.source_round || null,
      createdAt: pack.gmt_create, status: pack.status,
      artifact: { ref: pack.artifact_ref, size: Number(pack.artifact_size), sha256: pack.artifact_sha256, contentType: pack.artifact_content_type },
      applicationCount: applicationCounts[pack.pack_id] ?? 0,
    })) });
  }));

  router.get("/versions", asyncHandler(async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const actor = resolveRequestUserId(req);
    const botId = String(req.query.botId ?? "").trim();
    if (!actor) { res.status(401).json({ error: "无法识别当前用户" }); return; }
    const scope = String(req.query.scope ?? "mine") === "all" ? "all" : "mine";
    if (scope === "all" && !req.isClawEvolveAdmin) {
      res.status(403).json({ error: "Forbidden", message: "ClawEvolve 管理员权限不足" }); return;
    }
    const ownerUserId = String(req.query.ownerUserId ?? "").trim();
    const visibleOwner = scope === "all" ? (ownerUserId || null) : actor;
    const [packs, optimizeSteps] = await Promise.all([
      repo.listPacks(visibleOwner, botId || undefined),
      repo.listCompletedOptimizeVersions(visibleOwner, botId || undefined),
    ]);
    const roundPackByStep = new Map(packs
      .filter((pack) => pack.source_kind === "round")
      .map((pack) => [`${pack.source_task_id}:${pack.source_step_id}:${pack.source_round}`, pack]));
    const consumedPackIds = new Set<string>();
    const items: Array<Record<string, unknown>> = optimizeSteps.map((step) => {
      const output = parseJson(step.output_json) as Record<string, unknown> | null;
      const packOutput = isRecord(output?.pack) ? output.pack : null;
      const outputArtifact = isRecord(packOutput?.artifact) ? packOutput.artifact : null;
      const registeredPack = roundPackByStep.get(`${step.task_id}:${step.step_id}:${Number(step.round_no ?? 0)}`);
      if (registeredPack) consumedPackIds.add(registeredPack.pack_id);
      const benchDecision = nonEmptyString(output?.benchDecision) ? String(output?.benchDecision) : null;
      const reportedPackAvailable = Boolean(packOutput?.status === "available" && nonEmptyString(outputArtifact?.ref));
      const packAvailable = Boolean(registeredPack?.artifact_ref);
      const explicitAccepted = typeof output?.accepted === "boolean" ? output.accepted : null;
      const promotionStatus = nonEmptyString(output?.promotionStatus) ? String(output?.promotionStatus) : null;
      // Reports created before accepted/promotionStatus were additive fields
      // can be identified safely: upload-clawweb runs only after complete, so
      // a succeeded Step with passed Bench and a registered Pack represents a
      // completed historical promotion. New reports always use explicit facts.
      const accepted = explicitAccepted ?? (benchDecision === "passed" && packAvailable ? true : null);
      const stateSource = explicitAccepted === null ? "legacy_inferred" : "skill_output";
      const acceptanceStatus = accepted === true && packAvailable
        ? "accepted"
        : accepted === true ? "accepted_unregistered"
          : benchDecision === "passed" && promotionStatus === "failed" ? "promotion_failed"
            : benchDecision === "passed" ? "passed_not_promoted"
              : benchDecision === "not_improved" ? "rejected"
                : reportedPackAvailable ? "unregistered" : "unknown";
      const scoreComparison = isRecord(output?.scoreComparison) ? output.scoreComparison : null;
      const diff = isRecord(output?.diff) ? output.diff : null;
      const spec = isRecord(output?.spec) ? output.spec : null;
      const reviewStatus = nonEmptyString(output?.reviewStatus) ? String(output?.reviewStatus) : null;
      let diffArtifactAvailable = false;
      if (diff?.artifact) {
        try {
          parseEvolveArtifactRef(diff.artifact, {
            taskId: step.task_id, round: Number(step.round_no ?? 0), kind: "diff",
          });
          diffArtifactAvailable = true;
        } catch {
          // Historical or incomplete artifact metadata remains reviewable via summary/files only.
        }
      }
      return {
        versionId: `ROUND:${step.task_id}:${step.step_id}`,
        kind: "round",
        acceptanceStatus,
        userId: step.owner_user_id,
        botId: step.source_bot_id,
        taskId: step.task_id,
        taskName: step.source_task_name,
        taskType: step.source_task_type,
        stepId: step.step_id,
        round: Number(step.round_no ?? 0),
        createdAt: step.completed_at ?? step.gmt_modified,
        benchDecision,
        accepted,
        promotionStatus,
        stateSource,
        reviewStatus,
        scoreComparison: scoreComparison ? {
          name: scoreComparison.name ?? null,
          baseline: scoreComparison.baseline ?? null,
          candidate: scoreComparison.candidate ?? null,
          delta: scoreComparison.delta ?? null,
        } : null,
        specVersion: spec && nonEmptyString(spec.version) ? spec.version : null,
        diff: diff ? {
          summary: nonEmptyString(diff.summary) ? diff.summary : null,
          files: Array.isArray(diff.files) ? diff.files : [],
          available: Boolean(diffArtifactAvailable || diff.summary || (Array.isArray(diff.files) && diff.files.length)),
          artifactAvailable: diffArtifactAvailable,
        } : null,
        reportedPack: packOutput ? {
          status: nonEmptyString(packOutput.status) ? packOutput.status : null,
          artifact: outputArtifact && nonEmptyString(outputArtifact.ref) ? {
            ref: outputArtifact.ref,
            size: Number(outputArtifact.size ?? 0),
            sha256: outputArtifact.sha256,
          } : null,
        } : null,
        pack: registeredPack ? {
          packId: registeredPack.pack_id,
          status: registeredPack.status,
          artifact: {
            ref: registeredPack.artifact_ref,
            size: Number(registeredPack.artifact_size),
            sha256: registeredPack.artifact_sha256,
          },
        } : null,
      };
    });
    for (const pack of packs) {
      if (consumedPackIds.has(pack.pack_id)) continue;
      items.push({
        versionId: `PACK:${pack.pack_id}`,
        kind: pack.source_kind === "baseline" ? "initial" : pack.source_kind,
        acceptanceStatus: pack.source_kind === "round" ? "unknown" : "unassessed",
        userId: pack.user_id,
        botId: pack.bot_id,
        taskId: pack.source_task_id,
        taskName: null,
        taskType: pack.source_kind === "snapshot" ? "pack" : null,
        stepId: pack.source_step_id,
        round: pack.source_round || null,
        createdAt: pack.gmt_create,
        benchDecision: null,
        reviewStatus: null,
        scoreComparison: null,
        specVersion: null,
        diff: null,
        reportedPack: null,
        pack: {
          packId: pack.pack_id,
          status: pack.status,
          artifact: {
            ref: pack.artifact_ref,
            size: Number(pack.artifact_size),
            sha256: pack.artifact_sha256,
          },
        },
      });
    }
    const timestamp = (value: unknown) => {
      if (typeof value === "number") return value;
      const numeric = Number(value);
      if (Number.isFinite(numeric)) return numeric;
      const parsed = Date.parse(String(value ?? ""));
      return Number.isFinite(parsed) ? parsed : 0;
    };
    items.sort((left, right) => timestamp(right.createdAt) - timestamp(left.createdAt));
    res.json({ items });
  }));

  router.get("/admin/owners", asyncHandler(async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    if (!req.isClawEvolveAdmin) { res.status(403).json({ error: "Forbidden", message: "ClawEvolve 管理员权限不足" }); return; }
    res.json({ ownerUserIds: await repo.listEvolveOwnerUserIds() });
  }));

  router.get("/packs/:packId", asyncHandler(async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const actor = resolveRequestUserId(req); const pack = await repo.findPack(String(req.params.packId));
    if (!pack || !actor || (pack.user_id !== actor && !req.isClawEvolveAdmin)) { res.status(404).json({ error: "Pack 不存在" }); return; }
    const sourceTask = await repo.findTask(pack.source_task_id);
    const applications = await repo.listPackApplications(pack);
    res.json({
      pack: {
        packId: pack.pack_id, userId: pack.user_id, botId: pack.bot_id,
        taskId: pack.source_task_id, stepId: pack.source_step_id,
        sourceKind: pack.source_kind, sourceRound: pack.source_round || null,
        createdAt: pack.gmt_create, status: pack.status,
        artifact: { ref: pack.artifact_ref, size: Number(pack.artifact_size), sha256: pack.artifact_sha256, contentType: pack.artifact_content_type },
      },
      sourceTask: sourceTask ? publicTask(sourceTask as unknown as Record<string, unknown>) : null,
      applications: await Promise.all(applications.map(async (task) => publicTask(
        task as unknown as Record<string, unknown>, await repo.listSteps(task.task_id),
      ))),
    });
  }));

  router.get("/tasks", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const scope = String(req.query.scope ?? "mine") === "all" ? "all" : "mine";
    const page = Math.max(1, Number.parseInt(String(req.query.page ?? "1"), 10) || 1);
    const pageSize = Math.min(50, Math.max(1, Number.parseInt(String(req.query.pageSize ?? "20"), 10) || 20));
    const categories: Record<string, string[]> = {
      diagnosis: ["diagnose", "bench", "session_analysis", "session_export"],
      optimization: ["optimize", "bench_optimize"],
      repair: ["repair"],
      deployment: ["apply", "pack", "pack_restore", "runtime_cleanup"],
      full: ["full"],
    };
    const statusGroups: Record<string, string[]> = {
      running: ["pending", "accepted", "dispatched", "running", "waiting_approval", "waiting_acceptance", "waiting_context"],
      success: ["completed", "succeeded"],
      failed: ["failed", "canceled"],
    };
    const requestedCategory = String(req.query.category ?? "all");
    const category = requestedCategory === "all" || Object.hasOwn(categories, requestedCategory)
      ? requestedCategory
      : "all";
    if (scope === "all" && !req.isClawEvolveAdmin) {
      res.status(403).json({ error: "Forbidden", message: "ClawEvolve 管理员权限不足" }); return;
    }
    const actor = resolveRequestUserId(req);
    if (!actor) { res.status(401).json({ error: "Unauthorized" }); return; }
    const status = String(req.query.status ?? "all");
    const result = await repo.listTasksPage({
      createdBy: scope === "all" ? null : actor,
      ownerUserId: scope === "all" ? String(req.query.ownerUserId ?? "").trim() || null : null,
      page,
      pageSize,
      taskTypes: categories[category],
      statuses: statusGroups[status],
      query: String(req.query.query ?? ""),
    });
    res.json({ tasks: await Promise.all(result.rows.map(async (row) => withTaskSource(
      publicTask(row as unknown as Record<string, unknown>, await repo.listSteps(row.task_id)),
      row,
      taskSourceService,
    ))), page, pageSize, total: result.total, totalPages: Math.max(1, Math.ceil(result.total / pageSize)), scope, canViewAll: req.isClawEvolveAdmin === true });
  }));

  router.use("/tasks/:taskId", asyncHandler(async (req, res, next) => {
    if (!repo) { next(); return; }
    const task = await repo.findTask(String(req.params.taskId));
    if (task?.task_type === "repair") {
      res.status(404).json({ error: "任务不存在" }); return;
    }
    next();
  }));

  router.get("/tasks/:taskId", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const task = await repo.findTask(String(req.params.taskId));
    if (!task || ["session_analysis", "session_export"].includes(task.task_type)) { res.status(404).json({ error: "任务不存在" }); return; }
    if (!canReadTask(req, task)) { res.status(403).json({ code: "TASK_NOT_SHARED", error: "权限不足，请联系任务 Owner 开启分享" }); return; }
    const initialPack = (await repo.listPacks(task.user_id, task.bot_id)).find((pack) =>
      pack.source_task_id === task.task_id && pack.source_kind === "baseline" && pack.status === "available");
    const view = await withTaskSource(
      publicTask(
        task as unknown as Record<string, unknown>,
        await repo.listSteps(task.task_id),
        true,
      ),
      task,
      taskSourceService,
    );
    res.json({
      ...view,
      initialPack: initialPack ? {
        packId: initialPack.pack_id,
        taskId: initialPack.source_task_id,
        stepId: initialPack.source_step_id,
        sourceKind: initialPack.source_kind,
        status: initialPack.status,
        artifact: {
          ref: initialPack.artifact_ref,
          size: Number(initialPack.artifact_size),
          sha256: initialPack.artifact_sha256,
          contentType: initialPack.artifact_content_type,
        },
      } : null,
    });
  }));

  router.get("/tasks/:taskId/log-archives", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const task = await repo.findTask(String(req.params.taskId));
    if (!task) { res.status(404).json({ error: "任务不存在" }); return; }
    if (!canManageTaskLogs(req, task)) { res.status(403).json({ error: "只有任务 Owner 或管理员可获取日志" }); return; }
    res.json({ items: (await repo.listTaskLogArchives(task.task_id)).map(taskLogArchiveView) });
  }));

  router.post("/tasks/:taskId/log-archives", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const task = await repo.findTask(String(req.params.taskId));
    if (!task) { res.status(404).json({ error: "任务不存在" }); return; }
    if (!canManageTaskLogs(req, task)) { res.status(403).json({ error: "只有任务 Owner 或管理员可获取日志" }); return; }
    let active;
    try {
      active = await repo.findActiveTaskLogArchive(task.task_id);
    } catch (error) {
      console.error("[evolve-task-log] archive storage unavailable", {
        taskId: task.task_id,
        error: error instanceof Error ? error.message : String(error),
      });
      res.status(503).json({
        code: "TASK_LOG_STORAGE_UNAVAILABLE",
        error: "日志归档存储不可用，请确认 ClawWeb 数据库已完成日志归档表升级",
      });
      return;
    }
    if (active) { res.status(202).json({ archive: taskLogArchiveView(active), reused: true }); return; }
    const actor = resolveRequestUserId(req)!;
    const archiveId = id("LOG");
    let archive;
    try {
      archive = await repo.createTaskLogArchive({ archiveId, taskId: task.task_id, requestedBy: actor });
    } catch (error) {
      let concurrent = null;
      try {
        concurrent = await repo.findActiveTaskLogArchive(task.task_id);
      } catch (lookupError) {
        console.error("[evolve-task-log] active archive lookup failed", {
          taskId: task.task_id,
          error: lookupError instanceof Error ? lookupError.message : String(lookupError),
        });
      }
      if (concurrent) { res.status(202).json({ archive: taskLogArchiveView(concurrent), reused: true }); return; }
      console.error("[evolve-task-log] archive storage unavailable", {
        taskId: task.task_id,
        error: error instanceof Error ? error.message : String(error),
      });
      res.status(503).json({
        code: "TASK_LOG_STORAGE_UNAVAILABLE",
        error: "日志归档存储不可用，请确认 ClawWeb 数据库已完成日志归档表升级",
      });
      return;
    }
    try {
      const runtime = await repo.resolveEvolveBotRuntime(task.user_id, task.bot_id, taskBotEnv(task));
      if (!runtime) throw new Error("未找到目标 Bot 的运行环境");
      const baseUrl = getClawWebPublicBaseUrl().replace(/\/$/, "");
      const result = await dispatchTaskLogArchive({
        taskId: task.task_id, archiveId, userId: task.user_id, botId: task.bot_id, runtime,
        clawwebUrl: baseUrl,
        callbackUrl: `${baseUrl}/api/evolve/internal/tasks/${encodeURIComponent(task.task_id)}/log-archives/${encodeURIComponent(archiveId)}/bot-callback`,
      });
      await repo.markTaskLogArchiveDispatched({
        taskId: task.task_id, archiveId,
        transport: result.platformResponse.evolve_dispatch?.transport ?? "unknown",
        runId: result.runId, sessionId: result.sessionId, platformResponse: result.platformResponse,
      });
    } catch (error) {
      await repo.reportTaskLogArchive({
        taskId: task.task_id, archiveId, status: "failed", errorCode: "TASK_LOG_DISPATCH_FAILED",
        errorMessage: error instanceof Error ? error.message : String(error),
      });
    }
    const current = await repo.findTaskLogArchive(task.task_id, archiveId);
    res.status(202).json({ archive: taskLogArchiveView(current ?? archive), reused: false });
  }));

  router.get("/tasks/:taskId/log-archives/:archiveId/download-url", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const task = await repo.findTask(String(req.params.taskId));
    if (!task) { res.status(404).json({ error: "任务不存在" }); return; }
    if (!canManageTaskLogs(req, task)) { res.status(403).json({ error: "只有任务 Owner 或管理员可下载日志" }); return; }
    const archive = await repo.findTaskLogArchive(task.task_id, String(req.params.archiveId));
    if (!archive || archive.status !== "succeeded" || !archive.artifact_ref) {
      res.status(404).json({ error: "日志归档尚不可下载" }); return;
    }
    const location = taskLogArchiveLocation(task.task_id, archive.archive_id);
    if (archive.artifact_ref !== location.ref) { res.status(409).json({ error: "日志归档引用与登记位置不一致" }); return; }
    const filename = `${task.task_id}-${archive.archive_id}-logs.tar.gz`;
    const url = await artifactUrlStore.createSignedUrl(
      location.objectKey, "GET", EVOLVE_ARTIFACT_URL_TTL_SECONDS, {},
      { "response-content-disposition": `attachment; filename="${filename}"` },
    );
    res.json({ url, filename, expiresInSeconds: EVOLVE_ARTIFACT_URL_TTL_SECONDS });
  }));

  router.patch("/tasks/:taskId/share", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const task = await repo.findTask(String(req.params.taskId));
    if (!task) { res.status(404).json({ error: "任务不存在" }); return; }
    if (!canManageTask(req, task)) { res.status(403).json({ error: "只有任务 Owner 或管理员可修改分享设置" }); return; }
    if (typeof req.body?.shared !== "boolean") { res.status(400).json({ error: "shared 必须为布尔值" }); return; }
    const config = (parseJson(task.config_json) as Record<string, unknown> | null) ?? {};
    await repo.updateTaskConfig(task.task_id, { ...config, shared: req.body.shared });
    res.json({ taskId: task.task_id, shared: req.body.shared });
  }));

  router.post("/tasks/:taskId/steps/:stepId/retry", asyncHandler(async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const taskId = String(req.params.taskId);
    const stepId = String(req.params.stepId);
    const task = await repo.findTask(taskId);
    const failedStep = await repo.findStep(stepId);
    if (!task || !failedStep || failedStep.task_id !== taskId) {
      res.status(404).json({ error: "任务或 Step 不存在" }); return;
    }
    if (!new Set(["failed", "canceled"]).has(failedStep.status)) {
      res.status(409).json({ error: `只有失败或已取消的 Step 可以继续执行: ${failedStep.status}` }); return;
    }
    const steps = await repo.listSteps(taskId);
    const pendingBusinessAfterInitFailure = failedStep.step_type === "skill_init"
      && steps.filter((item) => item.step_id !== stepId)
        .every((item) => (item.status === "created" && item.step_type !== "skill_init")
          || (TERMINAL_STATUSES.has(item.status) && item.step_type === "skill_init"));
    if (steps.at(-1)?.step_id !== stepId && !pendingBusinessAfterInitFailure) {
      res.status(409).json({ error: "只能继续执行任务的最后一个失败节点" }); return;
    }
    if (steps.some((item) => !TERMINAL_STATUSES.has(item.status)
      && !(pendingBusinessAfterInitFailure && item.status === "created" && item.step_type !== "skill_init"))) {
      res.status(409).json({ error: "任务中已有正在执行的 Step" }); return;
    }

    const config = (parseJson(task.config_json) as {
      dispatchMode?: "message" | "run"; nodeCommands?: NodeCommandYamls;
      input?: { type?: string };
      model?: string;
      diagnoseIntent?: string;
      maxSessions?: number;
      sessionSource?: { mode?: "local" | "service_export" };
      startDate?: string; endDate?: string;
      trainBenchDomainId?: string; testBenchDomainId?: string;
      ownerUserId?: string;
      forceMessage?: boolean;
      runtimeMaintenance?: boolean;
      clawwebUrl?: string;
      botEnv?: string;
    } | null) ?? {};
    const runtime = await repo.resolveEvolveBotRuntime(task.user_id, task.bot_id, String(config.botEnv ?? ""));
    const dispatchMode = config.dispatchMode
      ?? await repo.resolveBotDispatchMode(task.user_id, task.bot_id, String(config.botEnv ?? ""));
    const forceMessage = config.forceMessage === true;
    if (failedStep.step_type === "skill_init") {
      const pendingBusiness = steps.find((item) => item.status === "created" && item.step_type !== "skill_init");
      if (!pendingBusiness) {
        res.status(409).json({ error: "Skill 初始化失败后没有待执行的业务节点" }); return;
      }
      await repo.prepareTaskRetry(taskId, config);
      const result = await startInitialEvolveStep({
        repo, dispatch, task, businessStep: pendingBusiness, runtime,
        clawwebUrl: config.clawwebUrl ?? getClawWebPublicBaseUrl(),
        callbackUrl: (createdStepId) => botCallbackUrl(req, taskId, createdStepId),
        initStepNo: Math.max(...steps.map((item) => item.step_no)) + 1,
        businessDispatch: {
          taskId, stepId: pendingBusiness.step_id, stepType: pendingBusiness.step_type,
          userId: task.user_id, botId: task.bot_id, command: pendingBusiness.command,
          mode: dispatchMode, callbackUrl: botCallbackUrl(req, taskId, pendingBusiness.step_id),
          runtime, forceMessage, runtimeMaintenance: config.runtimeMaintenance !== false,
          ...(pendingBusiness.step_type === "optimize" && pendingBusiness.round_no != null ? {
            optimizeArgs: {
              round: pendingBusiness.round_no,
              trainBenchDomainId: config.trainBenchDomainId,
              testBenchDomainId: config.testBenchDomainId,
            },
          } : {}),
        },
      });
      const dispatchedBusiness = result.businessStep ?? await repo.findStep(pendingBusiness.step_id);
      if (!dispatchedBusiness) { res.status(409).json({ error: "待执行的业务节点不存在" }); return; }
      res.status(201).json({ step: stepView(dispatchedBusiness) });
      return;
    }
    const newStepId = id("STEP");
    let publicCommand: string;
    let dispatchCommand: string;
    let diagnoseJudgeBackend: "subagent" | "api" | undefined;
    let diagnoseApiKey = "";
    if (failedStep.step_type === "diagnose") {
      const serviceRuntimeSelected = runtime?.botType?.toLowerCase() === "service";
      if (config.sessionSource?.mode === "service_export"
        && !serviceRuntimeSelected && !runtime?.hasServiceBot) {
        res.status(422).json({
          code: "SERVICE_SESSION_SOURCE_UNAVAILABLE",
          error: "所选草稿 Bot 没有可导出的服务态 Session",
        });
        return;
      }
      diagnoseJudgeBackend = readDiagnoseJudgeBackend(failedStep.command);
      diagnoseApiKey = String(req.body?.apiKey ?? "").trim();
      if (diagnoseJudgeBackend === "api"
        && runtime?.provider?.toLowerCase() === "arca") {
        res.status(422).json({
          code: "ARCA_API_JUDGE_UNSUPPORTED",
          error: "ARCA 模式只支持 Agent Judge，不支持传入 API Key",
        });
        return;
      }
      if (diagnoseJudgeBackend === "api" && !diagnoseApiKey) {
        res.status(400).json({ error: "重新执行 API Judge Diagnose 必须提供 apiKey" }); return;
      }
      const template = config.nodeCommands?.diagnose;
      if (template) {
        const systemArgs: Array<[string, string | number]> = [
          ["judge-backend", diagnoseJudgeBackend],
          ["max-sessions", config.maxSessions ?? 10],
          ["task-id", taskId], ["step-id", newStepId],
          ["clawweb-url", config.clawwebUrl ?? getClawWebPublicBaseUrl()],
        ];
        if (config.sessionSource?.mode === "service_export") {
          systemArgs.push(
            ["source", "service_export"],
            ["source-user-id", task.user_id],
            ["source-bot-id", task.bot_id],
            ["source-download-network", "office"],
          );
        }
        const commonValues = {
          model: config.model ?? "GLM-5.1",
          diagnose_intent: config.diagnoseIntent
            ? quoteCommandArgument(normalizeDiagnoseIntent(config.diagnoseIntent))
            : "",
          start_date: config.startDate ?? "",
          end_date: config.endDate ?? "",
        };
        publicCommand = renderCommand(template, { ...commonValues, api_key: "******" }, systemArgs);
        dispatchCommand = renderCommand(template, {
          ...commonValues,
          api_key: diagnoseJudgeBackend === "api" ? diagnoseApiKey : "******",
        }, systemArgs);
        if (diagnoseJudgeBackend === "subagent") {
          publicCommand = withoutDiagnoseApiKey(publicCommand);
          dispatchCommand = withoutDiagnoseApiKey(dispatchCommand);
        }
      } else {
        publicCommand = failedStep.command.replace(/--step[_-]id(?:\s+|=)[^\s]+/i, `--step-id ${newStepId}`);
        dispatchCommand = diagnoseJudgeBackend === "api"
          ? publicCommand.replace(/--api-key(?:\s+|=)\*+/i, `--api-key ${diagnoseApiKey}`)
          : publicCommand;
        if (publicCommand === failedStep.command
          || (diagnoseJudgeBackend === "api" && dispatchCommand === publicCommand)) {
          res.status(409).json({ error: "原 Diagnose 命令缺少可替换参数，无法安全重试" }); return;
        }
      }
    } else {
      const replaced = failedStep.command.replace(
        /--step[_-]id(?:\s+|=)[^\s]+/i,
        `--step-id ${newStepId}`,
      );
      if (replaced === failedStep.command) {
        res.status(409).json({ error: "原节点命令缺少 step_id，无法生成重试命令" }); return;
      }
      publicCommand = replaced;
      dispatchCommand = replaced;
      if (failedStep.step_type === "optimize" && !/--owner[_-]id(?:\s|=)/i.test(dispatchCommand)) {
        const ownerUserId = config.ownerUserId ?? task.user_id;
        const ownerArg = ` --owner-id ${safeBenchCommandValue("ownerId", ownerUserId)}`;
        publicCommand += ownerArg;
        dispatchCommand += ownerArg;
      }
    }

    if (!/--clawweb[_-]url(?:\s|=)/i.test(dispatchCommand)) {
      const clawwebArg = ` --clawweb-url ${config.clawwebUrl ?? getClawWebPublicBaseUrl()}`;
      publicCommand += clawwebArg;
      dispatchCommand += clawwebArg;
    }

    const retryStep = await repo.createStep({
      stepId: newStepId,
      taskId,
      stepType: failedStep.step_type,
      stepNo: Math.max(...steps.map((item) => item.step_no)) + 1,
      roundNo: failedStep.round_no,
      command: publicCommand,
    });
    await repo.prepareTaskRetry(taskId, config);
    const transport = resolveEvolveTransport({
      stepType: retryStep.step_type,
      runtime,
      forceMessage,
    });
    const selectedCommand = retryStep.step_type === "diagnose" && transport === "baas_execute_command"
      && diagnoseJudgeBackend === "api"
      ? withoutDiagnoseApiKey(publicCommand)
      : dispatchCommand;
    await startInitialEvolveStep({
      repo, dispatch, task, businessStep: retryStep, runtime,
      clawwebUrl: config.clawwebUrl ?? getClawWebPublicBaseUrl(),
      callbackUrl: (createdStepId) => botCallbackUrl(req, taskId, createdStepId),
      initStepNo: Math.max(...steps.map((item) => item.step_no)) + 2,
      businessDispatch: {
        taskId, stepId: newStepId, stepType: retryStep.step_type,
        userId: task.user_id, botId: task.bot_id, command: selectedCommand, mode: dispatchMode,
        callbackUrl: botCallbackUrl(req, taskId, newStepId),
        runtime,
        forceMessage,
        ...(retryStep.step_type === "diagnose" && transport === "baas_execute_command"
          && diagnoseJudgeBackend === "api" ? {
          secrets: { diagnoseApiKey },
        } : {}),
        ...(retryStep.step_type === "optimize" && retryStep.round_no != null ? {
          optimizeArgs: {
            round: retryStep.round_no,
            trainBenchDomainId: config.trainBenchDomainId,
            testBenchDomainId: config.testBenchDomainId,
          },
        } : {}),
      },
    });
    const refreshed = await repo.findStep(newStepId);
    res.status(201).json({ step: refreshed ? stepView(refreshed) : stepView(retryStep) });
  }));

  router.post("/tasks/:taskId/steps/:stepId/cancel", asyncHandler(async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const taskId = String(req.params.taskId);
    const stepId = String(req.params.stepId);
    const task = await repo.findTask(taskId);
    const step = await repo.findStep(stepId);
    if (!task || !step || step.task_id !== taskId) {
      res.status(404).json({ error: "任务或 Step 不存在" }); return;
    }
    if (!new Set(["created", "dispatched", "running"]).has(step.status)) {
      res.status(409).json({ error: `当前 Step 状态不允许停止: ${step.status}` }); return;
    }
    const steps = await repo.listSteps(taskId);
    const waitingForInitializer = step.status === "created" && step.step_type !== "skill_init"
      && steps.some((item) => item.step_type === "skill_init" && !TERMINAL_STATUSES.has(item.status));
    if (waitingForInitializer) {
      res.status(409).json({ error: "业务节点正在等待 Skill 初始化，不能单独停止" }); return;
    }
    const cancelableInitializer = step.step_type === "skill_init"
      && steps.filter((item) => item.step_id !== stepId)
        .every((item) => item.status === "created" && item.step_type !== "skill_init");
    if (steps.at(-1)?.step_id !== stepId && !cancelableInitializer) {
      res.status(409).json({ error: "只能停止任务的当前运行节点" }); return;
    }
    const reason = String(req.body?.reason ?? "用户手动停止").trim().slice(0, 500) || "用户手动停止";
    await repo.updateStepStatus(stepId, {
      status: "canceled",
      errorCode: "USER_CANCELED",
      errorMessage: reason,
      retryable: true,
    });
    let cancellation: {
      status: "not_required" | "remote_stopped" | "remote_stop_failed";
      transport?: string;
      error?: string;
      auditError?: string;
    } = { status: "not_required" };
    if (step.status !== "created") {
      const runtime = await repo.resolveEvolveBotRuntime(task.user_id, task.bot_id, taskBotEnv(task));
      let remoteCancellation: {
        status: "remote_stopped" | "remote_stop_failed";
        transport?: string;
        error?: string;
      };
      try {
        const result = await cancelExecution({
          taskId, stepId, stepType: step.step_type, userId: task.user_id, botId: task.bot_id,
          sessionId: step.bot_session_id,
          platformResponse: parseJson(step.bot_response_json), runtime,
        });
        remoteCancellation = { status: "remote_stopped", transport: result.transport };
      } catch (error) {
        remoteCancellation = {
          status: "remote_stop_failed",
          error: (error instanceof Error ? error.message : String(error)).slice(0, 500),
        };
      }
      cancellation = remoteCancellation;
      try {
        await repo.recordCancellationAttempt(stepId, remoteCancellation);
      } catch (error) {
        cancellation.auditError = (error instanceof Error ? error.message : String(error)).slice(0, 500);
        console.error(`[evolve] failed to persist cancellation audit for ${taskId}/${stepId}: ${cancellation.auditError}`);
      }
    }
    const canceled = await repo.findStep(stepId);
    res.json({ step: canceled ? stepView(canceled) : stepView(step), cancellation });
  }));

  router.use("/internal/tasks/:taskId", asyncHandler(async (req, res, next) => {
    if (!repo) { next(); return; }
    const task = await repo.findTask(String(req.params.taskId));
    if (task?.task_type === "repair") {
      res.status(404).json({ error: "任务不存在" }); return;
    }
    next();
  }));

  router.post("/internal/tasks/:taskId/log-archives/:archiveId/bot-callback", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const archive = await repo.findTaskLogArchive(String(req.params.taskId), String(req.params.archiveId));
    if (!archive) { res.status(404).json({ error: "日志归档不存在" }); return; }
    // Transport callbacks are audit-only. Collector reports are the archive state authority.
    res.json({ ok: true });
  }));

  router.post("/internal/tasks/:taskId/log-archives/:archiveId/upload-url", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const taskId = String(req.params.taskId);
    const archive = await repo.findTaskLogArchive(taskId, String(req.params.archiveId));
    if (!archive) { res.status(404).json({ error: "日志归档不存在" }); return; }
    if (!['dispatching', 'running'].includes(archive.status)) { res.status(409).json({ error: "终态日志归档不再签发上传 URL" }); return; }
    const { size, sha256, contentType } = req.body ?? {};
    if (!Number.isSafeInteger(size) || Number(size) < 0 || Number(size) > 2 * 1024 * 1024 * 1024
      || !/^[0-9a-f]{64}$/.test(String(sha256 ?? "")) || contentType !== "application/gzip") {
      res.status(400).json({ error: "日志归档元信息不合法" }); return;
    }
    const location = taskLogArchiveLocation(taskId, archive.archive_id);
    const headers: Record<string, string> = { "Content-Type": location.contentType };
    const url = await artifactUrlStore.createSignedUrl(location.objectKey, "PUT", EVOLVE_ARTIFACT_URL_TTL_SECONDS, headers);
    res.json({ method: "PUT", url, headers, expiresInSeconds: EVOLVE_ARTIFACT_URL_TTL_SECONDS,
      artifact: { kind: location.artifactKind, ref: location.ref, size, sha256, contentType: location.contentType } });
  }));

  router.post("/internal/tasks/:taskId/log-archives/:archiveId/report", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const taskId = String(req.params.taskId);
    const archiveId = String(req.params.archiveId);
    const archive = await repo.findTaskLogArchive(taskId, archiveId);
    if (!archive) { res.status(404).json({ error: "日志归档不存在" }); return; }
    const status = String(req.body?.status ?? "");
    if (!new Set(["running", "succeeded", "failed"]).has(status)) { res.status(400).json({ error: "status 不合法" }); return; }
    let artifact: { ref: string; size: number; sha256: string; contentType: string } | null = null;
    if (status === "succeeded") {
      const raw = req.body?.artifact;
      const location = taskLogArchiveLocation(taskId, archiveId);
      if (!isRecord(raw) || raw.ref !== location.ref || !Number.isSafeInteger(raw.size)
        || !/^[0-9a-f]{64}$/.test(String(raw.sha256 ?? "")) || raw.contentType !== location.contentType) {
        res.status(422).json({ error: "日志归档 Artifact 不合法" }); return;
      }
      artifact = { ref: String(raw.ref), size: Number(raw.size), sha256: String(raw.sha256), contentType: String(raw.contentType) };
    }
    await repo.reportTaskLogArchive({
      taskId, archiveId, status: status as 'running' | 'succeeded' | 'failed', artifact,
      metadata: req.body?.metadata,
      errorCode: isRecord(req.body?.error) ? String(req.body.error.code ?? "") : null,
      errorMessage: isRecord(req.body?.error) ? String(req.body.error.message ?? "").slice(0, 4000) : null,
    });
    res.json({ ok: true });
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/bot-callback", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const stepId = String(req.params.stepId);
    const step = await repo.findStep(stepId);
    if (!step) { res.status(404).json({ error: "step 不存在" }); return; }
    if (req.params.taskId && step.task_id !== String(req.params.taskId)) {
      res.status(404).json({ error: "step 不属于指定 task" }); return;
    }
    const { run_id: runId, bot_id: callbackBotId, status: rawStatus, result, error, metadata } = req.body ?? {};
    if (!runId || !rawStatus) { res.status(400).json({ error: "run_id、status 为必填项" }); return; }
    const normalized = String(rawStatus).toUpperCase();
    const botResponse = parseJson(step.bot_response_json) as {
      evolve_dispatch?: { provider?: string; transport?: string; runner_mode?: string };
    } | null;
    const directArcaRunner = botResponse?.evolve_dispatch?.provider?.toLowerCase() === "arca"
      && botResponse.evolve_dispatch.transport === "message"
      && botResponse.evolve_dispatch.runner_mode === "direct";
    const runnerStart = directArcaRunner
      ? parseArcaRunnerCallback(result, metadata, { taskId: step.task_id, stepId })
      : null;
    const initHasStarted = step.step_type === "skill_init" && step.status !== "dispatched";
    if ((normalized === "FAILED" || normalized === "ERROR") && !initHasStarted && !directArcaRunner) {
      if (!TERMINAL_STATUSES.has(step.status)) {
        const errorMessage = typeof error === "string" ? error : JSON.stringify(error ?? "Bot run failed");
        await repo.updateStepStatus(stepId, {
          status: "failed", errorCode: "BOT_RUN_FAILED", errorMessage, retryable: true,
        });
      }
    }
    if (directArcaRunner && (normalized === "FAILED" || normalized === "ERROR")) {
      console.warn("[clawweb][evolve][arca-runner] Bot Callback failed after Message acceptance; Handler report remains authoritative", {
        taskId: step.task_id,
        stepId,
        runId,
        callbackStatus: normalized,
        callbackError: typeof error === "string" ? error.slice(0, 500) : error ?? null,
      });
    }
    if (directArcaRunner && normalized === "COMPLETED" && !runnerStart) {
      console.warn("[clawweb][evolve][arca-runner] Bot Callback completed without a valid Runner start result; Handler report remains authoritative", {
        taskId: step.task_id,
        stepId,
        runId,
      });
    }
    res.json({
      ok: true,
      stepId,
      transportStatus: normalized,
      message: "Bot Callback 只记录传输结果；业务状态和 Output 由 Step /report 上报",
      runnerStart: runnerStart ? {
        status: runnerStart.status,
        taskId: runnerStart.task_id,
        stepId: runnerStart.step_id,
        pid: Number(runnerStart.pid),
      } : null,
      bot: { runId, botId: callbackBotId ?? null, result: result ?? null, metadata: metadata ?? null },
    });
  }));

  router.get("/internal/tasks/:taskId/steps/:stepId/input", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const step = await repo.findStep(String(req.params.stepId));
    if (!step) { res.status(404).json({ error: "step 不存在" }); return; }
    if (req.params.taskId && step.task_id !== String(req.params.taskId)) {
      res.status(404).json({ error: "step 不属于指定 task" }); return;
    }
    const task = await repo.findTask(step.task_id);
    if (!task) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
    if (step.step_type === "plan" && isInsightImprovementTask(task)) {
      if (!taskSourceService) {
        res.status(503).json({ code: "PLAN_SOURCE_INPUT_UNAVAILABLE", error: "Task Source 服务不可用" }); return;
      }
      const source = await taskSourceService.findView(task.task_id);
      if (!source) {
        res.status(409).json({ code: "PLAN_SOURCE_INPUT_UNAVAILABLE", error: "Task Source 不存在" }); return;
      }
      try {
        const planSource = await taskSourceService.resolvePlanSource(task.task_id);
        res.json({
          protocolVersion: "1.2",
          task: {
            taskId: task.task_id,
            taskType: task.task_type,
            config: parseJson(task.config_json),
          },
          target: { userId: task.user_id, botId: task.bot_id },
          step: {
            stepId: step.step_id,
            stepType: step.step_type,
            stepNo: step.step_no,
            roundNo: step.round_no,
          },
          inputs: { planSource },
          report: { url: `/api/evolve/internal/tasks/${task.task_id}/steps/${step.step_id}/report` },
        });
      } catch (error) {
        const failure: InsightBoundaryError = isInsightBoundaryError(error)
          ? error
          : Object.assign(
            new Error(error instanceof Error ? error.message : String(error)),
            { code: "PLAN_SOURCE_INPUT_UNAVAILABLE", stage: "interface", retryable: true },
          );
        res.status(failure.retryable ? 503 : 422).json({
          code: failure.code,
          error: failure.message,
          stage: failure.stage,
          retryable: failure.retryable,
        });
      }
      return;
    }
    let previous: EvolveStepRow[];
    try {
      previous = await repo.resolveInputSteps(step);
    } catch (error) {
      res.status(409).json({ error: error instanceof Error ? error.message : String(error) });
      return;
    }
    const taskConfig = (parseJson(task.config_json) as Record<string, unknown> | null) ?? {};
    const planDiagnose = previous.filter((item) => item.step_type === "diagnose" && item.status === "succeeded").at(-1);
    const sourceTaskIds = step.step_type === "optimize"
      ? ((taskConfig.sourceDiagnosisTaskIds as string[] | undefined) ?? [task.task_id])
      : [];
    const diagnosisInputs = step.step_type === "optimize"
      ? await Promise.all(sourceTaskIds.map(async (sourceTaskId, index) => {
        const sourceSteps = await repo.listSteps(sourceTaskId);
        const diagnose = sourceSteps.filter((item) => item.step_type === "diagnose" && item.status === "succeeded").at(-1);
        const plan = sourceSteps.filter((item) => item.step_type === "plan" && item.status === "succeeded").at(-1);
        return {
          taskId: sourceTaskId,
          role: index === 0 ? "primary" : "additional",
          diagnose: diagnose ? { stepId: diagnose.step_id, output: parseJson(diagnose.output_json) } : null,
          plan: plan ? { stepId: plan.step_id, output: parseJson(plan.output_json) } : null,
        };
      }))
      : undefined;
    res.json({
      protocolVersion: "1.0",
      task: {
        taskId: task.task_id, taskType: task.task_type,
        config: parseJson(task.config_json),
      },
      target: { userId: task.user_id, botId: task.bot_id },
      step: {
        stepId: step.step_id, stepType: step.step_type,
        stepNo: step.step_no, roundNo: step.round_no,
      },
      inputs: step.step_type === "plan"
        ? {
          diagnose: planDiagnose
            ? { stepId: planDiagnose.step_id, output: parseJson(planDiagnose.output_json) }
            : null,
        }
        : step.step_type === "optimize"
          ? {
            diagnoses: diagnosisInputs,
            previousRounds: previous
              .filter((item) => item.step_type === "optimize" && item.status === "succeeded")
              .map((item) => ({ stepId: item.step_id, roundNo: item.round_no, output: parseJson(item.output_json) })),
          }
          : {},
      report: { url: `/api/evolve/internal/tasks/${task.task_id}/steps/${step.step_id}/report` },
    });
  }));

  router.get("/internal/tasks/:taskId/steps/:stepId/output", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const step = await repo.findStep(String(req.params.stepId));
    if (!step) { res.status(404).json({ error: "step 不存在" }); return; }
    if (req.params.taskId && step.task_id !== String(req.params.taskId)) {
      res.status(404).json({ error: "step 不属于指定 task" }); return;
    }
    if (step.status !== "succeeded") {
      res.status(409).json({ error: `step 尚未成功: ${step.status}` }); return;
    }
    res.json({
      step: stepView(step),
      output: parseJson(step.output_json),
    });
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/artifacts/upload-url", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const taskId = String(req.params.taskId);
    const step = await repo.findStep(String(req.params.stepId));
    const task = step ? await repo.findTask(taskId) : null;
    if (!step || !task || step.task_id !== taskId) { res.status(404).json({ error: "Step 不属于指定 Task" }); return; }
    if (TERMINAL_STATUSES.has(step.status)) { res.status(409).json({ error: "终态 Step 不再签发上传 URL" }); return; }
    const { kind, round, size, sha256, contentType } = req.body ?? {};
    if (!Number.isSafeInteger(size) || Number(size) < 0 || !/^[0-9a-f]{64}$/.test(String(sha256 ?? ""))) {
      res.status(400).json({ error: "Artifact size 或 sha256 不合法" }); return;
    }
    let location;
    try {
      location = uploadArtifactLocation(taskId, kind, round);
      const isSnapshot = String(kind).startsWith("snapshot-");
      const isBaseline = String(kind).startsWith("baseline-");
      const isRound = String(kind).startsWith("round-");
      if ((isSnapshot && step.step_type !== "pack")
        || ((isBaseline || isRound) && step.step_type !== "optimize")) {
        throw new Error("当前 Step 不允许上传该类型 Artifact");
      }
      if (isBaseline && Number(step.round_no || 0) !== 1) throw new Error("Baseline Artifact 只能由第 1 轮 Optimize Step 上传");
      if (isRound && Number(round) !== Number(step.round_no || 0)) throw new Error("Artifact round 与当前 Step 不一致");
      if (String(contentType) !== location.contentType) throw new Error("Artifact Content-Type 不合法");
    } catch (error) {
      res.status(422).json({ error: error instanceof Error ? error.message : String(error) }); return;
    }
    const headers: Record<string, string> = {};
    const url = await artifactUrlStore.createSignedUrl(
      location.objectKey, "PUT", EVOLVE_ARTIFACT_URL_TTL_SECONDS, headers,
    );
    res.json({
      schemaVersion: "clawevolve.artifact-url.v1",
      method: "PUT", url, headers,
      expiresInSeconds: EVOLVE_ARTIFACT_URL_TTL_SECONDS,
      expiresAt: new Date(Date.now() + EVOLVE_ARTIFACT_URL_TTL_SECONDS * 1000).toISOString(),
      artifact: {
        kind: location.artifactKind, ref: location.ref,
        size: Number(size), sha256: String(sha256), contentType: location.contentType,
      },
    });
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/artifacts/restore-download-url", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const taskId = String(req.params.taskId);
    const step = await repo.findStep(String(req.params.stepId));
    const task = step ? await repo.findTask(taskId) : null;
    if (!step || !task || step.task_id !== taskId || step.step_type !== "restore") {
      res.status(404).json({ error: "Restore Step 不存在" }); return;
    }
    if (TERMINAL_STATUSES.has(step.status)) { res.status(409).json({ error: "终态 Restore Step 不再签发下载 URL" }); return; }
    const config = parseJson(task.config_json) as Record<string, unknown> | null;
    const frozenArtifact = isRecord(config?.artifact) ? config.artifact : null;
    if (!config || !frozenArtifact) { res.status(409).json({ error: "Restore Task 缺少冻结 Artifact" }); return; }
    const kind = String(req.body?.kind ?? "");
    let objectKey: string;
    try {
      validatePackArtifact(frozenArtifact);
      objectKey = kind === "manifest"
        ? restoreManifestLocation(String(config.sourceTaskId), config.sourceKind, config.sourceRound).objectKey
        : kind === "artifact"
          ? objectKeyFromFrozenPack(frozenArtifact.ref, String(config.sourceTaskId))
          : (() => { throw new Error("下载类型只能是 manifest 或 artifact"); })();
    } catch (error) {
      res.status(422).json({ error: error instanceof Error ? error.message : String(error) }); return;
    }
    const url = await artifactUrlStore.createSignedUrl(objectKey, "GET", EVOLVE_ARTIFACT_URL_TTL_SECONDS);
    res.json({
      schemaVersion: "clawevolve.artifact-url.v1",
      method: "GET", url, headers: {},
      expiresInSeconds: EVOLVE_ARTIFACT_URL_TTL_SECONDS,
      expiresAt: new Date(Date.now() + EVOLVE_ARTIFACT_URL_TTL_SECONDS * 1000).toISOString(),
      source: {
        sourceTaskId: config.sourceTaskId, sourceStepId: config.sourceStepId,
        sourceKind: config.sourceKind, sourceRound: config.sourceRound,
      },
      ...(kind === "artifact" ? { artifact: frozenArtifact } : {}),
    });
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/artifacts/accepted-download-url", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const taskId = String(req.params.taskId);
    const step = await repo.findStep(String(req.params.stepId));
    if (!step || step.task_id !== taskId || step.step_type !== "optimize" || TERMINAL_STATUSES.has(step.status)) {
      res.status(404).json({ error: "运行中的 Optimize Step 不存在" }); return;
    }
    const sourceRound = Number(req.body?.sourceRound);
    if (!Number.isSafeInteger(sourceRound) || sourceRound < 1 || sourceRound >= Number(step.round_no || 0)) {
      res.status(422).json({ error: "sourceRound 必须早于当前 Optimize Round" }); return;
    }
    const sourceStep = (await repo.listSteps(taskId)).find((item) =>
      item.step_type === "optimize" && item.status === "succeeded" && Number(item.round_no) === sourceRound);
    const sourceOutput = sourceStep ? parseJson(sourceStep.output_json) as Record<string, unknown> | null : null;
    const pack = isRecord(sourceOutput?.pack) ? sourceOutput.pack : null;
    if (!sourceStep || !pack || pack.status !== "available" || !isRecord(pack.artifact)) {
      res.status(404).json({ error: "来源 Round 没有已登记的 accepted Pack" }); return;
    }
    let artifact;
    try {
      artifact = parseEvolveArtifactRef(pack.artifact, { taskId, round: sourceRound, kind: "pack" }).artifact;
    } catch (error) { res.status(422).json({ error: error instanceof Error ? error.message : String(error) }); return; }
    const objectKey = objectKeyFromFrozenPack(artifact.ref, taskId);
    const url = await artifactUrlStore.createSignedUrl(objectKey, "GET", EVOLVE_ARTIFACT_URL_TTL_SECONDS);
    res.json({ schemaVersion: "clawevolve.artifact-url.v1", method: "GET", url, headers: {}, expiresInSeconds: EVOLVE_ARTIFACT_URL_TTL_SECONDS, artifact, sourceRound });
  }));

  router.get("/tasks/:taskId/steps/:stepId/diff", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const task = await repo.findTask(String(req.params.taskId));
    const step = await repo.findStep(String(req.params.stepId));
    if (!task || !step || step.task_id !== task.task_id || step.step_type !== "optimize") {
      res.status(404).json({ error: "Optimize Step 不存在" }); return;
    }
    const output = parseJson(step.output_json) as Record<string, unknown> | null;
    const diff = output?.diff as Record<string, unknown> | undefined;
    let parsed;
    try {
      parsed = parseEvolveArtifactRef(diff?.artifact, {
        taskId: task.task_id, round: Number(step.round_no || 0), kind: "diff",
      });
    } catch (error) {
      res.status(422).json({ error: error instanceof Error ? error.message : String(error) }); return;
    }
    const object = await artifactStore.getObject(parsed.objectKey);
    if (object.content.byteLength > 2 * 1024 * 1024) { res.status(413).json({ error: "Diff 超过 2 MiB 展示上限" }); return; }
    if (object.content.byteLength !== parsed.artifact.size
      || (await import("node:crypto")).createHash("sha256").update(object.content).digest("hex") !== parsed.artifact.sha256) {
      res.status(409).json({ error: "Diff 内容与登记摘要不一致" }); return;
    }
    res.setHeader("Content-Type", "text/x-diff; charset=utf-8");
    res.send(object.content);
  }));

  router.get("/tasks/:taskId/steps/:stepId/pack-download-url", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const taskId = String(req.params.taskId);
    const sourceKind = String(req.query.sourceKind ?? "");
    const task = await repo.findTask(taskId);
    const step = await repo.findStep(String(req.params.stepId));
    if (!task || !step || step.task_id !== taskId || step.status !== "succeeded") {
      res.status(404).json({ error: "Pack 不存在" }); return;
    }
    if (!new Set(["baseline", "snapshot", "round"]).has(sourceKind)) {
      res.status(400).json({ error: "sourceKind 不合法" }); return;
    }
    const sourceRound = sourceKind === "round" ? Number(step.round_no || 0) : 0;
    const registeredPack = (await repo.listPacks(task.user_id, task.bot_id)).find((pack) =>
      pack.source_task_id === taskId && pack.source_step_id === step.step_id
        && pack.source_kind === sourceKind && pack.source_round === sourceRound
        && pack.status === "available");
    if (!registeredPack) { res.status(404).json({ error: "Pack 未登记或不可用" }); return; }
    let artifact;
    try {
      artifact = validatePackArtifact({
        kind: sourceKind === "baseline" ? "baseline_pack" : "pack",
        ref: registeredPack.artifact_ref, size: Number(registeredPack.artifact_size),
        sha256: registeredPack.artifact_sha256, contentType: registeredPack.artifact_content_type,
      });
      const round = Number(step.round_no || 0);
      const expectedSuffix = sourceKind === "baseline"
        ? "/baseline/artifact_v0.zip"
        : sourceKind === "snapshot"
          ? "/snapshots/artifact.zip"
          : `/rounds/round-${String(round).padStart(3, "0")}/artifacts/artifact_v${round}.zip`;
      if (artifact.ref !== `oss://${getArtifactBucket()}/evolution/${taskId}${expectedSuffix}`) {
        throw new Error("Pack 引用与来源不一致");
      }
    } catch (error) {
      res.status(422).json({ error: error instanceof Error ? error.message : String(error) }); return;
    }
    const objectKey = objectKeyFromFrozenPack(artifact.ref, taskId);
    const filename = sourceKind === "baseline" ? `${taskId}-${step.step_id}-baseline.zip`
      : sourceKind === "snapshot" ? `${taskId}-${step.step_id}-snapshot.zip`
        : `${taskId}-${step.step_id}-round-${step.round_no}.zip`;
    const url = await artifactUrlStore.createSignedUrl(
      objectKey,
      "GET",
      EVOLVE_ARTIFACT_URL_TTL_SECONDS,
      {},
      { "response-content-disposition": `attachment; filename="${filename}"` },
    );
    res.json({
      schemaVersion: "clawevolve.pack-download.v1", url,
      filename,
      expiresInSeconds: EVOLVE_ARTIFACT_URL_TTL_SECONDS,
      artifact,
    });
  }));

  router.post("/internal/tasks/:taskId/steps/:stepId/report", asyncHandler(async (req, res) => {
    if (!repo) { res.status(503).json({ error: "数据库不可用" }); return; }
    const stepId = String(req.params.stepId);
    const step = await repo.findStep(stepId);
    if (!step) { res.status(404).json({ error: "step 不存在" }); return; }
    if (req.params.taskId && step.task_id !== String(req.params.taskId)) {
      res.status(404).json({ error: "step 不属于指定 task" }); return;
    }
    if (step.step_type === "run_analysis" || step.step_type === "suggestion_apply") {
      res.status(410).json({
        error: "task_guard_managed_callback_required",
        message: "Task Guard steps must use the signed managed callback",
      });
      return;
    }
    const userId = resolveRequestUserId(req);
    const { status, summary, error, output: reportedOutput } = req.body ?? {};
    let output = reportedOutput;
    if (!status) { res.status(400).json({ error: "status 为必填项" }); return; }
    if (!ALLOWED_STATUSES.has(String(status))) { res.status(400).json({ error: `不支持的状态: ${status}` }); return; }
    if (step.status === "canceled" && step.step_type === "optimize") {
      res.json({
        ok: true, duplicate: true, ignored: true, revised: false,
        stepId, status: step.status, reportedStatus: String(status), nextStep: null,
      });
      return;
    }
    if (status === "running" && step.step_type === "optimize" && isRecord(output?.baselineArtifact)) {
      try {
        const baseline = output.baselineArtifact;
        if (baseline.status !== "available" || !isRecord(baseline.artifact)) {
          throw new Error("Optimize 初始 Pack 必须为 available");
        }
        const artifact = validatePackArtifact(baseline.artifact);
        if (artifact.kind !== "baseline_pack"
          || artifact.ref !== `oss://${getArtifactBucket()}/evolution/${step.task_id}/baseline/artifact_v0.zip`) {
          throw new Error("Optimize 初始 Pack 路径与当前 Task 不一致");
        }
        const packTask = await repo.findTask(step.task_id);
        if (!packTask) throw new Error("step 关联任务不存在");
        await registerStepPacks(repo, packTask, step, output);
      } catch (packError) {
        console.warn("[clawweb][evolve][optimize-report] initial Pack registration warning", {
          taskId: step.task_id,
          stepId: step.step_id,
          warning: packError instanceof Error ? packError.message : String(packError),
        });
      }
    }
    if (status === "succeeded" && step.step_type === "optimize"
      && !(TERMINAL_STATUSES.has(step.status) && output === undefined)) {
      const warnings: OptimizeReportWarning[] = [];
      try {
        warnings.push(...await collectOptimizeReportWarnings(repo, benchRunRepo, step, output));
      } catch (warningError) {
        addOptimizeWarning(
          warnings,
          "OPTIMIZE_WARNING_COLLECTION_FAILED",
          "ClawWeb 未能完成 Optimize 结果辅助检查；不影响本轮上报和调度",
          { error: warningError instanceof Error ? warningError.message : String(warningError) },
        );
      }
      output = {
        ...(isRecord(output) ? output : { reportedOutput: output }),
        clawwebWarnings: warnings,
      };
      for (const warning of warnings) {
        console.warn("[clawweb][evolve][optimize-report]", {
          taskId: step.task_id, stepId: step.step_id, roundNo: step.round_no, ...warning,
        });
      }
      const packTask = await repo.findTask(step.task_id);
      if (!packTask) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
      try {
        await registerStepPacks(repo, packTask, step, output);
      } catch (packError) {
        addOptimizeWarning(
          warnings,
          "OPTIMIZE_PACK_REGISTRATION_WARNING",
          "Optimize Pack 登记失败；不影响本轮上报和下一轮调度",
          { error: packError instanceof Error ? packError.message : String(packError) },
        );
        output = { ...(output as Record<string, unknown>), clawwebWarnings: warnings };
      }
    }
    if (status === "succeeded" && step.step_type === "pack") {
      const outputError = validateStepOutput(step.step_type, output);
      if (outputError) { res.status(422).json({ error: outputError }); return; }
      const pack = (output as Record<string, unknown>).pack as Record<string, unknown>;
      if (pack.status !== "available" || !isRecord(pack.artifact)) { res.status(422).json({ error: "Pack 必须为 available" }); return; }
      try {
        const artifact = validatePackArtifact(pack.artifact);
        if (artifact.ref !== `oss://${getArtifactBucket()}/evolution/${step.task_id}/snapshots/artifact.zip`) throw new Error("Pack 路径与当前 Task 不一致");
      } catch (packError) { res.status(422).json({ error: packError instanceof Error ? packError.message : String(packError) }); return; }
    }
    if (status === "succeeded" && step.step_type === "restore") {
      const outputError = validateStepOutput(step.step_type, output);
      if (outputError) { res.status(422).json({ error: outputError }); return; }
      const task = await repo.findTask(step.task_id); const config = task ? parseJson(task.config_json) as Record<string, unknown> | null : null;
      const restore = (output as Record<string, unknown>).restore as Record<string, unknown>;
      const reportedArtifact = isRecord(restore.artifact) ? restore.artifact : {};
      const frozenArtifact = isRecord(config?.artifact) ? config.artifact : {};
      const artifactMatches = ["ref", "size", "sha256", "contentType"].every((key) => reportedArtifact[key] === frozenArtifact[key]);
      const normalizedSourceRound = Number(config?.sourceRound ?? 0);
      if (!config || restore.status !== "succeeded" || restore.sourceTaskId !== config.sourceTaskId
        || restore.sourceKind !== config.sourceKind || Number(restore.sourceRound ?? 0) !== normalizedSourceRound
        || !artifactMatches) {
        res.status(422).json({ error: "Restore 结果与冻结来源不一致" }); return;
      }
    }
    if (step.status === "canceled") {
      res.json({
        ok: true, duplicate: true, ignored: true, revised: false,
        stepId, status: step.status, reportedStatus: String(status), nextStep: null,
      });
      return;
    }
    if (status === "succeeded" && step.step_type === "bench") {
      const outputError = validateStepOutput(step.step_type, output);
      if (outputError) { res.status(422).json({ error: outputError }); return; }
      if (!benchRunRepo) { res.status(503).json({ error: "Bench Run 数据库不可用" }); return; }
      const task = await repo.findTask(step.task_id);
      if (!task) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
      const config = (parseJson(task.config_json) as { bench?: { ownerUserId?: string; domainId?: string } } | null) ?? {};
      const benchRunId = String((output as Record<string, unknown>).benchRunId);
      const run = await benchRunRepo.findByBenchRunId(benchRunId);
      if (!run) { res.status(422).json({ error: "Bench Run 不存在" }); return; }
      const reportedDomainId = (output as Record<string, unknown>).domainId;
      const expectedTemplateOwnerId = String(config.bench?.ownerUserId ?? "").trim();
      const actualRunOwnerId = dbText(run.owner_user_id).trim();
      const expectedDomainId = String(config.bench?.domainId ?? "").trim();
      const actualRunDomainId = dbText(run.domain_id).trim();
      console.info("[clawweb][evolve][bench-report] validating Bench Run ownership", {
        taskId: task.task_id,
        stepId: step.step_id,
        benchRunId,
        taskUserId: task.user_id,
        expectedTemplateOwnerId,
        expectedTemplateOwnerType: typeof config.bench?.ownerUserId,
        runOwnerUserId: run.owner_user_id,
        runOwnerUserIdType: typeof run.owner_user_id,
        taskDomainId: config.bench?.domainId ?? null,
        runDomainId: run.domain_id,
        reportedDomainId: reportedDomainId == null ? null : String(reportedDomainId),
        runStatus: run.status,
      });
      if (!expectedTemplateOwnerId || actualRunOwnerId !== expectedTemplateOwnerId) {
        console.warn("[clawweb][evolve][bench-report] Bench Run owner mismatch", {
          taskId: task.task_id,
          stepId: step.step_id,
          benchRunId,
          expectedTemplateOwnerId,
          actualRunOwnerUserId: actualRunOwnerId,
          expectedTemplateOwnerType: typeof config.bench?.ownerUserId,
          actualRunOwnerType: typeof run.owner_user_id,
        });
        res.status(422).json({
          code: "BENCH_RUN_OWNER_MISMATCH",
          error: `Bench Run owner 校验失败: expected=${expectedTemplateOwnerId || "<missing>"}, actual=${actualRunOwnerId || "<missing>"}`,
          expectedOwnerId: expectedTemplateOwnerId,
          actualOwnerId: actualRunOwnerId,
          expectedType: typeof config.bench?.ownerUserId,
          actualType: typeof run.owner_user_id,
          benchRunId,
          taskId: task.task_id,
          stepId: step.step_id,
        }); return;
      }
      if (!expectedDomainId || actualRunDomainId !== expectedDomainId) {
        console.warn("[clawweb][evolve][bench-report] Bench Run domain mismatch", {
          taskId: task.task_id,
          stepId: step.step_id,
          benchRunId,
          expectedTaskDomainId: expectedDomainId,
          actualRunDomainId,
          reportedDomainId: reportedDomainId == null ? null : String(reportedDomainId),
        });
        res.status(422).json({
          code: "BENCH_RUN_DOMAIN_MISMATCH",
          error: `Bench Run Domain 不一致: expected=${expectedDomainId || "<missing>"}, actual=${actualRunDomainId || "<missing>"}`,
          expectedDomainId,
          actualRunDomainId,
          benchRunId,
          taskId: task.task_id,
          stepId: step.step_id,
        }); return;
      }
      if (run.status !== "succeeded") {
        res.status(422).json({ error: `Bench Run 尚未成功: ${run.status}` }); return;
      }
      const runConfig = (parseJson(run.run_config_json) as { evolveTaskId?: string; evolveStepId?: string } | null) ?? {};
      if (runConfig.evolveTaskId !== task.task_id || runConfig.evolveStepId !== step.step_id) {
        res.status(422).json({ error: "Bench Run 的 Evolve Task/Step 引用不一致" }); return;
      }
      const reportedMetrics = isRecord((output as Record<string, unknown>).metrics)
        ? (output as Record<string, unknown>).metrics as Record<string, unknown>
        : {};
      output = {
        ...(output as Record<string, unknown>),
        domainId: run.domain_id,
        detailUrl: `/evolve/bench/runs/${encodeURIComponent(run.bench_run_id)}`,
        metrics: {
          ...reportedMetrics,
          score: run.score,
          maxScore: run.max_score,
          scoreRatio: run.score != null && run.max_score ? run.score / run.max_score : null,
          passRate: run.pass_rate,
        },
      };
    }
    if (TERMINAL_STATUSES.has(step.status)) {
      if (step.status === status) {
        if (status === "succeeded" && step.step_type === "skill_init") {
          const task = await repo.findTask(step.task_id);
          if (!task) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
          const next = await dispatchPendingBusinessStep({
            repo, dispatch, task,
            callbackUrl: (createdStepId) => botCallbackUrl(req, task.task_id, createdStepId),
          });
          res.json({
            ok: true, duplicate: true, revised: false,
            stepId, status: step.status,
            nextStep: next ? { stepId: next.step_id, stepType: next.step_type, roundNo: next.round_no } : null,
          });
          return;
        }
        if (status === "succeeded" && step.step_type === "bench_plan") {
          const task = await repo.findTask(step.task_id);
          if (!task) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
          const nextStep = await createBenchEvolutionOptimizeStep(req, repo, dispatch, task);
          res.json({ ok: true, duplicate: true, revised: false, stepId, status: step.status, nextStep });
          return;
        }
        if (status === "succeeded" && output !== undefined && step.step_type !== "optimize") {
          const outputError = validateStepOutput(step.step_type, output);
          if (outputError) { res.status(422).json({ error: outputError }); return; }
        }
        if (status === "succeeded" && output !== undefined && step.step_type === "pack") {
          const packTask = await repo.findTask(step.task_id);
          if (!packTask) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
          await registerStepPacks(repo, packTask, step, output);
        }
        const summaryChanged = summary !== undefined && String(summary) !== (step.summary ?? "");
        const outputChanged = output !== undefined
          && JSON.stringify(output) !== JSON.stringify(parseJson(step.output_json));
        if (status === "succeeded" && (summaryChanged || outputChanged)) {
          await repo.reviseSucceededStep(stepId, {
            summary: summary === undefined ? undefined : String(summary),
            output: output === undefined ? undefined : output as Record<string, unknown>,
          });
          if (step.step_type !== "optimize") {
            res.json({
              ok: true, duplicate: false, revised: true,
              stepId, status: step.status, nextStep: null,
            });
            return;
          }
        }
        if (status === "succeeded" && step.step_type === "optimize") {
          const effectiveOutput = output === undefined ? parseJson(step.output_json) : output;
          const nextStep = await advanceOptimizeTask(req, repo, dispatch, step, effectiveOutput, improvementRepo);
          res.json({
            ok: true, duplicate: true, revised: summaryChanged || outputChanged,
            stepId, status: step.status, nextStep,
          });
          return;
        }
        res.json({ ok: true, duplicate: true, revised: false, stepId, status: step.status, nextStep: null }); return;
      }
      res.status(409).json({ error: `step 已处于终态: ${step.status}` }); return;
    }
    if (status === "succeeded" && step.step_type !== "optimize") {
      const outputError = validateStepOutput(step.step_type, output);
      if (outputError) { res.status(422).json({ error: outputError }); return; }
    }
    if (status === "succeeded" && step.step_type === "plan") {
      if (!benchTemplateRepo) { res.status(503).json({ error: "Bench Template 数据库不可用" }); return; }
      const task = await repo.findTask(step.task_id);
      if (!task) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
      const value = output as Record<string, unknown>;
      const domains = value.benchDomains as Record<string, unknown>;
      const items = ((value.benchCases as Record<string, unknown>).items as Array<Record<string, unknown>>);
      for (const item of items) {
        const template = item.template as Record<string, unknown>;
        const expectedDomainId = item.split === "train"
          ? String(domains.trainBenchDomainId) : String(domains.testBenchDomainId);
        const ownerUserId = String(template.ownerUserId);
        const domainId = String(template.domainId);
        const templateName = String(template.templateName);
        const published = await benchTemplateRepo.findByOwnerDomainAndName(ownerUserId, domainId, templateName);
        if (!published || published.status !== "published" || published.published_version == null
          || ownerUserId !== task.user_id || domainId !== expectedDomainId
          || (template.version != null && Number(template.version) !== published.published_version)) {
          res.status(422).json({
            code: "PLAN_TEMPLATE_MISMATCH",
            error: "Plan Bench Case 引用的模板未发布或与冻结 owner/domain/version 不一致",
            ownerUserId, domainId, templateName,
          }); return;
        }
      }
    }
    if (status === "succeeded" && step.step_type === "bench_plan") {
      if (!benchRunRepo) { res.status(503).json({ error: "Bench Run 数据库不可用" }); return; }
      const task = await repo.findTask(step.task_id);
      if (!task) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
      const config = (parseJson(task.config_json) as {
        ownerUserId?: string; trainBenchDomainId?: string; testBenchDomainId?: string;
      } | null) ?? {};
      const baseline = (output as { baseline: {
        train: { benchRunId: string; domainId: string; ownerUserId: string; producerStepId: string };
        test: { benchRunId: string; domainId: string; ownerUserId: string; producerStepId: string };
      } }).baseline;
      if (baseline.train.benchRunId === baseline.test.benchRunId) {
        res.status(422).json({ error: "Train/Test Baseline 必须使用不同的 Bench Run" }); return;
      }
      for (const [role, reported, expectedDomainId] of [
        ["train", baseline.train, config.trainBenchDomainId],
        ["test", baseline.test, config.testBenchDomainId],
      ] as const) {
        const run = await benchRunRepo.findByBenchRunId(reported.benchRunId);
        const runConfig = run ? (parseJson(run.run_config_json) as {
          evolveTaskId?: string; evolveStepId?: string; role?: string; templates?: unknown;
        } | null) : null;
        const actualOwnerId = dbText(run?.owner_user_id).trim();
        const actualDomainId = dbText(run?.domain_id).trim();
        if (!run || run.status !== "succeeded" || actualOwnerId !== String(config.ownerUserId ?? "").trim()
          || actualDomainId !== expectedDomainId || reported.domainId !== expectedDomainId
          || reported.ownerUserId !== actualOwnerId || reported.producerStepId !== step.step_id
          || runConfig?.evolveTaskId !== task.task_id || runConfig?.evolveStepId !== step.step_id
          || runConfig?.role !== `baseline_${role}`) {
          res.status(422).json({
            code: "BENCH_PLAN_RUN_MISMATCH",
            error: `${role} Baseline Bench Run 与冻结的 owner/domain 或终态不一致`,
            benchRunId: reported.benchRunId, expectedOwnerId: config.ownerUserId,
            expectedDomainId, actualOwnerId, actualDomainId,
            actualStatus: run?.status,
          }); return;
        }
      }
    }

    await repo.updateStepStatus(stepId, {
      status: String(status), summary: summary == null ? undefined : String(summary),
      output: status === "succeeded" ? output as Record<string, unknown> : undefined,
      errorCode: error?.code == null ? undefined : String(error.code),
      errorMessage: error?.message == null ? undefined : String(error.message),
      retryable: error?.retryable == null ? undefined : Boolean(error.retryable),
    });
    if (status === "succeeded" && step.step_type === "skill_init") {
      const task = await repo.findTask(step.task_id);
      if (!task) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
      const next = await dispatchPendingBusinessStep({
        repo, dispatch, task,
        callbackUrl: (createdStepId) => botCallbackUrl(req, task.task_id, createdStepId),
      });
      res.json({
        ok: true, duplicate: false, stepId, status,
        nextStep: next ? { stepId: next.step_id, stepType: next.step_type, roundNo: next.round_no } : null,
      });
      return;
    }
    if (status === "succeeded" && step.step_type === "pack") {
      const packTask = await repo.findTask(step.task_id);
      if (!packTask) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
      await registerStepPacks(repo, packTask, step, output);
    }

    const reportedErrorCode = error?.code == null ? "" : String(error.code);
    const isPlanSourceFailure = ["PLAN_SOURCE_", "EVIDENCE_", "SOURCE_"]
      .some((prefix) => reportedErrorCode.startsWith(prefix));
    if (status === "failed" && step.step_type === "plan" && taskSourceService && isPlanSourceFailure) {
      const task = await repo.findTask(step.task_id);
      if (task && isInsightImprovementTask(task)) {
        await taskSourceService.markRuntimeFailure(
          step.task_id,
          reportedErrorCode,
          error?.message == null ? "Plan Source 解析失败" : String(error.message),
        );
      }
    }

    if (status === "succeeded" && step.step_type === "diagnose") {
      const diagnoseCases = (output as { cases?: { items?: unknown[] } }).cases?.items;
      if (Array.isArray(diagnoseCases) && diagnoseCases.length === 0) {
        await repo.completeTask(step.task_id);
        res.json({
          ok: true,
          duplicate: false,
          stepId,
          status,
          nextStep: null,
          reason: "diagnose_no_cases",
        });
        return;
      }
      const task = await repo.findTask(step.task_id);
      if (!task) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
      const nextStep = await createPlanStep(req, repo, dispatch, step, task);
      res.json({
        ok: true, duplicate: false, stepId: stepId, status,
        nextStep,
      });
      return;
    }
    if (status === "succeeded" && step.step_type === "plan") {
      const task = await repo.findTask(step.task_id);
      if (!task) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
      if (task.task_type === "full") {
        const domains = (output as { benchDomains: { trainBenchDomainId: string; testBenchDomainId: string } }).benchDomains;
        const config = {
          ...((parseJson(task.config_json) as Record<string, unknown> | null) ?? {}),
          trainBenchDomainId: domains.trainBenchDomainId,
          testBenchDomainId: domains.testBenchDomainId,
          maxRounds: Number((parseJson(task.config_json) as Record<string, unknown> | null)?.maxRounds ?? 3),
        };
        await repo.updateTaskConfig(task.task_id, config);
        const updatedTask = await repo.findTask(task.task_id);
        if (!updatedTask) { res.status(409).json({ error: "任务不存在" }); return; }
        const nextStep = await createOptimizeStep(req, repo, dispatch, updatedTask, 1);
        res.json({ ok: true, duplicate: false, stepId, status, nextStep });
        return;
      }
      await repo.completeTask(step.task_id);
    }
    if (status === "succeeded" && step.step_type === "bench_plan") {
      const task = await repo.findTask(step.task_id);
      if (!task) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
      const nextStep = await createBenchEvolutionOptimizeStep(req, repo, dispatch, task);
      res.json({ ok: true, duplicate: false, stepId, status, nextStep });
      return;
    }
    if (status === "succeeded" && step.step_type === "bench") {
      await repo.completeTask(step.task_id);
    }
    if (status === "succeeded" && (step.step_type === "pack" || step.step_type === "restore" || step.step_type === "runtime_cleanup")) {
      await repo.completeTask(step.task_id);
    }
    if (status === "succeeded" && step.step_type === "optimize") {
      const nextStep = await advanceOptimizeTask(req, repo, dispatch, step, output, improvementRepo);
      res.json({ ok: true, duplicate: false, stepId, status, nextStep });
      return;
    }

    if ((status === "succeeded" || status === "failed") && step.step_type === "run_analysis") {
      const task = await repo.findTask(step.task_id);
      if (!task) { res.status(409).json({ error: "step 关联任务不存在" }); return; }
      const taskConfig = parseJson(task.config_json) as Record<string, unknown> | null;
      const flowId = String(taskConfig?.flowId ?? "");
      try {
        if (status === "succeeded" && flowId) {
          await repo.completeFlowAnalysis(flowId);
        } else if (flowId) {
          await repo.failFlowAnalysis(flowId);
        }
      } catch (e) {
        console.warn("[clawweb][evolve][run-analysis-report] 更新 flow_runs 分析状态失败", { flowId, error: e instanceof Error ? e.message : String(e) });
      }
      await repo.completeTask(step.task_id);
      res.json({ ok: true, duplicate: false, stepId, status, nextStep: null, flowId });
      return;
    }

    if ((status === "succeeded" || status === "failed") && step.step_type === "suggestion_apply") {
      const task = await repo.findTask(step.task_id);
      if (!task) { res.status(409).json({ error: "step \u5173\u8054\u4efb\u52a1\u4e0d\u5b58\u5728" }); return; }
      const config = parseJson(task.config_json) as Record<string, unknown> | null;
      const suggestionIds = Array.isArray(config?.suggestionIds)
        ? config.suggestionIds.map(String).filter(Boolean)
        : [String(config?.suggestionId ?? "")].filter(Boolean);
      const suggestionId = suggestionIds[0] ?? "";
      const workflowId = String(config?.workflowId ?? "");
      const succeeded = status === "succeeded";
      const note = summary == null ? undefined : String(summary);
      const updatedSuggestions: EvolveSuggestionRow[] = [];
      for (const currentSuggestionId of suggestionIds) {
        try {
          const actor = userId ?? task.created_by ?? "system";
          const updated = succeeded
            ? await repo.markSuggestionAppliedUnverified(currentSuggestionId, {
              actor,
              note: note ?? "Bot 已完成工作流修改，尚未验证业务效果",
            })
            : await repo.updateSuggestionStatus(currentSuggestionId, "failed", {
              action: "failed",
              actor,
              note: note ?? String(error?.message ?? "Bot 应用建议失败"),
              timestamp: new Date().toISOString(),
            });
          if (updated) updatedSuggestions.push(updated);
        } catch (e) {
          console.warn("[clawweb][evolve][suggestion-apply-report] \u66f4\u65b0 suggestion \u72b6\u6001\u5931\u8d25", { suggestionId: currentSuggestionId, error: e instanceof Error ? e.message : String(e) });
        }
        try {
          await repo.recordSuggestionOutcome({
            suggestionId: currentSuggestionId,
            workflowId,
            nodeId: null,
            action: "suggestion_apply",
            applied: succeeded,
            succeeded,
            verdict: succeeded ? "application_succeeded" : "application_failed",
            note: note ?? (succeeded ? undefined : String(error?.message ?? "")),
            sourceTaskId: task.task_id,
            sourceStepId: step.step_id,
            createdBy: userId ?? task.created_by,
          });
        } catch (e) {
          console.warn("[clawweb][evolve][suggestion-apply-report] \u8bb0\u5f55 outcome \u5931\u8d25", { taskId: task.task_id, stepId, suggestionId: currentSuggestionId, error: e instanceof Error ? e.message : String(e) });
        }
      }
      await repo.completeTask(step.task_id);
      res.json({
        ok: true, duplicate: false, stepId, status, nextStep: null,
        suggestionId,
        suggestionIds,
        suggestionStatus: updatedSuggestions[0]?.status,
        suggestionStatuses: updatedSuggestions.map((item) => ({ suggestionId: String(item.id), status: item.status })),
      });
      return;
    }
    res.json({ ok: true, duplicate: false, stepId: stepId, status, nextStep: null });
  }));

  // Workflow-evolution knowledge endpoints. Mounted AFTER the legacy task-creating
  // POST /diagnoses so that legacy callers keep their route unchanged while new
  // GET/POST/PATCH /lessons and GET/diagnoses/:id/promote endpoints are available.

  router.get("/runs/eligible-bots", asyncHandler(async (req: Request, res: Response) => {
    if (!repo || !botWorkflowPermissionRepo) {
      res.status(503).json({ error: "服务不可用", message: "Evolve repository 未配置" });
      return;
    }
    const userId = resolveRequestUserId(req);
    if (!userId) { res.status(401).json({ error: "未登录" }); return; }
    const workflowId = textOrNull(req.query.workflowId);
    if (!workflowId) { res.status(400).json({ error: "workflowId 为必填项" }); return; }
    const bots = await repo.listEligibleBotsForAnalyze(userId, workflowId);
    res.json({ bots });
  }));

  router.get("/runs/:flowId/analysis-result", asyncHandler(async (req: Request, res: Response) => {
    if (!repo || !workflowEvolutionRepo) {
      res.status(503).json({ error: "服务不可用", message: "Workflow Evolution repository 未配置" });
      return;
    }
    const flowId = String(req.params.flowId ?? "").trim();
    if (!flowId) { res.status(400).json({ error: "flowId 为必填项" }); return; }
    const workflowId = await repo.getWorkflowIdByFlowId(flowId);
    if (!workflowId) { res.status(404).json({ error: "未找到该 flow 对应的工作流" }); return; }
    if (!await requireWorkflowAccess(req, res, botWorkflowPermissionRepo, workflowId, "view")) return;

    const requestedAnalysisId = textOrNull(req.query.analysisId);
    const analysis = requestedAnalysisId
      ? await workflowEvolutionRepo.findAnalysisRunForFlow(requestedAnalysisId, flowId)
      : await workflowEvolutionRepo.findLatestAnalysisRunByFlow(flowId);
    if (!analysis || (analysis.workflow_id != null && analysis.workflow_id !== workflowId)) {
      if (requestedAnalysisId) {
        res.status(404).json({ error: "未找到该运行对应的分析结果" });
        return;
      }
      res.json({ analysis: null });
      return;
    }

    const base = {
      analysisId: analysis.analysis_id,
      flowId,
      workflowId: analysis.workflow_id ?? workflowId,
      status: analysis.status,
      evidenceStatus: analysis.evidence_status,
      requestedAtMs: analysis.requested_at_ms,
      completedAtMs: analysis.completed_at_ms,
      errorCode: analysis.error_code,
      facts: [] as string[],
      inferences: [] as string[],
      unknowns: [] as string[],
      diagnoses: [] as Array<Record<string, unknown>>,
    };
    if (analysis.status !== "completed" || !analysis.result_json) {
      res.json({ analysis: base });
      return;
    }

    let result;
    try {
      result = validateWorkflowEvolutionAnalysisResult(JSON.parse(analysis.result_json));
      if (result.analysisId !== analysis.analysis_id) throw new Error("analysis result identity mismatch");
    } catch (error) {
      res.status(500).json({
        code: "ANALYSIS_RESULT_INVALID",
        error: "分析结果格式无效",
        message: error instanceof Error ? error.message : String(error),
      });
      return;
    }
    const citedIds = result.diagnoses.flatMap((diagnosis) => diagnosis.evidenceEventIds);
    const evidenceRows = await workflowEvolutionRepo.listEvidenceByEventIds(citedIds);
    const evidenceById = new Map(evidenceRows.map((row) => [row.event_id, row]));
    res.json({
      analysis: {
        ...base,
        facts: result.facts,
        inferences: result.inferences,
        unknowns: result.unknowns,
        diagnoses: result.diagnoses.map((diagnosis) => ({
          ...diagnosis,
          sourceEvidence: presentEvidence(diagnosis.evidenceEventIds, evidenceById),
        })),
      },
    });
  }));

  router.get("/runs/:flowId/analysis-progress", asyncHandler(async (req: Request, res: Response) => {
    if (!repo || !workflowEvolutionRepo) {
      res.status(503).json({ error: "服务不可用", message: "Workflow Evolution repository 未配置" });
      return;
    }
    const flowId = String(req.params.flowId ?? "").trim();
    if (!flowId) { res.status(400).json({ error: "flowId 为必填项" }); return; }
    const analysis = await workflowEvolutionRepo.findLatestAnalysisRunByFlow(flowId);
    if (!analysis) {
      res.json({ analysisId: null, status: null, progress: null });
      return;
    }
    if (!analysis.task_id || !analysis.step_id) {
      res.json({ analysisId: analysis.analysis_id, status: analysis.status, progress: null });
      return;
    }
    const [task, step] = await Promise.all([
      repo.findTask(analysis.task_id),
      repo.findStep(analysis.step_id),
    ]);
    if (!task || !step || step.task_id !== task.task_id || step.step_type !== "run_analysis") {
      res.status(409).json({ error: "分析任务绑定无效" });
      return;
    }
    if (!canReadTask(req, task)) { res.status(403).json({ error: "无权查看该分析任务" }); return; }
    const output = parseJson(step.output_json) as { analysisProgress?: unknown } | null;
    res.json({
      analysisId: analysis.analysis_id,
      status: analysis.status,
      taskId: analysis.task_id,
      stepId: analysis.step_id,
      progress: isRecord(output?.analysisProgress) ? output.analysisProgress : null,
    });
  }));

  router.post("/runs/:flowId/analyze", asyncHandler(async (req: Request, res: Response) => {
    if (!repo || !botWorkflowPermissionRepo) {
      res.status(503).json({ error: "服务不可用", message: "Evolve repository 未配置" });
      return;
    }
    const userId = resolveRequestUserId(req);
    if (!userId) { res.status(401).json({ error: "未登录" }); return; }
    const flowId = String(req.params.flowId ?? "").trim();
    if (!flowId) { res.status(400).json({ error: "flowId 为必填项" }); return; }
    if (runAnalysisStarter) {
      const requestBody = (req.body ?? {}) as Record<string, unknown>;
      try {
        const result = await runAnalysisStarter.start({
          flowId,
          userId,
          botId: String(requestBody.botId ?? "").trim() || undefined,
          botEnv: requestBody.botEnv ? String(requestBody.botEnv) : undefined,
          force: requestBody.force === true,
        });
        res.json(result);
      } catch (error) {
        if (error instanceof RunAnalysisStartError) {
          const payload = error.statusCode === 502
            ? { error: "消息派发失败", message: error.message, ...error.details }
            : { code: error.code, error: error.message, ...error.details };
          res.status(error.statusCode).json(payload);
          return;
        }
        throw error;
      }
      return;
    }
    const workflowId = await repo.getWorkflowIdByFlowId(flowId);
    if (!workflowId) { res.status(404).json({ error: "未找到该 flow 对应的工作流" }); return; }

    // Guard against duplicate in-flight analysis tasks for the same run.
    const existingTask = await repo.findRunningRunAnalysisTask(flowId);
    const runAnalysisTimeoutMs = 30 * 60 * 1000;
    const dispatchedStallMs = 5 * 60 * 1000;
    if (existingTask) {
      const createdAt = typeof existingTask.gmt_create === "number"
        ? existingTask.gmt_create * 1000
        : new Date(existingTask.gmt_create).getTime();
      const stuckMs = Date.now() - createdAt;
      const inFlightStatuses = ["created", "dispatching", "pending", "running", "analyzing"];

      // Allow retry only if the old task is not stuck.
      if (existingTask.status === "dispatched" && stuckMs < dispatchedStallMs) {
        res.json({ ok: true, taskId: existingTask.task_id, stepId: existingTask.step_id, status: existingTask.status, duplicate: true });
        return;
      }
      if (inFlightStatuses.includes(existingTask.status) && stuckMs < runAnalysisTimeoutMs) {
        res.json({ ok: true, taskId: existingTask.task_id, stepId: existingTask.step_id, status: existingTask.status, duplicate: true });
        return;
      }

      // Old task is stuck; mark it failed so the guard does not block the new one.
      await repo.updateStepStatus(existingTask.step_id, {
        status: "failed",
        errorCode: "RUN_ANALYSIS_TIMEOUT",
        errorMessage: stuckMs >= runAnalysisTimeoutMs
          ? "Analysis task timed out; user re-triggered"
          : "User re-triggered analysis; previous dispatched task canceled",
      });
    }

    // Load run metadata so we can prefer the originating bot.
    const run = await repo.getFlowRun(flowId);
    const originBotIdRaw = run?.origin_bot_id ?? null;
    const originBotId = originBotIdRaw ? String(originBotIdRaw).split(":")[0].trim() : null;

    const body = (req.body ?? {}) as Record<string, unknown>;
    let botId = String(body.botId ?? "").trim();
    let botEnv = body.botEnv ? String(body.botEnv) : undefined;
    const force = body.force === true;
    const eligible = await repo.listEligibleBotsForAnalyze(userId, workflowId);

    function isEligible(id: string, env?: string) {
      return eligible.some((b) => b.botId === id && (env == null || b.env === env));
    }

    if (!botId && originBotId && isEligible(originBotId)) {
      // Prefer the bot that actually ran this flow.
      botId = originBotId;
      botEnv = undefined; // env will be resolved from ac_bots if not provided
    }

    if (!botId) {
      // Auto-select the first eligible OpenClaw bot so the UI can trigger analysis with one click.
      for (const b of eligible) {
        const candRuntime = await repo.resolveEvolveBotRuntime(userId, b.botId, b.env ?? undefined);
        if (candRuntime && (!candRuntime.activeEngine || candRuntime.activeEngine.toLowerCase() === "openclaw")) {
          botId = b.botId;
          botEnv = b.env ?? undefined;
          break;
        }
      }
    }
    if (!botId) { res.status(400).json({ error: "botId 为必填项，且未找到可用的 OpenClaw Bot" }); return; }
    if (!eligible.some((b) => b.botId === botId && (botEnv == null || b.env === botEnv))) {
      res.status(403).json({ error: "所选 Bot 没有该 workflow 的分析/执行权限" });
      return;
    }
    const runtime = await repo.resolveEvolveBotRuntime(userId, botId, botEnv);
    if (!runtime) { res.status(404).json({ error: "无法解析 Bot 运行时" }); return; }
    if (runtime.activeEngine && runtime.activeEngine.toLowerCase() !== "openclaw") {
      res.status(422).json({
        code: "EVOLVE_ENGINE_UNSUPPORTED",
        error: `当前分析任务仅支持 OpenClaw 引擎，所选 Bot 为 ${runtime.activeEngine}`,
        activeEngine: runtime.activeEngine,
      });
      return;
    }
    const taskId = evolveTaskId();
    const stepId = `${taskId}-step-analyze`;
    const analysisId = `AN-${randomUUID().replaceAll("-", "").slice(0, 20).toUpperCase()}`;
    const taskName = `运行日志分析：${flowId}`;
    const baseUrl = getClawWebPublicBaseUrl();
    const callbackUrl = `${baseUrl}/api/evolve/internal/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepId)}/bot-callback`;
    const message = buildRunAnalysisMessage({ analysisId, flowId });
    const shortCommand = `[run-analysis] ${flowId}`;
    const started = await repo.startFlowAnalysis(flowId);
    if (!started) {
      // The flow is already marked analyzing by another concurrent task.
      const latest = await repo.findRunningRunAnalysisTask(flowId);
      if (latest) {
        res.json({ ok: true, taskId: latest.task_id, stepId: latest.step_id, status: latest.status, duplicate: true });
        return;
      }
      res.status(409).json({ error: "该运行正在分析中或已分析完成" });
      return;
    }
    if (!workflowEvolutionRepo) {
      await repo.failFlowAnalysis(flowId);
      res.status(503).json({ error: "Evolution analysis repository 未配置" });
      return;
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
        taskId,
        stepId,
      });
    } catch (error) {
      await repo.failFlowAnalysis(flowId);
      res.status(503).json({
        error: "Evolution analysis storage unavailable",
        message: error instanceof Error ? error.message : String(error),
      });
      return;
    }
    await repo.createTaskWithStep({
      task: {
        taskId, taskType: "run_analysis", userId, botId, taskName,
        configJson: JSON.stringify({ analysisId, flowId, workflowId, botId, botEnv: botEnv ?? null, force }),
        createdBy: userId,
      },
      step: { stepId, stepType: "run_analysis", stepNo: 1, command: shortCommand },
    });
    try {
      const dispatchResult = await dispatch({
        taskId, stepPk: 0, stepId, stepType: "run_analysis",
        userId, botId, command: message, mode: "message",
        callbackUrl, runtime, forceMessage: true,
      });
      await repo.markDispatched(stepId, dispatchResult.runId, dispatchResult.sessionId, dispatchResult.platformResponse);
    } catch (dispatchError) {
      console.error(`[clawweb][evolve][run-analysis] dispatch failed for flow ${flowId}`, dispatchError instanceof Error ? dispatchError.message : String(dispatchError));
      await repo.updateStepStatus(stepId, {
        status: "failed",
        errorCode: "DISPATCH_FAILED",
        errorMessage: dispatchError instanceof Error ? dispatchError.message : String(dispatchError),
      });
      await repo.failFlowAnalysis(flowId);
      await workflowEvolutionRepo.failAnalysisRun(analysisId, "DISPATCH_FAILED", Date.now()).catch(() => undefined);
      res.status(502).json({ error: "消息派发失败", message: dispatchError instanceof Error ? dispatchError.message : String(dispatchError) });
      return;
    }
    const diagnosisCount = await repo.countDiagnosesByFlow(flowId);
    const suggestionCount = 0; // suggestion 目前按 workflow 聚合，未按 flow 精确计数
    res.json({ ok: true, analysisId, flowId, status: "analyzing" as const, diagnosisCount, suggestionCount });
  }));

  router.post("/runs/:flowId/reset-analysis", asyncHandler(async (req: Request, res: Response) => {
    if (!repo) { res.status(503).json({ error: "服务不可用" }); return; }
    const userId = resolveRequestUserId(req);
    if (!userId) { res.status(401).json({ error: "未登录" }); return; }
    const flowId = String(req.params.flowId ?? "").trim();
    if (!flowId) { res.status(400).json({ error: "flowId 为必填项" }); return; }
    const workflowId = await repo.getWorkflowIdByFlowId(flowId);
    if (!workflowId) { res.status(404).json({ error: "未找到该 flow 对应的工作流" }); return; }
    const eligible = await repo.listEligibleBotsForAnalyze(userId, workflowId);
    if (eligible.length === 0) { res.status(403).json({ error: "无权重置该运行的分析状态" }); return; }
    const canceled = await repo.resetFlowAnalysis(flowId);
    res.json({ ok: true, flowId, canceled });
  }));

  router.get("/suggestions/apply-tasks", asyncHandler(async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "服务不可用", message: "Evolve repository 未配置" });
      return;
    }
    const userId = resolveRequestUserId(req);
    if (!userId) { res.status(401).json({ error: "未登录" }); return; }
    const raw = req.query.suggestionIds;
    const suggestionIds: string[] = [];
    if (Array.isArray(raw)) {
      for (const item of raw) {
        const s = String(item ?? "").trim();
        if (s) suggestionIds.push(s);
      }
    } else if (typeof raw === "string" && raw.trim()) {
      for (const s of raw.split(",")) {
        const trimmed = s.trim();
        if (trimmed) suggestionIds.push(trimmed); }
    }
    if (suggestionIds.length === 0) { res.status(400).json({ error: "suggestionIds 为必填项" }); return; }
    for (const suggestionId of suggestionIds) {
      const suggestion = await repo.findSuggestionById(suggestionId);
      if (!suggestion) { res.status(404).json({ error: "Suggestion not found" }); return; }
      if (!await requireWorkflowAccess(req, res, botWorkflowPermissionRepo, suggestion.workflow_id, "view")) return;
    }
    const tasks = await repo.listSuggestionApplyTasks(suggestionIds);
    res.json({ tasks });
  }));


  router.get("/suggestions/:suggestionId/eligible-bots", asyncHandler(async (req: Request, res: Response) => {
    if (!repo || !botWorkflowPermissionRepo) {
      res.status(503).json({ error: "\u670d\u52a1\u4e0d\u53ef\u7528", message: "Evolve repository \u672a\u914d\u7f6e" });
      return;
    }
    const userId = resolveRequestUserId(req);
    if (!userId) { res.status(401).json({ error: "\u672a\u767b\u5f55" }); return; }
    const suggestion = await repo.findSuggestionById(String(req.params.suggestionId));
    if (!suggestion) { res.status(404).json({ error: "Suggestion not found" }); return; }
    if (!await requireWorkflowAccess(req, res, botWorkflowPermissionRepo, suggestion.workflow_id, "edit")) return;
    const bots = await repo.listEligibleBotsForSuggestion(userId, suggestion.workflow_id);
    res.json({ bots });
  }));

  const dispatchSuggestionApplication = async (
    res: Response,
    userId: string,
    suggestions: EvolveSuggestionRow[],
    body: Record<string, unknown>,
  ): Promise<{ taskId: string; stepId: string; status: string; reportUrl: string; suggestionIds: string[] } | null> => {
    if (!repo) return null;
    const workflowId = suggestions[0]?.workflow_id;
    const botId = String(body.botId ?? "").trim();
    const botEnv = body.botEnv ? String(body.botEnv) : undefined;
    if (!workflowId || suggestions.length === 0) { res.status(400).json({ error: "suggestionIds 为必填项" }); return null; }
    if (!botId) { res.status(400).json({ error: "botId 为必填项" }); return null; }
    const eligible = await repo.listEligibleBotsForSuggestion(userId, workflowId);
    if (!eligible.some((b) => b.botId === botId && (botEnv == null || b.env === botEnv))) {
      res.status(403).json({ error: "所选 Bot 没有该 workflow 的编辑权限" });
      return null;
    }

    const suggestionIds = suggestions.map((suggestion) => String(suggestion.id));
    const existingTasks = await repo.listSuggestionApplyTasks(suggestionIds);
    const activeTask = existingTasks.find((task) =>
      ["created", "pending", "dispatching", "dispatched", "running", "applying"].includes(task.status));
    if (activeTask) {
      res.status(409).json({
        error: "suggestion_application_active",
        message: `建议已有进行中的应用任务 ${activeTask.taskId}`,
        taskId: activeTask.taskId,
      });
      return null;
    }
    const runtime = await repo.resolveEvolveBotRuntime(userId, botId, botEnv);
    if (!runtime) { res.status(404).json({ error: "无法解析 Bot 运行时" }); return null; }
    if (runtime.activeEngine && runtime.activeEngine.toLowerCase() !== "openclaw") {
      res.status(422).json({
        code: "EVOLVE_ENGINE_UNSUPPORTED",
        error: `当前进化流程仅支持 OpenClaw 引擎，所选 Bot 为 ${runtime.activeEngine}`,
        activeEngine: runtime.activeEngine,
      });
      return null;
    }

    const validatedProposals: Array<ReturnType<typeof validateWorkflowPatchProposal>> = [];
    for (const suggestion of suggestions) {
      if (!suggestion.proposal_json) continue;
      try {
        const proposal = validateWorkflowPatchProposal(JSON.parse(suggestion.proposal_json));
        if (proposal.workflowId !== workflowId || digestCanonicalJson(proposal) !== suggestion.proposal_digest) {
          res.status(400).json({ error: "proposal_digest_mismatch", message: `建议 ${suggestion.id} 的 typed proposal 不完整或已被修改` });
          return null;
        }
        validatedProposals.push(proposal);
      } catch (error) {
        res.status(400).json({
          error: "proposal_invalid",
          message: error instanceof Error ? error.message : String(error),
        });
        return null;
      }
    }
    if (validatedProposals.length > 0) {
      if (!db) {
        res.status(503).json({ error: "workflow_spec_unavailable", message: "无法读取当前 Workflow 配置" });
        return null;
      }
      const current = (await db.query<{ spec_json: unknown }>(
        "SELECT spec_json FROM workflow_specs WHERE workflow_id = ? LIMIT 1",
        [workflowId],
      ))[0];
      let currentSpec: unknown;
      try {
        currentSpec = JSON.parse(dbText(current?.spec_json));
      } catch {
        res.status(409).json({ error: "workflow_spec_unavailable", message: "当前 Workflow 配置不存在或无法解析" });
        return null;
      }
      const currentSpecDigest = digestCanonicalJson(currentSpec);
      if (validatedProposals.some((proposal) => proposal.baseSpecDigest !== currentSpecDigest)) {
        res.status(409).json({
          error: "workflow_spec_changed",
          message: "分析后 Workflow 已发生变化，请重新分析或更新修复要求后再应用",
        });
        return null;
      }
    }

    const defaultSpec = suggestions
      .map((suggestion) => suggestion.fix_spec?.trim() || `${suggestion.failure_signature}（未提供具体修复说明）`)
      .join("\n");
    const applicationSpec = typeof body.applicationSpec === "string" ? body.applicationSpec.trim() : defaultSpec;
    if (!applicationSpec || applicationSpec.length > 20_000) {
      res.status(400).json({ error: "applicationSpec_invalid", message: "本次修复要求不能为空且不能超过 20000 字" });
      return null;
    }
    const edited = applicationSpec !== defaultSpec;
    const taskId = evolveTaskId();
    const stepId = `${taskId}-step-apply`;
    const taskName = suggestions.length === 1
      ? `应用建议：${suggestions[0].failure_signature.slice(0, 40)}`
      : `批量应用 ${suggestions.length} 条工作流建议`;
    const baseUrl = getClawWebPublicBaseUrl();
    const reportUrl = `${baseUrl}/api/evolve/internal/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepId)}/report`;
    const claimToken = randomBytes(32).toString("base64url");
    const claimTokenDigest = createHash("sha256").update(claimToken).digest("hex");
    const proposals = suggestions
      .map((suggestion) => suggestion.proposal_json ? JSON.parse(suggestion.proposal_json) as Record<string, unknown> : null)
      .filter((proposal): proposal is Record<string, unknown> => proposal != null);
    const proposal = proposals.length === 1
      ? proposals[0]
      : proposals.length > 1
        ? { schemaVersion: "workflow-patch-batch/v1", workflowId, proposals }
        : undefined;
    const suggestionRevisions = suggestions.map((suggestion) => ({
      suggestionId: String(suggestion.id),
      proposalDigest: suggestion.proposal_digest ?? null,
      proposal: suggestion.proposal_json ? JSON.parse(suggestion.proposal_json) as Record<string, unknown> : null,
    }));
    const priorTasks = suggestions.length === 1 ? existingTasks : [];
    const previousTask = priorTasks.find((task) => task.proposal
      && ["succeeded", "completed", "applied_unverified"].includes(task.status)
      && task.proposalDigest !== suggestions[0].proposal_digest);
    const previousProposal = previousTask?.proposal ?? undefined;
    const proposalDelta = previousProposal && proposal ? diffSuggestionProposals(previousProposal, proposal) : undefined;
    const diagnosisContext = await buildSuggestionDiagnosisContext(suggestions, workflowEvolutionRepo, repo);
    const message = buildSuggestionApplyMessage({
      taskId,
      stepId,
      workflowId,
      applicationSpec,
      proposal: edited ? undefined : proposal,
      claimToken,
    });
    const shortCommand = suggestions.length === 1
      ? `[suggestion-apply] ${suggestions[0].fix_kind ?? "unknown"} · ${suggestions[0].failure_signature.slice(0, 80)}`
      : `[suggestion-apply] batch · ${suggestions.length} suggestions`;

    await repo.createTaskWithStep({
      task: {
        taskId, taskType: "suggestion_apply", userId, botId, taskName,
        configJson: JSON.stringify({
          suggestionId: suggestionIds[0],
          suggestionIds,
          workflowId,
          botId,
          botEnv: botEnv ?? null,
          applicationMode: "task_guard_orchestrated",
          claimTokenDigest,
          suggestionRevisions,
          applicationInput: {
            workflowId,
            spec: applicationSpec,
            edited,
            originalProposal: proposal ?? null,
            ...(!edited && proposal ? { proposal } : {}),
            ...(previousProposal ? { previousProposal } : {}),
            ...(proposalDelta ? { proposalDelta } : {}),
            ...(diagnosisContext ? { diagnosisContext } : {}),
            deploy: true,
          },
        }),
        createdBy: userId,
      },
      step: { stepId, stepType: "suggestion_apply", stepNo: 1, command: shortCommand },
    });
    const progressStartedAt = Date.now();
    await repo.updateStepStatus(stepId, {
      status: "dispatching",
      summary: "任务正在派发",
      output: {
        applicationProgress: {
          phase: "task_received",
          message: "任务正在派发",
          elapsedMs: 0,
          updatedAtMs: progressStartedAt,
          history: [{
            phase: "task_received",
            message: "任务正在派发",
            updatedAtMs: progressStartedAt,
          }],
        },
      },
    });
    for (const suggestion of suggestions) {
      await repo.updateSuggestionStatus(suggestion.id, "applying", {
        action: "applying",
        actor: userId,
        note: `应用任务 ${taskId} 正在派发`,
        timestamp: new Date().toISOString(),
      });
      await repo.updateDiagnosesSuggestionStatus(workflowId, suggestion.failure_signature, String(suggestion.id), "applying");
    }
    let dispatchResult;
    try {
      dispatchResult = await dispatch({
        taskId, stepPk: 0, stepId, stepType: "suggestion_apply",
        userId, botId, command: message, mode: "message",
        runtime, forceMessage: true,
      });
    } catch (error) {
      const errorMessage = error instanceof Error ? error.message : String(error);
      await repo.markDispatchFailed(stepId, errorMessage);
      for (const suggestion of suggestions) {
        await repo.updateSuggestionStatus(suggestion.id, "failed", {
          action: "failed",
          actor: userId,
          note: `Bot 应用任务派发失败：${errorMessage}`,
          timestamp: new Date().toISOString(),
        });
        await repo.updateDiagnosesSuggestionStatus(workflowId, suggestion.failure_signature, String(suggestion.id), "failed");
      }
      throw error;
    }
    await repo.markDispatched(stepId, dispatchResult.runId, dispatchResult.sessionId, dispatchResult.platformResponse);
    return { taskId, stepId, status: "running", reportUrl, suggestionIds };
  };

  router.post("/suggestions/apply-batch", asyncHandler(async (req: Request, res: Response) => {
    if (!repo || !botWorkflowPermissionRepo) {
      res.status(503).json({ error: "服务不可用", message: "Evolve repository 未配置" });
      return;
    }
    const userId = resolveRequestUserId(req);
    if (!userId) { res.status(401).json({ error: "未登录" }); return; }
    const body = (req.body ?? {}) as Record<string, unknown>;
    const rawIds = Array.isArray(body.suggestionIds) ? body.suggestionIds : [];
    const suggestionIds = Array.from(new Set(rawIds.map(String).map((id) => id.trim()).filter(Boolean)));
    if (suggestionIds.length === 0) { res.status(400).json({ error: "suggestionIds 为必填项" }); return; }
    if (suggestionIds.length > 20) { res.status(400).json({ error: "单次最多应用 20 条建议" }); return; }
    const suggestions: EvolveSuggestionRow[] = [];
    for (const suggestionId of suggestionIds) {
      const suggestion = await repo.findSuggestionById(suggestionId);
      if (!suggestion) { res.status(404).json({ error: `Suggestion not found: ${suggestionId}` }); return; }
      suggestions.push(suggestion);
    }
    const workflowId = suggestions[0].workflow_id;
    if (suggestions.some((suggestion) => suggestion.workflow_id !== workflowId)) {
      res.status(400).json({ error: "批量应用仅支持同一 workflow 的建议" });
      return;
    }
    const invalid = suggestions.find((suggestion) => !["pending", "adopted", "failed"].includes(suggestion.status));
    if (invalid) {
      res.status(409).json({ error: `建议 ${invalid.id} 当前状态为 ${invalid.status}，不能发起应用` });
      return;
    }
    if (!await requireWorkflowAccess(req, res, botWorkflowPermissionRepo, workflowId, "edit")) return;
    const result = await dispatchSuggestionApplication(res, userId, suggestions, body);
    if (result) res.json({ ok: true, ...result });
  }));

  router.post("/suggestions/:suggestionId/apply", asyncHandler(async (req: Request, res: Response) => {
    if (!repo || !botWorkflowPermissionRepo) {
      res.status(503).json({ error: "服务不可用", message: "Evolve repository 未配置" });
      return;
    }
    const userId = resolveRequestUserId(req);
    if (!userId) { res.status(401).json({ error: "未登录" }); return; }
    const suggestion = await repo.findSuggestionById(String(req.params.suggestionId));
    if (!suggestion) { res.status(404).json({ error: "Suggestion not found" }); return; }
    if (!await requireWorkflowAccess(req, res, botWorkflowPermissionRepo, suggestion.workflow_id, "edit")) return;
    if (!["pending", "adopted", "failed"].includes(suggestion.status)) {
      res.status(409).json({ error: `当前建议状态为 ${suggestion.status}，不能发起应用` });
      return;
    }
    const result = await dispatchSuggestionApplication(res, userId, [suggestion], (req.body ?? {}) as Record<string, unknown>);
    if (result) res.json({ ok: true, ...result });
  }));


  router.post("/suggestions/:suggestionId/action", asyncHandler(async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "\u670d\u52a1\u4e0d\u53ef\u7528", message: "Evolve repository \u672a\u914d\u7f6e" });
      return;
    }
    const userId = resolveRequestUserId(req);
    if (!userId) { res.status(401).json({ error: "\u672a\u767b\u5f55" }); return; }
    const suggestion = await repo.findSuggestionById(String(req.params.suggestionId));
    if (!suggestion) { res.status(404).json({ error: "Suggestion not found" }); return; }
    if (!await requireWorkflowAccess(req, res, botWorkflowPermissionRepo, suggestion.workflow_id, "edit")) return;
    const body = (req.body ?? {}) as Record<string, unknown>;
    const action = String(body.action ?? "").trim();
    if (!action || !["adopt", "adopted", "reject", "rejected", "bench", "benched", "verify", "verified", "ineffective"].includes(action)) {
      res.status(400).json({ error: "Bad Request", message: "action must be one of adopt, reject, bench, verify, ineffective" });
      return;
    }
    const statusMap: Record<string, string> = { adopt: "adopted", adopted: "adopted", reject: "rejected", rejected: "rejected", bench: "benched", benched: "benched", verify: "verified", verified: "verified", ineffective: "ineffective" };
    const nextStatus = statusMap[action];
    const actor = userId || (typeof body.actor === "string" ? body.actor : "system");
    const note = typeof body.note === "string" ? body.note : null;
    const updated = nextStatus === "verified" || nextStatus === "ineffective"
      ? await repo.markSuggestionVerification(suggestion.id, nextStatus, { actor, note })
      : await repo.updateSuggestionStatus(suggestion.id, nextStatus, {
        action, actor, note, timestamp: new Date().toISOString(),
      });
    if (!updated) {
      res.status(500).json({ error: "Internal Server Error", message: "\u66f4\u65b0 suggestion \u72b6\u6001\u5931\u8d25" });
      return;
    }
    await repo.updateDiagnosesSuggestionStatus(suggestion.workflow_id, suggestion.failure_signature, String(suggestion.id), nextStatus);
    const recorded = await repo.recordSuggestionAction({
      workflowId: suggestion.workflow_id,
      signature: suggestion.failure_signature,
      action: nextStatus,
      nodeId: suggestion.node_id,
      fixKind: suggestion.fix_kind,
      note,
      createdBy: actor,
    });
    res.json({ suggestion: updated, action: recorded });
  }));

  router.use(createEvolveKnowledgeRouter(
    db ?? (repo as unknown as { db: IDatabase }).db,
    repo,
    botWorkflowPermissionRepo,
  ));

  return router;
}
