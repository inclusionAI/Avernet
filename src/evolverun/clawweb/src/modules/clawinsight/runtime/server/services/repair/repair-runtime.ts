import { createHash, randomUUID } from "node:crypto";
import type { EvolveRepository, EvolveStepRow, EvolveTaskRow } from "../../repositories/evolve-repository.js";
import {
  RepairRepository,
  assertRepairAuditPersistable,
  RepairToolCallIdempotencyConflictError,
  RepairToolCallWorkloadConflictError,
  type RepairToolCall,
  type RepairToolCallLedgerContext,
  type RepairToolCallTerminalStatus,
} from "../../repositories/repair-repository.js";
import { AisTaskRunner, type AisTaskDefinition } from "../ais-task-runner.js";
import type { AistudioService } from "../aistudio-service.js";
import type { MistOssObjectStore } from "../object-storage/oss-object-store.js";
import type {
  ApprovedRepairPlan,
  RepairAgentExecutionInput,
  RepairAgentMode,
  RepairApplyActionInput,
  RepairArtifactRefreshInput,
  RepairAuthorizationScope,
  RepairExecutionMode,
  RepairCfuseEngine,
  RepairCfuseAuthCodeInput,
  RepairCfuseLoginInput,
  RepairCfuseLoginReportInput,
  RepairCreateTaskInput,
  RepairDecisionInput,
  RepairDiagnosticMode,
  RepairDispatchConfig,
  RepairDiscoveredIdentifierCandidate,
  RepairExecutorOutput,
  RepairIssueInput,
  RepairLogSearchInput,
  RepairOcbContextInput,
  RepairPendingDecision,
  RepairPhase,
  RepairPlanAction,
  RepairPlanArtifact,
  RepairPlanArtifactV2,
  RepairRuntimeInspectInput,
  RepairRuntimeTargetSnapshot,
  RepairSemanticConclusionInput,
  RepairResumeInput,
  RepairInsightSource,
  RepairStepArtifacts,
  RepairHistoryItem,
  RepairInvestigationRequirement,
  RepairTaskConfig,
  RepairTaskContext,
  RepairTarget,
  RepairTargetEnvironment,
  RepairWorkloadIdentity,
} from "./contracts.js";
import {
  LEGACY_REPAIR_PLAN_VERSION,
  REPAIR_CONTRACT_VERSION,
  REPAIR_PLAN_VERSION,
} from "./contracts.js";
import {
  resolveRepairTaskControlPlaneEnvironment,
  type RepairConfig,
} from "./config.js";
import {
  RepairError,
  repairFinalizationRejected,
  repairForbidden,
  repairNotFound,
  repairUnavailable,
  repairValidation,
} from "./errors.js";
import { deriveRepairLogSourceCoverage, type RepairLogTool } from "./log-tool.js";
import {
  parseOcbRepairOperation,
  type OcbRepairGateway,
  type OcbRepairGatewayResult,
  type OcbRepairOperation,
} from "./ocb-gateway.js";
import type { RepairTargetResolver } from "./repository-target-resolver.js";
import { containsRepairSecret, redactPersistableText, redactText, redactValue } from "./redaction.js";
import { buildRepairRuntimeCommand, type RepairRuntimeTool } from "./runtime-tool.js";
import type { ImprovementDetail } from "../insight/contracts.js";
import { issueRepairExecutionTicket } from "./workload-verifier.js";
import {
  projectInsightPlanSourceForRepair,
  unavailableInsightPlanSource,
} from "./insight-plan-source-projection.js";

const REPAIR_PARAMS_KEY = "${clawevolve_params}";
const REPAIR_TOOL_REQUEST_SCHEMA_VERSION = "repair-tool-request/v1";
const REPAIR_STEP_FAILURE_VERSION = "ce-repair-step-failure/v1";
const REPAIR_RECOVERY_CONTEXT_VERSION = "ce-repair-recovery-context/v1";
const MAX_RECOVERY_CHECKPOINT_BYTES = 64 * 1024;
const MAX_RECOVERY_CONTEXT_TOOL_CALLS = 40;
const RECOVERY_CONTEXT_HEAD_TOOL_CALLS = 8;
const MAX_STEP_AUDIT_CALLS = 50_000;
const TERMINAL_STEP_STATUSES = new Set(["succeeded", "failed", "canceled", "interrupted"]);
const TERMINAL_TOOL_STATUSES = new Set(["succeeded", "failed", "unknown", "canceled"]);
const WRITE_OCB_OPERATIONS = new Set(["restart_bot"]);
const HISTORICAL_WRITE_OCB_OPERATIONS = new Set([
  ...WRITE_OCB_OPERATIONS,
  "engine_config_patch",
  "engine_config_replace",
  "identity_file_replace",
]);
const REPAIR_PLAN_QUALITIES = new Set(["verified", "partially_verified", "blocked", "unknown"] as const);
const REPAIR_PLAN_DISPOSITIONS = new Set(["execute_actions", "no_change", "insufficient_evidence"] as const);
const REPAIR_APPLY_RESULT_VERSION = "ce-repair-apply-result/v1";
const REPAIR_APPLY_ACTION_STATUSES = new Set(["succeeded", "failed", "skipped", "blocked", "unknown"] as const);
const REPAIR_APPLY_ATTEMPT_STATUSES = new Set(["succeeded", "failed", "unknown"] as const);
const REPAIR_APPLY_VERIFICATION_STATUSES = new Set([
  "verified", "partially_verified", "failed", "blocked", "unknown",
] as const);
const REPAIR_APPLY_VERDICTS = new Set([
  "verified", "partially_verified", "failed", "blocked", "unknown",
] as const);
const MAX_APPLY_ATTEMPTS = 500;
const REPAIR_RECOVERY_PROGRESS_VERSION = "ce-repair-recovery-progress/v1";
const RAW_PROCESS_SIGNAL_COMMAND = /(?:^|[\s;&|()])(?:[^\s;&|()]*\/)?(?:kill|pkill|killall)(?=$|[\s;&|()])/iu;
const SHELL_TOKEN_DECORATION = /["'\\`$]/gu;
const REPAIR_AGENT_MODES = new Set<RepairAgentMode>(["openclaw", "cfuse"]);
const CFUSE_ENGINES = new Set<RepairCfuseEngine>(["cfuse", "claude-code"]);
const PERSISTED_CFUSE_ENGINES = new Set(["cfuse", "claude-code", "codex"] as const);
const REPAIR_DIAGNOSTIC_MODES = new Set<RepairDiagnosticMode>(["observe", "deep"]);
const CFUSE_LOGIN_ORIGIN = "https://codefuse.antgroup-inc.cn";
const CFUSE_LOGIN_PATH = "/cloud/oauth";

type CfuseAuthCodeSlot = {
  taskId: string;
  stepId: string;
  executionId: string;
  authorizationScopeDigest: string;
  expiresAt: number;
  state: "available" | "taken";
  authCode: string | null;
};

type RepairAgentSelection = Omit<Pick<RepairTaskConfig,
  "agentMode"
  | "llmUseDefault"
  | "llmModel"
  | "openclawUsesCustomApiKey"
  | "cfuseEngine"
  | "cfuseModel"
>, "cfuseEngine"> & { cfuseEngine: RepairCfuseEngine | null; llmApiKey: string | null };

export type RepairInsightBridge = {
  getDetail: (ownerUserId: string, improvementId: number, allowAdmin: boolean) => Promise<ImprovementDetail | null>;
  findLinkByRequest: (improvementId: number, requestId: string) => Promise<{ evolve_task_id: string } | null>;
  freezeTask: (input: {
    taskId: string;
    detail: ImprovementDetail;
    target: { ownerUserId: string; botId: string; selectedBy: string; crossBotConfirmed: boolean };
    repairDirection?: string | null;
    adminOverride?: { mode: "ADMIN_ONCE"; operatorUserId: string; reason: string; repairDirection: string | null };
  }) => Promise<void>;
  linkTask: (input: {
    improvementId: number;
    ownerUserId: string;
    evolveTaskId: string;
    requestId: string;
    createdBy: string;
  }) => Promise<void>;
  resolvePlanSource: (taskId: string) => Promise<unknown>;
  markApplied: (input: { taskId: string; improvementId: number; requestId: string; appliedBy: string }) => Promise<void>;
  ensurePersistentAuthorization?: (input: {
    ownerUserId: string;
    botId: string;
    improvement: ImprovementDetail;
    grantedBy: string;
    adminConsentToken?: string;
  }) => Promise<{ grantId: number }>;
  validatePersistentAuthorization?: (input: {
    ownerUserId: string;
    botId: string;
    improvement: ImprovementDetail;
    grantId: number;
  }) => Promise<void>;
};

export type RepairTaskServiceDeps = {
  config: RepairConfig;
  repo: EvolveRepository;
  repairRepo: RepairRepository;
  store: MistOssObjectStore;
  ais: AistudioService;
  targets: RepairTargetResolver;
  ocb: OcbRepairGateway;
  logs: RepairLogTool;
  runtimeTool: RepairRuntimeTool;
  insightBridge?: RepairInsightBridge;
  nowSeconds?: () => number;
};

function sha256(value: string | Buffer): string {
  return createHash("sha256").update(value).digest("hex");
}

function requiredText(value: unknown, field: string, maxLength: number): string {
  const text = typeof value === "string" || typeof value === "number" ? String(value).trim() : "";
  if (!text) repairValidation("invalid_repair_input", `${field} 为必填项`);
  if (text.length > maxLength || /[\r\n\0]/u.test(text)) {
    repairValidation("invalid_repair_input", `${field} 格式不合法或超过 ${maxLength} 字符`);
  }
  return text;
}

function optionalText(value: unknown, field: string, maxLength: number): string | null {
  if (value == null || value === "") return null;
  return requiredText(value, field, maxLength);
}

function requiredMultilineText(value: unknown, field: string, maxLength: number): string {
  const text = (typeof value === "string" || typeof value === "number" ? String(value) : "")
    .replace(/\r\n?/gu, "\n")
    .trim();
  if (!text) repairValidation("invalid_repair_input", `${field} 为必填项`);
  if (text.length > maxLength || text.includes("\0")) {
    repairValidation("invalid_repair_input", `${field} 格式不合法或超过 ${maxLength} 字符`);
  }
  return text;
}

function optionalMultilineText(value: unknown, field: string, maxLength: number): string | null {
  if (value == null || value === "") return null;
  return requiredMultilineText(value, field, maxLength);
}

function auditText(value: unknown, field: string, maxLength: number): string {
  const text = requiredText(value, field, maxLength);
  if (text.length < 4) {
    repairValidation("invalid_repair_audit_text", `${field} 必须是至少 4 个字符的单行说明`);
  }
  if (containsRepairSecret(text)) {
    repairValidation("repair_audit_secret_forbidden", `${field} 不能包含凭据或密钥`);
  }
  return text;
}

function optionalSecretText(value: unknown, field: string, maxLength: number): string | null {
  if (value == null || value === "") return null;
  if (typeof value !== "string") {
    return repairValidation(`invalid_${field.toLowerCase()}`, `${field} 格式不合法`);
  }
  const text = value.trim();
  if (!text || text.length > maxLength || /[\r\n\0]/u.test(text)) {
    return repairValidation(`invalid_${field.toLowerCase()}`, `${field} 格式不合法`);
  }
  return text;
}

function agentMode(value: unknown, fallback?: RepairAgentMode): RepairAgentMode {
  if (value == null || value === "") {
    if (fallback) return fallback;
    return repairValidation("invalid_repair_agent_mode", "agentMode 必须是 openclaw 或 cfuse");
  }
  const mode = requiredText(value, "agentMode", 16).toLowerCase();
  if (!REPAIR_AGENT_MODES.has(mode as RepairAgentMode)) {
    return repairValidation("invalid_repair_agent_mode", "agentMode 必须是 openclaw 或 cfuse");
  }
  return mode as RepairAgentMode;
}

function inputBoolean(value: unknown, field: string, fallback?: boolean): boolean {
  if (value == null) {
    if (fallback != null) return fallback;
    return repairValidation(`invalid_${field.toLowerCase()}`, `${field} 必须是 boolean`);
  }
  if (typeof value !== "boolean") {
    return repairValidation(`invalid_${field.toLowerCase()}`, `${field} 必须是 boolean`);
  }
  return value;
}

function diagnosticMode(value: unknown, fallback: RepairDiagnosticMode = "observe"): RepairDiagnosticMode {
  if (value == null || value === "") return fallback;
  const mode = requiredText(value, "diagnosticMode", 16).toLowerCase();
  if (!REPAIR_DIAGNOSTIC_MODES.has(mode as RepairDiagnosticMode)) {
    return repairValidation("invalid_repair_diagnostic_mode", "diagnosticMode 必须是 observe 或 deep");
  }
  return mode as RepairDiagnosticMode;
}

function modelName(value: unknown, field: "llmModel" | "cfuseModel"): string {
  return requiredText(value, field, 128);
}

function cfuseEngine(value: unknown): RepairCfuseEngine {
  const engine = requiredText(value, "cfuseEngine", 32).toLowerCase();
  if (!CFUSE_ENGINES.has(engine as RepairCfuseEngine)) {
    return repairValidation("invalid_cfuse_engine", "cfuseEngine 必须是 cfuse 或 claude-code");
  }
  return engine as RepairCfuseEngine;
}

function executionSupported(config: RepairTaskConfig): boolean {
  return config.agentMode !== "cfuse"
    || (config.cfuseEngine != null && CFUSE_ENGINES.has(config.cfuseEngine as RepairCfuseEngine));
}

function assertExecutionSupported(config: RepairTaskConfig): asserts config is RepairTaskConfig & {
  cfuseEngine: RepairCfuseEngine | null;
} {
  if (!executionSupported(config)) {
    throw new RepairError(
      409,
      "repair_legacy_cfuse_engine_unsupported",
      "此历史 Repair 使用已停用的 Codex Engine，只能查看或采纳已有结果，不能继续执行",
    );
  }
}

function hasInput(value: unknown): boolean {
  return value != null && value !== "";
}

function initialAgentSelection(input: RepairAgentExecutionInput): RepairAgentSelection {
  const mode = agentMode(input.agentMode, "openclaw");
  if (mode === "openclaw") {
    if (hasInput(input.cfuseEngine) || hasInput(input.cfuseModel)) {
      return repairValidation("invalid_repair_agent_config", "OpenClaw 模式不能携带 cfuse 配置");
    }
    const useDefault = inputBoolean(input.llmUseDefault, "llmUseDefault", true);
    const llmApiKey = optionalSecretText(input.llmApiKey, "llmApiKey", 8_192);
    if (useDefault) {
      if (hasInput(input.llmModel) || llmApiKey != null) {
        return repairValidation("invalid_openclaw_default_config", "OpenClaw 默认配置不能覆盖模型或 API Key");
      }
      return {
        agentMode: mode,
        llmUseDefault: true,
        llmModel: null,
        openclawUsesCustomApiKey: false,
        cfuseEngine: null,
        cfuseModel: null,
        llmApiKey: null,
      };
    }
    return {
      agentMode: mode,
      llmUseDefault: false,
      llmModel: modelName(input.llmModel, "llmModel"),
      openclawUsesCustomApiKey: llmApiKey != null,
      cfuseEngine: null,
      cfuseModel: null,
      llmApiKey,
    };
  }
  if (input.llmUseDefault != null || hasInput(input.llmModel) || hasInput(input.llmApiKey)) {
    return repairValidation("invalid_repair_agent_config", "cfuse 模式不能携带 OpenClaw 配置或 API Key");
  }
  return {
    agentMode: mode,
    llmUseDefault: true,
    llmModel: null,
    openclawUsesCustomApiKey: false,
    cfuseEngine: cfuseEngine(input.cfuseEngine),
    cfuseModel: modelName(input.cfuseModel, "cfuseModel"),
    llmApiKey: null,
  };
}

function assertAgentSelectionEcho(config: RepairTaskConfig, input: RepairAgentExecutionInput): void {
  if (hasInput(input.agentMode) && agentMode(input.agentMode) !== config.agentMode) {
    throw new RepairError(409, "repair_agent_config_mismatch", "agentMode 与 Task 已冻结选择不一致");
  }
  if (config.agentMode === "openclaw") {
    if (hasInput(input.cfuseEngine) || hasInput(input.cfuseModel)) {
      throw new RepairError(409, "repair_agent_config_mismatch", "请求不能切换 Task 已冻结的 Agent 配置");
    }
    if (input.llmUseDefault != null
      && inputBoolean(input.llmUseDefault, "llmUseDefault") !== config.llmUseDefault) {
      throw new RepairError(409, "repair_agent_config_mismatch", "llmUseDefault 与 Task 已冻结选择不一致");
    }
    if (hasInput(input.llmModel) && modelName(input.llmModel, "llmModel") !== config.llmModel) {
      throw new RepairError(409, "repair_agent_config_mismatch", "llmModel 与 Task 已冻结选择不一致");
    }
    const suppliedKey = optionalSecretText(input.llmApiKey, "llmApiKey", 8_192);
    if (suppliedKey != null && !config.openclawUsesCustomApiKey) {
      repairValidation("unexpected_llm_api_key", "Task 创建时未选择自定义 API Key，不能在续跑时切换");
    }
    return;
  }
  if (input.llmUseDefault != null || hasInput(input.llmModel) || hasInput(input.llmApiKey)) {
    throw new RepairError(409, "repair_agent_config_mismatch", "请求不能切换 Task 已冻结的 Agent 配置");
  }
  if (hasInput(input.cfuseEngine) && cfuseEngine(input.cfuseEngine) !== config.cfuseEngine) {
    throw new RepairError(409, "repair_agent_config_mismatch", "cfuseEngine 与 Task 已冻结选择不一致");
  }
  if (hasInput(input.cfuseModel) && modelName(input.cfuseModel, "cfuseModel") !== config.cfuseModel) {
    throw new RepairError(409, "repair_agent_config_mismatch", "cfuseModel 与 Task 已冻结选择不一致");
  }
}

function newExecutionApiKey(config: RepairTaskConfig, input: RepairAgentExecutionInput): string | null {
  const llmApiKey = optionalSecretText(input.llmApiKey, "llmApiKey", 8_192);
  if (config.agentMode !== "openclaw" || config.llmUseDefault) {
    if (llmApiKey != null) {
      return repairValidation("unexpected_llm_api_key", "当前 Task 的 Agent 配置不接受 llmApiKey");
    }
    return null;
  }
  if (!config.openclawUsesCustomApiKey) {
    if (llmApiKey != null) {
      return repairValidation("unexpected_llm_api_key", "Task 创建时未选择自定义 API Key，不能在续跑时切换");
    }
    return null;
  }
  if (llmApiKey == null) {
    return repairValidation("llm_api_key_required", "新 execution 必须重新提交本次使用的 llmApiKey");
  }
  return llmApiKey;
}

function environment(value: unknown): RepairTargetEnvironment {
  const normalized = requiredText(value, "targetEnvironment", 16).toLowerCase();
  if (normalized === "pre" || normalized === "prepub") return "pre";
  if (normalized === "prod" || normalized === "gray") return "prod";
  return repairValidation(
    "unsupported_target_environment",
    `Repair 当前仅支持 pre 和 prod Bot，当前运行环境为 ${normalized}`,
  );
}

function cfuseLoginUrl(value: unknown): string {
  const raw = requiredText(value, "loginUrl", 2_048);
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return repairValidation("invalid_cfuse_login_url", "cfuse loginUrl 格式不合法");
  }
  if (url.origin !== CFUSE_LOGIN_ORIGIN
    || url.pathname !== CFUSE_LOGIN_PATH
    || url.username !== ""
    || url.password !== ""
    || url.port !== ""
    || url.hash !== "") {
    return repairValidation("invalid_cfuse_login_url", "cfuse loginUrl 不在允许范围内");
  }
  const query = [...url.searchParams.entries()];
  if (query.length !== 1) {
    return repairValidation("invalid_cfuse_login_url", "cfuse loginUrl 必须只包含一个授权参数");
  }
  const [name, parameter] = query[0];
  const validPort = name === "port"
    && /^[1-9]\d{0,4}$/u.test(parameter)
    && Number(parameter) <= 65_535;
  const validIdentifier = name === "identifier"
    && /^[A-Za-z0-9_-]{1,128}$/u.test(parameter);
  if (!validPort && !validIdentifier) {
    return repairValidation("invalid_cfuse_login_url", "cfuse loginUrl 授权参数不合法");
  }
  return url.toString();
}

function cfuseAuthCode(value: unknown): string {
  const code = typeof value === "string" ? value.trim() : "";
  if (!code || code.length > 8_192 || /[\r\n\0]/u.test(code)) {
    return repairValidation("invalid_cfuse_auth_code", "cfuse authCode 必填且格式必须合法");
  }
  return code;
}

function cfuseLoginReportStatus(value: unknown): "succeeded" | "failed" {
  const status = requiredText(value, "status", 32).toLowerCase();
  if (status !== "succeeded" && status !== "failed") {
    return repairValidation("invalid_cfuse_login_status", "cfuse login status 必须是 succeeded 或 failed");
  }
  return status;
}

function issueOf(input: RepairCreateTaskInput): RepairIssueInput {
  const now = Math.floor(Date.now() / 1_000);
  const hasFrom = input.timeRange?.from != null;
  const hasTo = input.timeRange?.to != null;
  if (hasFrom !== hasTo) repairValidation("invalid_time_range", "timeRange.from/to 必须同时提供");
  const from = hasFrom ? Number(input.timeRange?.from) : now - 30 * 60;
  const to = hasTo ? Number(input.timeRange?.to) : now;
  if (!Number.isSafeInteger(from) || !Number.isSafeInteger(to) || from <= 0 || to <= from || to - from > 6 * 60 * 60) {
    repairValidation("invalid_time_range", "timeRange 必须是最长 6 小时的有效 unix 秒区间");
  }
  if (to > now + 5 * 60) repairValidation("invalid_time_range", "timeRange.to 不能明显晚于当前时间");
  const errorText = optionalMultilineText(input.errorText, "errorText", 2_000);
  return {
    symptom: redactText(requiredMultilineText(input.symptom, "symptom", 4_000), 4_000),
    traceId: optionalText(input.traceId, "traceId", 256),
    relatedTaskId: optionalText(input.relatedTaskId, "relatedTaskId", 256),
    errorText: errorText == null ? null : redactText(errorText, 2_000),
    timeRange: { from, to },
  };
}

function optionalPositiveInteger(value: unknown, field: string): number | null {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed <= 0) {
    return repairValidation("invalid_repair_input", `${field} 必须是正整数`);
  }
  return parsed;
}

function repairInsightSource(
  detail: ImprovementDetail,
  requestId: string,
  repairDirection: string | null,
  authorizationMode: "ONCE" | "PERSISTENT",
  authorizationGrantId?: number,
  adminOverride?: { operatorUserId: string; reason: string },
): RepairInsightSource {
  return {
    sourceType: "insight_improvement",
    improvementId: detail.improvementId,
    requestId,
    version: detail.version,
    title: detail.title,
    sourceBatchId: detail.batchId,
    evidenceCount: detail.evidenceCount,
    sessionIds: [...new Set(detail.evidence.map((item) => item.sessionId))],
    evidenceTaskRefs: detail.evidence.map((item) => ({
      sessionId: item.sessionId,
      taskIndex: item.taskIndex,
      ordinal: item.ordinal,
    })),
    repairDirection,
    authorizationMode,
    ...(authorizationGrantId == null ? {} : { authorizationGrantId }),
    ...(adminOverride ? { adminOverride } : {}),
  };
}

function authorizationScope(
  actorUserId: string,
  target: RepairTarget,
  executionMode: RepairExecutionMode = "OWNER",
): RepairAuthorizationScope {
  return {
    actorUserId,
    ownerId: target.ownerId,
    botId: target.botId,
    environment: target.environment,
    executionMode,
  };
}

function authorizationScopeDigest(scope: RepairAuthorizationScope): string {
  return sha256(JSON.stringify({
    actorUserId: scope.actorUserId,
    ownerId: scope.ownerId,
    botId: scope.botId,
    environment: scope.environment,
    executionMode: scope.executionMode ?? "OWNER",
  }));
}

function storedConfigDigest(configJson: string): string {
  return sha256(configJson);
}

function targetFingerprint(target: RepairTarget): string {
  return sha256(JSON.stringify({
    environment: target.environment,
    ownerId: target.ownerId,
    botId: target.botId,
    bindingId: target.bindingId,
    provider: target.provider,
    deviceId: target.deviceId,
    sandboxId: target.sandboxId ?? null,
    arcaInstanceId: target.arcaInstanceId ?? null,
  }));
}

function targetSnapshot(target: RepairTarget, version: number, reason: RepairRuntimeTargetSnapshot["reason"]): RepairRuntimeTargetSnapshot {
  return { version, fingerprint: targetFingerprint(target), target, reason };
}

function artifactsFor(taskId: string, stepId: string, phase: RepairPhase): RepairStepArtifacts {
  const prefix = `evolution/${taskId}/repair/${stepId}`;
  return repairArtifactsWithContentTypes(phase === "repair_plan"
    ? {
      plan: { objectKey: `${prefix}/plan.json` },
      markdown: { objectKey: `${prefix}/plan.md` },
      result: { objectKey: `${prefix}/plan-result.json` },
      checkpoint: { objectKey: `${prefix}/checkpoint.json` },
    }
    : {
      applyResult: { objectKey: `${prefix}/apply-result.json` },
      markdown: { objectKey: `${prefix}/apply.md` },
      result: { objectKey: `${prefix}/result.json` },
      checkpoint: { objectKey: `${prefix}/checkpoint.json` },
    });
}

function repairArtifactContentType(name: string): string {
  return name === "markdown"
    ? "text/markdown; charset=utf-8"
    : "application/json; charset=utf-8";
}

function repairArtifactsWithContentTypes(artifacts: RepairStepArtifacts): RepairStepArtifacts {
  return Object.fromEntries(Object.entries(artifacts).map(([name, artifact]) => [name, {
    ...artifact,
    contentType: artifact.contentType ?? repairArtifactContentType(name),
  }]));
}

function taskConfig(task: EvolveTaskRow): RepairTaskConfig {
  let parsed: unknown;
  try {
    parsed = JSON.parse(task.config_json);
  } catch {
    throw new RepairError(500, "invalid_repair_task_config", "Repair Task config_json 无法解析");
  }
  const parsedConfig = parsed as RepairTaskConfig;
  if (parsedConfig?.schemaVersion !== REPAIR_CONTRACT_VERSION
    || parsedConfig.taskId !== task.task_id
    || !parsedConfig.current
    || !parsedConfig.execution
    || !parsedConfig.authorizationScope
    || !parsedConfig.runtimeTarget) {
    throw new RepairError(500, "invalid_repair_task_config", "Repair Task config_json 契约不匹配");
  }
  if (parsedConfig.shared != null && typeof parsedConfig.shared !== "boolean") {
    throw new RepairError(500, "invalid_repair_task_config", "Repair Task shared 配置不合法");
  }
  if (parsedConfig.controlPlaneEnvironment != null
    && parsedConfig.controlPlaneEnvironment !== "pre"
    && parsedConfig.controlPlaneEnvironment !== "prod") {
    throw new RepairError(500, "invalid_repair_task_config", "Repair Task 控制面环境配置不合法");
  }
  const executionMode = parsedConfig.authorizationScope.executionMode ?? "OWNER";
  if (executionMode !== "OWNER" && executionMode !== "ADMIN_ONCE") {
    throw new RepairError(500, "invalid_repair_task_config", "Repair Task executionMode 配置不合法");
  }
  if (executionMode === "OWNER"
    && parsedConfig.authorizationScope.actorUserId !== parsedConfig.authorizationScope.ownerId) {
    throw new RepairError(500, "invalid_repair_task_config", "Owner Repair 授权范围不能跨用户");
  }
  const agentConfig: RepairTaskConfig = parsedConfig.agentMode == null
    ? {
      ...parsedConfig,
      agentMode: "openclaw",
      llmUseDefault: true,
      llmModel: null,
      openclawUsesCustomApiKey: false,
      cfuseEngine: null,
      cfuseModel: null,
    }
    : parsedConfig;
  const config: RepairTaskConfig = {
    ...agentConfig,
    shared: agentConfig.shared === true,
    diagnosticMode: diagnosticMode(agentConfig.diagnosticMode),
  };
  const validOpenClaw = config.agentMode === "openclaw"
    && typeof config.llmUseDefault === "boolean"
    && typeof config.openclawUsesCustomApiKey === "boolean"
    && config.cfuseEngine == null
    && config.cfuseModel == null
    && (config.llmUseDefault
      ? config.llmModel == null && !config.openclawUsesCustomApiKey
      : typeof config.llmModel === "string" && config.llmModel.length > 0 && config.llmModel.length <= 128);
  const validCfuse = config.agentMode === "cfuse"
    && config.llmUseDefault === true
    && config.llmModel == null
    && config.openclawUsesCustomApiKey === false
    && config.cfuseEngine != null
    && PERSISTED_CFUSE_ENGINES.has(config.cfuseEngine)
    && typeof config.cfuseModel === "string"
    && config.cfuseModel.length > 0
    && config.cfuseModel.length <= 128;
  if (!validOpenClaw && !validCfuse) {
    throw new RepairError(500, "invalid_repair_task_config", "Repair Task Agent 配置契约不匹配");
  }
  return config;
}

function parseOutput(step: EvolveStepRow): Record<string, unknown> {
  if (!step.output_json) return {};
  try {
    const parsed = JSON.parse(step.output_json) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

type RepairStepFailureMetadata = {
  schemaVersion: typeof REPAIR_STEP_FAILURE_VERSION;
  stage?: string;
  reason?: string;
  artifactName?: string;
  exitCode?: number;
  httpStatus?: number;
  providerCode?: string;
  providerRequestId?: string;
  retryCount?: number;
  field?: string;
  rule?: "han_required" | "chinese_dominance";
  retryBranch?: "not_allowed" | "already_consumed" | "session_missing" | "session_mismatch"
    | "output_invalid" | "semantic_mismatch" | "contract_invalid" | "still_non_chinese";
};

type RepairRecoveryProgress = {
  schemaVersion: typeof REPAIR_RECOVERY_PROGRESS_VERSION;
  kind: "result_finalization";
  mode: "agent" | "system_fallback";
  attempt: number;
  maxAttempts: number;
};

function repairRecoveryProgress(value: unknown): RepairRecoveryProgress | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const output = value as Record<string, unknown>;
  const raw = output.recovery;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const recovery = raw as Record<string, unknown>;
  if (recovery.schemaVersion !== REPAIR_RECOVERY_PROGRESS_VERSION
    || recovery.kind !== "result_finalization"
    || (recovery.mode !== "agent" && recovery.mode !== "system_fallback")
    || !Number.isSafeInteger(recovery.attempt)
    || !Number.isSafeInteger(recovery.maxAttempts)) return null;
  const attempt = Number(recovery.attempt);
  const maxAttempts = Number(recovery.maxAttempts);
  if (attempt < 1 || maxAttempts < 1 || maxAttempts > 3 || attempt > maxAttempts) return null;
  return {
    schemaVersion: REPAIR_RECOVERY_PROGRESS_VERSION,
    kind: "result_finalization",
    mode: recovery.mode,
    attempt,
    maxAttempts,
  };
}

const REPAIR_FAILURE_OPERATION_OUTCOMES = [
  "timeout",
  "spawn_failed",
  "authentication_failed",
  "authorization_failed",
  "request_timeout",
  "rate_limited",
  "upstream_unavailable",
  "request_rejected",
  "transport_failed",
  "nonzero_exit",
] as const;

function repairFailureOperationReasons(...operations: string[]): string[] {
  return operations.flatMap((operation) => REPAIR_FAILURE_OPERATION_OUTCOMES
    .map((outcome) => `${operation}_${outcome}`));
}

const REPAIR_FAILURE_STAGE_REASONS: ReadonlyMap<string, ReadonlySet<string>> = new Map([
  ["model_output_parse", new Set(["invalid_json_or_root", "format_retry_invalid"])],
  ["model_output_security", new Set(["credential_detected"])],
  ["model_output_correction", new Set(["locked_field_changed"])],
  ["plan_validation", new Set(["invalid_plan_shape"])],
  ["apply_validation", new Set(["invalid_apply_shape"])],
  ["user_facing_language_validation", new Set([
    "user_facing_chinese_required",
    "language_retry_invalid",
    "language_retry_semantic_mismatch",
  ])],
  ["artifact_upload", new Set([
    "http_rejected", "transport_failed", "refresh_failed", "refresh_invalid",
  ])],
  ["cfuse_preflight", new Set(["engine_help_failed"])],
  ["preflight", new Set([
    "model_config_missing",
    "version_mismatch",
    "required_flags_missing",
    "node_or_cli_check_failed",
    "cli_probe_failed",
    "openclaw_installer_missing",
    "openclaw_install_failed",
    "node_installer_missing",
    "node_install_failed",
    "node_binary_missing",
    "node_probe_failed",
    "node_version_invalid",
    "node_version_unsupported",
  ])],
  ["provider_config", new Set([
    "isolated_config_failed",
    "isolated_config_io_failed",
    "snapshot_config_missing",
    "isolated_config_invalid",
    "model_missing",
    ...repairFailureOperationReasons("provider_set"),
  ])],
  ["mcp_set", new Set(repairFailureOperationReasons("server_set"))],
  ["agents_add", new Set([
    "agent_not_persisted",
    "system_context_changed",
    ...repairFailureOperationReasons("agent_create"),
  ])],
  ["tool_policy", new Set([
    "agent_index_missing",
    ...repairFailureOperationReasons("skills_set", "allow_set", "deny_set"),
  ])],
  ["agents_list", new Set([
    "list_timeout",
    "list_spawn_failed",
    "list_invalid_json",
    "list_unexpected_shape",
  ])],
  ["agent_invoke", new Set([
    "message_file_failed",
    "spawn_failed",
    "timeout",
    "authentication_failed",
    "authorization_failed",
    "request_timeout",
    "rate_limited",
    "upstream_unavailable",
    "request_rejected",
    "transport_failed",
    "nonzero_exit",
  ])],
  ["response_parse", new Set([
    "invalid_json",
    "result_not_object",
    "payloads_missing",
    "agent_run_error",
    "final_text_missing",
    "final_text_ambiguous",
    "format_retry_session_missing",
    "format_retry_session_mismatch",
  ])],
  ["agent_closeout", new Set([
    "session_missing",
    "result_missing",
    "result_invalid",
    "closeout_failed",
    "session_mismatch",
    "timeout_exhausted",
  ])],
  ["agent_session", new Set(["resume_session_mismatch", "result_session_mismatch"])],
  ["unknown", new Set(["failed"])],
]);

const REPAIR_FAILURE_ARTIFACT_NAMES = new Set([
  "plan",
  "applyResult",
  "markdown",
  "result",
  "checkpoint",
]);

const REPAIR_FAILURE_LANGUAGE_RULES: ReadonlySet<string> = new Set([
  "han_required",
  "chinese_dominance",
]);

const REPAIR_FAILURE_LANGUAGE_RETRY_BRANCHES: ReadonlySet<string> = new Set([
  "not_allowed",
  "already_consumed",
  "session_missing",
  "session_mismatch",
  "output_invalid",
  "semantic_mismatch",
  "contract_invalid",
  "still_non_chinese",
]);

const REPAIR_FAILURE_LANGUAGE_FIELD = /^(?:diagnosis\.(?:facts|inferences|unknowns)\[\d{1,3}\]|recommendation\.(?:summary|reason|nextSteps\[\d{1,3}\])|actions\[\d{1,3}\]\.(?:summary|risk|verification|rollback|attempts\[\d{1,2}\]\.evidence\[\d{1,2}\]|verification\.evidence\[\d{1,2}\])|evidence\[\d{1,3}\]\.claim|summary|markdown\.line\[\d{1,6}\])$/u;

function failureToken(value: unknown, maxLength: number, pattern: RegExp): string | null {
  const text = typeof value === "string" ? value.trim() : "";
  return text && text.length <= maxLength && pattern.test(text) ? text : null;
}

function repairStepFailureMetadata(error: Record<string, unknown>): RepairStepFailureMetadata | null {
  if (containsRepairSecret({
    stage: error.stage,
    reason: error.reason,
    artifactName: error.artifactName,
    providerCode: error.providerCode,
    providerRequestId: error.providerRequestId,
    field: error.field,
    rule: error.rule,
    retryBranch: error.retryBranch,
  })) return null;
  const stage = failureToken(error.stage, 128, /^[a-z][a-z0-9_]*$/u);
  const reason = failureToken(error.reason, 128, /^[a-z][a-z0-9_]*$/u);
  if (stage == null || reason == null || !REPAIR_FAILURE_STAGE_REASONS.get(stage)?.has(reason)) return null;
  const hasArtifactName = error.artifactName != null && error.artifactName !== "";
  const artifactName = hasArtifactName
    ? failureToken(error.artifactName, 128, /^[A-Za-z][A-Za-z0-9_.-]*$/u)
    : null;
  if (hasArtifactName && (artifactName == null || !REPAIR_FAILURE_ARTIFACT_NAMES.has(artifactName))) return null;
  const exitCode = Number.isSafeInteger(error.exitCode) && Number(error.exitCode) >= -255
    && Number(error.exitCode) <= 255 ? Number(error.exitCode) : null;
  const httpStatus = Number.isSafeInteger(error.httpStatus) && Number(error.httpStatus) >= 100
    && Number(error.httpStatus) <= 599 ? Number(error.httpStatus) : null;
  const hasProviderCode = error.providerCode != null && error.providerCode !== "";
  const hasProviderRequestId = error.providerRequestId != null && error.providerRequestId !== "";
  const hasRetryCount = error.retryCount != null;
  if ((hasProviderCode || hasProviderRequestId || hasRetryCount) && stage !== "artifact_upload") return null;
  const providerCode = hasProviderCode
    ? failureToken(error.providerCode, 128, /^[A-Za-z][A-Za-z0-9._-]*$/u)
    : null;
  if (hasProviderCode && providerCode == null) return null;
  const providerRequestId = hasProviderRequestId
    ? failureToken(error.providerRequestId, 256, /^[A-Za-z0-9][A-Za-z0-9._:-]*$/u)
    : null;
  if (hasProviderRequestId && providerRequestId == null) return null;
  const retryCount = Number.isSafeInteger(error.retryCount)
    && Number(error.retryCount) >= 0 && Number(error.retryCount) <= 3
    ? Number(error.retryCount) : null;
  if (hasRetryCount && retryCount == null) return null;
  const hasField = error.field != null && error.field !== "";
  const hasRule = error.rule != null && error.rule !== "";
  const hasRetryBranch = error.retryBranch != null && error.retryBranch !== "";
  const hasLanguageDiagnostic = hasField || hasRule || hasRetryBranch;
  if (hasLanguageDiagnostic && stage !== "user_facing_language_validation") return null;
  const field = hasField ? failureToken(error.field, 256, REPAIR_FAILURE_LANGUAGE_FIELD) : null;
  if (hasField && field == null) return null;
  const rawRule = hasRule ? failureToken(error.rule, 64, /^[a-z][a-z_]*$/u) : null;
  const rule = rawRule != null && REPAIR_FAILURE_LANGUAGE_RULES.has(rawRule)
    ? rawRule as RepairStepFailureMetadata["rule"] : null;
  if (hasRule && rule == null) return null;
  const rawRetryBranch = hasRetryBranch
    ? failureToken(error.retryBranch, 64, /^[a-z][a-z_]*$/u)
    : null;
  const retryBranch = rawRetryBranch != null
    && REPAIR_FAILURE_LANGUAGE_RETRY_BRANCHES.has(rawRetryBranch)
    ? rawRetryBranch as RepairStepFailureMetadata["retryBranch"] : null;
  if (hasRetryBranch && retryBranch == null) return null;
  return {
    schemaVersion: REPAIR_STEP_FAILURE_VERSION,
    ...(stage == null ? {} : { stage }),
    ...(reason == null ? {} : { reason }),
    ...(artifactName == null ? {} : { artifactName }),
    ...(exitCode == null ? {} : { exitCode }),
    ...(httpStatus == null ? {} : { httpStatus }),
    ...(providerCode == null ? {} : { providerCode }),
    ...(providerRequestId == null ? {} : { providerRequestId }),
    ...(retryCount == null ? {} : { retryCount }),
    ...(field == null ? {} : { field }),
    ...(rule == null ? {} : { rule }),
    ...(retryBranch == null ? {} : { retryBranch }),
  };
}

function repairStepFailureCode(value: unknown): string {
  return failureToken(value, 128, /^[A-Z][A-Z0-9_]*$/u) ?? "REPAIR_EXECUTION_FAILED";
}

function executorOutput(value: unknown): RepairExecutorOutput {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    repairValidation("invalid_repair_executor_output", "output 必须是对象");
  }
  return value as RepairExecutorOutput;
}

function artifactDigest(value: unknown): string {
  const digest = typeof value === "string" ? value.trim().toLowerCase() : "";
  if (!/^[a-f0-9]{64}$/.test(digest)) {
    repairValidation("invalid_artifact_digest", "artifactDigest 必须是 SHA-256");
  }
  return digest;
}

function optionalArtifactDigest(value: unknown): string | null {
  if (value == null || value === "") return null;
  return artifactDigest(value);
}

function clientRequestId(value: unknown, fallback: string): string {
  if (value == null || value === "") return fallback;
  return requiredText(value, "clientRequestId", 128);
}

function boolean(value: unknown): boolean {
  return value === true;
}

function objectParams(value: unknown): Record<string, unknown> {
  if (value == null) return {};
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    repairValidation("invalid_ocb_operation_params", "OCB operation params 必须是对象");
  }
  const encoded = JSON.stringify(value);
  if (Buffer.byteLength(encoded, "utf8") > 256 * 1024) {
    repairValidation("invalid_ocb_operation_params", "OCB operation params 过大");
  }
  return value as Record<string, unknown>;
}

function assertStringArray(value: unknown, field: string): asserts value is string[] {
  if (!Array.isArray(value) || value.length > 200
    || value.some((item) => typeof item !== "string" || item.length > 4_000)) {
    repairValidation("invalid_repair_plan", `${field} 必须是受限字符串数组`);
  }
}

const TECHNICAL_ENGLISH_WORDS = new Set([
  "api", "http", "https", "json", "mcp", "ocb", "baas", "ais", "bot", "openclaw",
  "clawweb", "antlogs", "engine", "adapter", "session", "workspace", "cron", "apply",
  "plan", "repair", "sql", "ddl", "pid", "id",
]);

function nonTechnicalEnglishWordCount(value: string): number {
  const prose = value
    .replace(/`[^`\r\n]*`/g, " ")
    .replace(/https?:\/\/\S+/gi, " ")
    .replace(/(^|\s)\/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+/g, " ")
    .replace(/\b[A-Za-z0-9]+(?:[_:.=-][A-Za-z0-9]+)+\b/g, " ");
  return prose.match(/[A-Za-z][A-Za-z'-]*/g)?.filter((word) => {
    if (TECHNICAL_ENGLISH_WORDS.has(word.toLowerCase())) return false;
    if (word === word.toUpperCase()) return false;
    if (/[a-z][A-Z]/.test(word)) return false;
    return true;
  }).length ?? 0;
}

function assertChineseDominantFinalText(
  text: string,
  code: string,
  message: string,
): void {
  const hanCount = text.match(/[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/gu)?.length ?? 0;
  const englishWordCount = nonTechnicalEnglishWordCount(text);
  if (hanCount === 0 || (englishWordCount >= 3 && hanCount < englishWordCount)) {
    repairValidation(code, message);
  }
}

function assertPlanV2Chinese(plan: RepairPlanArtifactV2): void {
  const displayText = [
    ...plan.diagnosis.facts,
    ...plan.diagnosis.inferences,
    ...plan.diagnosis.unknowns,
    plan.recommendation.summary,
    plan.recommendation.reason,
    ...(plan.recommendation.nextSteps ?? []),
    ...plan.actions.flatMap((action) => [
      action.summary,
      action.risk,
      action.verification,
      ...(action.rollback == null ? [] : [action.rollback]),
    ]),
  ].join("\n");
  assertChineseDominantFinalText(
    displayText,
    "invalid_repair_plan_language",
    "Repair Plan 用户展示文案整体必须以中文为主体",
  );
}

function assertExecutablePayloadSecretFree(action: RepairPlanAction): void {
  const payload = action.type === "container_command"
    ? { command: action.command }
    : action.operation?.params ?? {};
  if (containsRepairSecret(payload)) {
    repairValidation("invalid_repair_plan_secret", "Repair Plan 的可执行内容不能包含凭据或密钥");
  }
}

function assertNoUnsafeRawProcessAction(action: RepairPlanAction): void {
  const command = action.type === "container_command" ? action.command ?? "" : "";
  const normalizedCommand = command.replace(SHELL_TOKEN_DECORATION, "");
  if (action.type === "container_command"
    && (RAW_PROCESS_SIGNAL_COMMAND.test(command) || RAW_PROCESS_SIGNAL_COMMAND.test(normalizedCommand))) {
    repairValidation(
      "unsafe_raw_process_action",
      "Repair Plan 不能用原始 shell 命令向进程发送信号；缺少实例身份绑定时必须重新规划",
    );
  }
}

function isRepairPlanV2(plan: RepairPlanArtifact): plan is RepairPlanArtifactV2 {
  return plan.schemaVersion === REPAIR_PLAN_VERSION;
}

function validatePlanBody(
  plan: RepairPlanArtifact,
  options: {
    allowHistoricalUnsafeProcessActions?: boolean;
    allowHistoricalEngineConfigReplace?: boolean;
    allowHistoricalUnvalidatedOcbOperationParams?: boolean;
  } = {},
): void {
  if (plan.schemaVersion !== REPAIR_PLAN_VERSION && plan.schemaVersion !== LEGACY_REPAIR_PLAN_VERSION) {
    repairValidation("invalid_repair_plan", "Repair Plan schemaVersion 不受支持");
  }
  if (isRepairPlanV2(plan)) {
    const allowed = new Set([
      "schemaVersion", "taskId", "stepId", "attempt", "authorizationScopeDigest",
      "runtimeTargetVersion", "diagnosis", "recommendation", "quality", "actions",
    ]);
    if (Object.keys(plan).some((key) => !allowed.has(key))) {
      repairValidation("invalid_repair_plan", "Repair Plan v2 含有不支持的顶层字段");
    }
  }
  requiredText(plan.taskId, "taskId", 256);
  requiredText(plan.stepId, "stepId", 256);
  requiredText(plan.authorizationScopeDigest, "authorizationScopeDigest", 128);
  if (!Number.isSafeInteger(plan.attempt) || plan.attempt <= 0
    || !Number.isSafeInteger(plan.runtimeTargetVersion) || plan.runtimeTargetVersion <= 0) {
    repairValidation("invalid_repair_plan", "Repair Plan attempt 或 runtimeTargetVersion 不合法");
  }
  if (!plan.diagnosis || typeof plan.diagnosis !== "object" || Array.isArray(plan.diagnosis)
    || Object.keys(plan.diagnosis).some((key) => !new Set(["facts", "inferences", "unknowns"]).has(key))) {
    repairValidation("invalid_repair_plan", "diagnosis 必须只包含 facts/inferences/unknowns");
  }
  assertStringArray(plan.diagnosis.facts, "diagnosis.facts");
  assertStringArray(plan.diagnosis?.inferences, "diagnosis.inferences");
  assertStringArray(plan.diagnosis?.unknowns, "diagnosis.unknowns");
  if (!Array.isArray(plan.actions) || plan.actions.length > 100) {
    repairValidation("invalid_repair_plan", "actions 必须是最多 100 项的数组");
  }
  if (isRepairPlanV2(plan)) {
    const recommendation = plan.recommendation;
    if (!recommendation || !REPAIR_PLAN_DISPOSITIONS.has(recommendation.disposition)) {
      repairValidation("invalid_repair_plan", "recommendation.disposition 不合法");
    }
    if (!REPAIR_PLAN_QUALITIES.has(plan.quality)) {
      repairValidation("invalid_repair_plan", "quality 不合法");
    }
    requiredText(recommendation.summary, "recommendation.summary", 4_000);
    requiredText(recommendation.reason, "recommendation.reason", 4_000);
    if (recommendation.nextSteps != null) {
      assertStringArray(recommendation.nextSteps, "recommendation.nextSteps");
      recommendation.nextSteps.forEach((item, index) => {
        requiredText(item, `recommendation.nextSteps[${index}]`, 4_000);
      });
    }
    const hasActions = plan.actions.length > 0;
    if ((recommendation.disposition === "execute_actions") !== hasActions) {
      repairValidation("invalid_repair_plan", "execute_actions 必须包含操作，其他方案结论不得包含操作");
    }
    const hasInsufficientQuality = plan.quality === "blocked" || plan.quality === "unknown";
    if ((recommendation.disposition === "insufficient_evidence") !== hasInsufficientQuality) {
      repairValidation(
        "invalid_repair_plan",
        "insufficient_evidence 必须且只能使用 blocked 或 unknown 质量",
      );
    }
  }
  const ids = new Set<string>();
  for (const action of plan.actions) {
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(action?.actionId ?? "") || ids.has(action.actionId)) {
      repairValidation("invalid_repair_plan", "actionId 必须合法且唯一");
    }
    ids.add(action.actionId);
    requiredText(action.summary, "action.summary", 4_000);
    requiredText(action.risk, "action.risk", 4_000);
    requiredText(action.verification, "action.verification", 4_000);
    if (action.rollback != null) requiredText(action.rollback, "action.rollback", 4_000);
    if (action.type === "container_command") {
      requiredText(action.command, "action.command", 16_384);
      if (action.operation != null) repairValidation("invalid_repair_plan", "container action 不能包含 OCB operation");
    } else if (action.type === "ocb_operation") {
      const operation = requiredText(action.operation?.type, "action.operation.type", 64);
      const allowedOperations = options.allowHistoricalEngineConfigReplace
        ? HISTORICAL_WRITE_OCB_OPERATIONS
        : WRITE_OCB_OPERATIONS;
      if (!allowedOperations.has(operation)) {
        if (operation === "engine_config_replace") {
          repairValidation(
            "unsafe_engine_config_replace",
            "Repair 不再通过 OCB 修改引擎配置；请先确认目标容器中的真实配置接口，再提交受审计的 container_command",
          );
        }
        repairValidation("invalid_repair_plan", `不支持的 OCB 写操作: ${operation}`);
      }
      if (operation === "engine_config_replace" || options.allowHistoricalUnvalidatedOcbOperationParams) {
        objectParams(action.operation?.params);
      } else {
        parseOcbRepairOperation(action.operation);
      }
      if (action.command != null) repairValidation("invalid_repair_plan", "OCB action 不能包含 shell command");
    } else {
      repairValidation("invalid_repair_plan", "action.type 不受支持");
    }
    if (!options.allowHistoricalUnsafeProcessActions) assertNoUnsafeRawProcessAction(action);
    assertExecutablePayloadSecretFree(action);
  }
  for (const action of plan.actions) {
    for (const dependency of action.dependsOn ?? []) {
      if (!ids.has(dependency) || dependency === action.actionId) {
        repairValidation("invalid_repair_plan", `action ${action.actionId} 的 dependsOn 不合法`);
      }
    }
    if (action.rollbackActionId != null
      && (!ids.has(action.rollbackActionId) || action.rollbackActionId === action.actionId)) {
      repairValidation("invalid_repair_plan", `action ${action.actionId} 的 rollbackActionId 不合法`);
    }
  }
  if (containsRepairSecret(plan)) {
    repairValidation("invalid_repair_plan_secret", "Repair Plan 不能包含凭据或签名链接");
  }
  if (isRepairPlanV2(plan)) assertPlanV2Chinese(plan);
}

type RepairApplyAttempt = {
  status: "succeeded" | "failed" | "unknown";
  toolCallId: string;
  evidence: string[];
};

type RepairApplyActionResult = {
  actionId: string;
  status: "succeeded" | "failed" | "skipped" | "blocked" | "unknown";
  attempts: RepairApplyAttempt[];
  verification: {
    status: "verified" | "partially_verified" | "failed" | "blocked" | "unknown";
    evidence: string[];
  };
};

type RepairApplyResultArtifact = {
  schemaVersion: typeof REPAIR_APPLY_RESULT_VERSION;
  taskId: string;
  stepId: string;
  attempt: number;
  actions: RepairApplyActionResult[];
  verdict: "verified" | "partially_verified" | "failed" | "blocked" | "unknown";
  evidence: Array<{ source: string; claim: string }>;
  summary: string;
};

function applyObject(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    repairValidation("invalid_repair_apply_result", `${field} 必须是对象`);
  }
  return value as Record<string, unknown>;
}

function assertApplyKeys(value: Record<string, unknown>, allowed: readonly string[], field: string): void {
  const allowedKeys = new Set(allowed);
  if (Object.keys(value).some((key) => !allowedKeys.has(key))) {
    repairValidation("invalid_repair_apply_result", `${field} 含有不支持的字段`);
  }
}

function applyText(value: unknown, field: string, maxLength: number): string {
  const text = typeof value === "string" ? value.trim() : "";
  if (!text || text.length > maxLength) {
    repairValidation("invalid_repair_apply_result", `${field} 缺失或过长`);
  }
  return text;
}

function applyStringArray(
  value: unknown,
  field: string,
  maximumItems: number,
  maximumLength: number,
): string[] {
  if (!Array.isArray(value) || value.length > maximumItems) {
    repairValidation("invalid_repair_apply_result", `${field} 必须是受限字符串数组`);
  }
  return value.map((item, index) => applyText(item, `${field}[${index}]`, maximumLength));
}

function expectedApplyVerdict(actions: RepairApplyActionResult[]): RepairApplyResultArtifact["verdict"] {
  const actionStatuses = new Set(actions.map((action) => action.status));
  const verificationStatuses = new Set(actions.map((action) => action.verification.status));
  if (actionStatuses.has("failed") || verificationStatuses.has("failed")) return "failed";
  if (actionStatuses.has("unknown") || verificationStatuses.has("unknown")) return "unknown";
  if (actionStatuses.has("blocked") || verificationStatuses.has("blocked")) return "blocked";
  if (verificationStatuses.has("partially_verified")) return "partially_verified";
  return "verified";
}

function validateApplyResultBody(
  value: unknown,
  config: RepairTaskConfig,
  approvedPlan: RepairPlanArtifact,
): RepairApplyResultArtifact {
  const result = applyObject(value, "Apply 结果");
  assertApplyKeys(result, [
    "schemaVersion", "taskId", "stepId", "attempt", "actions", "verdict", "evidence", "summary",
  ], "Apply 结果");
  if (result.schemaVersion !== REPAIR_APPLY_RESULT_VERSION
    || result.taskId !== config.taskId
    || result.stepId !== config.current.stepId
    || result.attempt !== config.current.attempt) {
    repairValidation("repair_apply_result_identity_mismatch", "Apply 结果与当前 Task/Step/attempt 不匹配");
  }
  if (!Array.isArray(result.actions) || result.actions.length === 0 || result.actions.length > 100) {
    repairValidation("invalid_repair_apply_result", "Apply actions 必须是非空受限数组");
  }
  const approvedActionIds = new Set(approvedPlan.actions.map((action) => action.actionId));
  const seenActionIds = new Set<string>();
  const seenToolCallIds = new Set<string>();
  let attemptCount = 0;
  const actions = result.actions.map((rawAction, actionIndex): RepairApplyActionResult => {
    const action = applyObject(rawAction, `actions[${actionIndex}]`);
    assertApplyKeys(action, ["actionId", "status", "attempts", "verification"], `actions[${actionIndex}]`);
    const actionId = applyText(action.actionId, `actions[${actionIndex}].actionId`, 128);
    if (!/^[A-Za-z0-9_-]{1,128}$/.test(actionId) || seenActionIds.has(actionId)) {
      repairValidation("invalid_repair_apply_result", "Apply actionId 必须合法且唯一");
    }
    seenActionIds.add(actionId);
    if (!REPAIR_APPLY_ACTION_STATUSES.has(action.status as never)) {
      repairValidation("invalid_repair_apply_result", "Apply action.status 不合法");
    }
    const status = action.status as RepairApplyActionResult["status"];
    if (!Array.isArray(action.attempts) || action.attempts.length > 50) {
      repairValidation("invalid_repair_apply_result", "Apply action.attempts 必须是受限数组");
    }
    attemptCount += action.attempts.length;
    if (attemptCount > MAX_APPLY_ATTEMPTS) {
      repairValidation("invalid_repair_apply_result", `Apply attempts 总数不能超过 ${MAX_APPLY_ATTEMPTS}`);
    }
    const attempts = action.attempts.map((rawAttempt, attemptIndex): RepairApplyAttempt => {
      const attempt = applyObject(rawAttempt, `actions[${actionIndex}].attempts[${attemptIndex}]`);
      assertApplyKeys(attempt, ["status", "toolCallId", "evidence"], `actions[${actionIndex}].attempts[${attemptIndex}]`);
      if (!REPAIR_APPLY_ATTEMPT_STATUSES.has(attempt.status as never)) {
        repairValidation("invalid_repair_apply_result", "Apply attempt.status 不合法");
      }
      const toolCallId = applyText(
        attempt.toolCallId,
        `actions[${actionIndex}].attempts[${attemptIndex}].toolCallId`,
        64,
      );
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(toolCallId) || seenToolCallIds.has(toolCallId)) {
        repairValidation("invalid_repair_apply_result", "Apply attempt.toolCallId 必须合法且不能重复");
      }
      seenToolCallIds.add(toolCallId);
      return {
        status: attempt.status as RepairApplyAttempt["status"],
        toolCallId,
        evidence: applyStringArray(
          attempt.evidence,
          `actions[${actionIndex}].attempts[${attemptIndex}].evidence`,
          50,
          4_000,
        ),
      };
    });
    if (status === "succeeded" || status === "failed" || status === "unknown") {
      if (!attempts.length || attempts.at(-1)?.status !== status) {
        repairValidation("invalid_repair_apply_result", "已执行 action 的最后一次 attempt 必须与 action.status 一致");
      }
    } else if (attempts.length) {
      repairValidation("invalid_repair_apply_result", "skipped 或 blocked action 不能包含 attempts");
    }
    const rawVerification = applyObject(action.verification, `actions[${actionIndex}].verification`);
    assertApplyKeys(rawVerification, ["status", "evidence"], `actions[${actionIndex}].verification`);
    if (!REPAIR_APPLY_VERIFICATION_STATUSES.has(rawVerification.status as never)) {
      repairValidation("invalid_repair_apply_result", "Apply verification.status 不合法");
    }
    const verificationEvidence = applyStringArray(
      rawVerification.evidence,
      `actions[${actionIndex}].verification.evidence`,
      50,
      4_000,
    );
    if (rawVerification.status !== "unknown" && verificationEvidence.length === 0) {
      repairValidation("invalid_repair_apply_result", "终态 verification 必须包含证据");
    }
    return {
      actionId,
      status,
      attempts,
      verification: {
        status: rawVerification.status as RepairApplyActionResult["verification"]["status"],
        evidence: verificationEvidence,
      },
    };
  });
  if (seenActionIds.size !== approvedActionIds.size
    || [...seenActionIds].some((actionId) => !approvedActionIds.has(actionId))) {
    repairValidation("repair_apply_action_set_mismatch", "Apply 结果必须逐项覆盖获批方案的全部 action");
  }
  if (!REPAIR_APPLY_VERDICTS.has(result.verdict as never)) {
    repairValidation("invalid_repair_apply_result", "Apply verdict 不合法");
  }
  const verdict = result.verdict as RepairApplyResultArtifact["verdict"];
  if (verdict !== expectedApplyVerdict(actions)) {
    repairValidation("invalid_repair_apply_result", "Apply verdict 与 action/verification 状态不一致");
  }
  if (!Array.isArray(result.evidence) || result.evidence.length > 200) {
    repairValidation("invalid_repair_apply_result", "Apply evidence 必须是受限数组");
  }
  const evidence = result.evidence.map((rawEvidence, index) => {
    const item = applyObject(rawEvidence, `evidence[${index}]`);
    assertApplyKeys(item, ["source", "claim"], `evidence[${index}]`);
    return {
      source: applyText(item.source, `evidence[${index}].source`, 256),
      claim: applyText(item.claim, `evidence[${index}].claim`, 4_000),
    };
  });
  const summary = applyText(result.summary, "summary", 2_000);
  const displayText = [
    summary,
    ...evidence.map((item) => item.claim),
    ...actions.flatMap((action) => [
      ...action.attempts.flatMap((attempt) => attempt.evidence),
      ...action.verification.evidence,
    ]),
  ].join("\n");
  assertChineseDominantFinalText(
    displayText,
    "invalid_repair_apply_result_language",
    "Repair Apply 用户展示文案整体必须以中文为主体",
  );
  if (containsRepairSecret({ actions, evidence, summary })) {
    repairValidation("invalid_repair_apply_result_secret", "Apply 结果不能包含凭据或签名链接");
  }
  return {
    schemaVersion: REPAIR_APPLY_RESULT_VERSION,
    taskId: config.taskId,
    stepId: config.current.stepId,
    attempt: config.current.attempt,
    actions,
    verdict,
    evidence,
    summary,
  };
}

function assertApplyResultMatchesLedger(
  result: RepairApplyResultArtifact,
  config: RepairTaskConfig,
  ledger: readonly RepairToolCall[],
): void {
  const calls = ledger.filter((call) => call.isWrite).sort((left, right) => left.id - right.id);
  if (calls.length > MAX_APPLY_ATTEMPTS) {
    repairValidation("invalid_repair_apply_result", "当前 Apply Step 的写操作审计超过 500 条，拒绝收口");
  }
  const attemptRefs = result.actions.flatMap((action) =>
    action.attempts.map((attempt) => ({ actionId: action.actionId, attempt })));
  const referencedCallIds = new Set(attemptRefs.map(({ attempt }) => attempt.toolCallId));
  if (calls.length !== referencedCallIds.size
    || calls.some((call) => !referencedCallIds.has(call.callId))) {
    repairFinalizationRejected(
      "repair_apply_evidence_incomplete",
      "Apply 结果必须逐项引用当前 Step 的全部写操作审计，不能遗漏失败或未知尝试",
    );
  }
  for (const action of result.actions) {
    const ledgerIds = calls.filter((call) => call.actionId === action.actionId).map((call) => call.callId);
    const reportedIds = action.attempts.map((attempt) => attempt.toolCallId);
    if (ledgerIds.length !== reportedIds.length
      || ledgerIds.some((callId, index) => callId !== reportedIds[index])) {
      repairFinalizationRejected(
        "repair_apply_evidence_order_mismatch",
        "Apply 结果中的 attempts 必须按真实写操作审计顺序逐项记录",
      );
    }
  }
  const callsById = new Map(calls.map((call) => [call.callId, call]));
  for (const { actionId, attempt } of attemptRefs) {
    const call = callsById.get(attempt.toolCallId);
    if (!call
      || call.taskId !== config.taskId
      || call.stepId !== config.current.stepId
      || call.executionId !== config.execution.executionId
      || call.authorizationScopeDigest !== config.authorizationScopeDigest
      || call.actionId !== actionId
      || !["baas_write", "arca_write", "ocb_write"].includes(call.toolName)
      || repairApplyAttemptStatus(call.status) !== attempt.status) {
      repairFinalizationRejected(
        "repair_apply_evidence_mismatch",
        "Apply 结果引用的写操作审计与当前 Step/action/执行状态不匹配",
      );
    }
  }
}

function repairApplyAttemptStatus(status: string): "succeeded" | "failed" | "unknown" {
  if (status === "succeeded" || status === "failed") return status;
  return "unknown";
}

function validatePlan(plan: RepairPlanArtifact, expected: RepairTaskConfig): void {
  validatePlanBody(plan);
  if (plan.taskId !== expected.taskId
    || plan.stepId !== expected.current.stepId
    || plan.attempt !== expected.current.attempt
    || plan.authorizationScopeDigest !== expected.authorizationScopeDigest
    || plan.runtimeTargetVersion !== expected.runtimeTarget.version) {
    repairValidation("repair_plan_identity_mismatch", "Repair Plan 与当前 Task/Step/授权范围/目标版本不匹配");
  }
}

function safeTaskEnvelope(
  task: RepairTaskConfig & { llmApiKey?: string },
  uploadArtifacts: Record<string, unknown>,
  executionTicket?: string,
): Record<string, unknown> {
  const agent = task.agentMode === "openclaw"
    ? {
      openclaw: {
        useDefaultModelConfig: task.llmUseDefault,
        model: task.llmModel,
        ...(task.llmApiKey ? { modelApiKey: task.llmApiKey } : {}),
      },
    }
    : {
      cfuse: {
        engine: task.cfuseEngine,
        model: task.cfuseModel,
      },
    };
  return {
    schemaVersion: "clawevolve-task/v1",
    taskType: "repair",
    taskId: task.taskId,
    stepId: task.current.stepId,
    attempt: task.current.attempt,
    execution: {
      executor: "ais",
      executionId: task.execution.executionId,
      action: task.current.phase,
      agentMode: task.agentMode,
      resumeSessionId: task.execution.ccSessionId,
    },
    input: {
      issue: task.issue,
      agent,
      authorizationScope: task.authorizationScope,
      authorizationScopeDigest: task.authorizationScopeDigest,
      target: task.runtimeTarget.target,
      runtimeTargetVersion: task.runtimeTarget.version,
      history: agentVisibleHistory(task),
      investigationRequirements: currentInvestigationRequirements(task),
      ...(task.approvedPlan ? { approvedPlan: task.approvedPlan } : {}),
      ...(task.pendingDecision?.feedback ? { feedback: task.pendingDecision.feedback } : {}),
    },
    runtime: {
      clawwebUrl: task.publicBaseUrl,
      toolsBaseUrl: `${task.publicBaseUrl}/api/repair/v1/internal/tasks/${encodeURIComponent(task.taskId)}/steps/${encodeURIComponent(task.current.stepId)}`,
      ...(executionTicket ? { executionTicket } : {}),
      artifacts: uploadArtifacts,
      timings: {
        decisionGraceSeconds: task.execution.decisionDeadlineAt == null ? null : Math.max(0, task.execution.decisionDeadlineAt - Math.floor(Date.now() / 1_000)),
      },
    },
  };
}

function currentInvestigationRequirements(
  task: Pick<RepairTaskConfig, "current" | "history">,
): RepairInvestigationRequirement[] {
  if (task.current.phase !== "repair_plan") return [];
  const source = [...task.history].reverse().find((item) => Boolean(item.feedback?.trim()));
  if (!source?.feedback) return [];
  return [{
    requirementId: `user-feedback:${source.stepId}`,
    source: "user_feedback",
    text: source.feedback,
    introducedBy: {
      stepId: source.stepId,
      stepNo: source.stepNo,
      attempt: source.attempt,
      phase: source.phase,
    },
  }];
}

function agentVisibleHistory(
  task: Pick<RepairTaskConfig, "history">,
): RepairHistoryItem[] {
  return task.history.filter(item => item.status !== "failed");
}

function definition(config: RepairConfig): AisTaskDefinition<RepairDispatchConfig> {
  return {
    taskTypes: ["repair"],
    snapshotId: task => config.aisSnapshotIds[
      resolveRepairTaskControlPlaneEnvironment(task, config.controlPlaneEnvironment)
    ],
    artifactTransport: "signed_put",
    dispatchMetadata: task => ({
      taskId: task.taskId,
      stepId: task.current.stepId,
      taskType: "repair",
      action: task.current.phase,
      attempt: task.current.attempt,
      authorizationScopeDigest: task.authorizationScopeDigest,
      runtimeTargetVersion: task.runtimeTarget.version,
      controlPlaneEnvironment: resolveRepairTaskControlPlaneEnvironment(
        task,
        config.controlPlaneEnvironment,
      ),
      executionId: task.execution.executionId,
    }),
    buildGlobalParams: (task, uploadArtifacts) => ({
      [REPAIR_PARAMS_KEY]: JSON.stringify(safeTaskEnvelope(task, uploadArtifacts, task.executionTicket)),
    }),
  };
}

type RepairToolRequestView = {
  payload: unknown;
  targetVersion: number | null;
  purpose: string | null;
  semanticConclusionRequired: boolean;
};

type BrowserToolConclusion = {
  recordToolCallId: string;
  text: string;
  nextAction: string | null;
  evidenceToolCallIds: string[];
};

const BUSINESS_AUDIT_TOOLS = new Set([
  "antlogs", "baas_read", "baas_write", "arca_read", "arca_write", "ocb_read", "ocb_write",
]);
const OWNER_BROWSER_RELAY_TOOLS = new Set(["arca_read", "arca_write", "ocb_read", "ocb_write"]);

function requiresOwnerBrowserRelay(call: RepairToolCall): boolean {
  return call.status === "pending" && OWNER_BROWSER_RELAY_TOOLS.has(call.toolName);
}

function defaultToolPurpose(toolName: string, operation: string): string {
  const purposes: Record<string, string> = {
    "repair_control:bootstrap": "载入本次 Repair 的目标、历史记录和可用工具",
    "repair_control:decision_claim": "领取用户决策并继续下一步骤",
    "repair_control:record_conclusion": "记录本次工具调用的证据结论",
    "step_report:running": "记录当前步骤已开始运行",
    "step_report:succeeded": "记录当前步骤已成功完成",
    "step_report:failed": "记录当前步骤执行失败",
    "step_report:waiting_context": "记录当前步骤因等待上下文而暂停",
    "antlogs:search": "查询相关日志并补充故障证据",
    "baas_read:fs_list": "查看 Bot 容器中的目录内容",
    "baas_read:fs_find": "在 Bot 容器中查找文件",
    "baas_read:fs_stat": "查看 Bot 容器中的文件或目录信息",
    "baas_read:fs_read": "读取 Bot 容器中的文件片段",
    "baas_read:fs_search": "在 Bot 容器文件中搜索文本",
    "baas_read:process_list": "查看 Bot 容器中的运行进程",
    "baas_read:port_list": "查看 Bot 容器中的监听端口",
    "baas_read:http_get": "检查 Bot 容器内的本地服务是否可访问",
    "baas_read:shell_exec": "在用户授权范围内执行深度诊断命令",
    "arca_read:fs_list": "查看 Bot 容器中的目录内容",
    "arca_read:fs_find": "在 Bot 容器中查找文件",
    "arca_read:fs_stat": "查看 Bot 容器中的文件或目录信息",
    "arca_read:fs_read": "读取 Bot 容器中的文件片段",
    "arca_read:fs_search": "在 Bot 容器文件中搜索文本",
    "arca_read:process_list": "查看 Bot 容器中的运行进程",
    "arca_read:port_list": "查看 Bot 容器中的监听端口",
    "arca_read:http_get": "检查 Bot 容器内的本地服务是否可访问",
    "arca_read:shell_exec": "在用户授权范围内执行深度诊断命令",
    "ocb_read:current_target": "确认当前 Bot 的运行目标",
    "ocb_read:engine_config_read": "读取当前 Bot 的引擎配置",
    "ocb_read:identity_file_read": "读取 Bot 的身份与行为配置文件",
    "cfuse_login:authorize": "等待用户完成 CodeFuse 登录授权",
  };
  if (purposes[`${toolName}:${operation}`]) return purposes[`${toolName}:${operation}`];
  if (toolName === "baas_write" || toolName === "arca_write" || toolName === "ocb_write") {
    return "执行已批准的修复动作";
  }
  if (toolName === "repair_control") return "处理 Repair 流程控制";
  if (toolName === "step_report") return "更新 Repair 步骤状态";
  return "执行一项受控工具调用";
}

function persistedAuditText(value: unknown, maxLength: number): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (text.length < 4 || text.length > maxLength || /[\r\n\0]/u.test(text)
    || containsRepairSecret(text)) return null;
  return text;
}

function persistableSingleLineError(value: unknown, fallback: string, maxLength = 4_000): string {
  const normalized = redactPersistableText(value, maxLength)
    .replace(/\s+/gu, " ")
    .trim();
  return (normalized || fallback).slice(0, maxLength);
}

function toolRequestEnvelope(
  payload: unknown,
  runtimeTargetVersion: number,
  purpose: string,
  semanticConclusionRequired: boolean,
): Record<string, unknown> {
  return {
    schemaVersion: REPAIR_TOOL_REQUEST_SCHEMA_VERSION,
    runtimeTargetVersion,
    purpose,
    ...(semanticConclusionRequired ? { semanticConclusionRequired: true } : {}),
    payload,
  };
}

function unpackToolRequest(request: unknown): RepairToolRequestView {
  if (!request || typeof request !== "object" || Array.isArray(request)) {
    return { payload: request, targetVersion: null, purpose: null, semanticConclusionRequired: false };
  }
  const envelope = request as Record<string, unknown>;
  if (envelope.schemaVersion !== REPAIR_TOOL_REQUEST_SCHEMA_VERSION
    || !Number.isSafeInteger(envelope.runtimeTargetVersion)
    || Number(envelope.runtimeTargetVersion) < 1
    || !("payload" in envelope)) {
    return { payload: request, targetVersion: null, purpose: null, semanticConclusionRequired: false };
  }
  return {
    payload: envelope.payload,
    targetVersion: Number(envelope.runtimeTargetVersion),
    purpose: persistedAuditText(envelope.purpose, 200),
    semanticConclusionRequired: envelope.semanticConclusionRequired === true,
  };
}

function publicToolCall(call: RepairToolCall): Record<string, unknown> {
  const request = unpackToolRequest(call.request);
  return {
    toolCallId: call.callId,
    stepId: call.stepId,
    toolName: call.toolName,
    operation: call.operation,
    actionId: call.actionId,
    targetVersion: request.targetVersion,
    purpose: request.purpose ?? defaultToolPurpose(call.toolName, call.operation),
    status: call.status,
    request: request.payload,
    result: call.result,
    error: call.errorCode || call.errorMessage ? { code: call.errorCode, message: call.errorMessage } : null,
    deadlineAt: call.deadlineAt,
    createdAt: call.gmtCreate,
    updatedAt: call.gmtModified,
    requiresBrowserRelay: requiresOwnerBrowserRelay(call),
  };
}

function browserExecutionTarget(call: RepairToolCall, payload: unknown, canViewDetails: boolean): string {
  if (!canViewDetails) {
    if (call.toolName === "antlogs") return "授权范围内的 AgentClaw 后端日志";
    if (["baas_read", "baas_write", "arca_read", "arca_write"].includes(call.toolName)) {
      return "当前 Bot 的运行容器";
    }
    if (call.toolName === "ocb_read" || call.toolName === "ocb_write") return "当前 Bot 的 OCB 控制面";
    if (call.toolName === "cfuse_login") return "本次 Repair 的 CodeFuse 会话";
    return "本次 Repair 的中央控制记录";
  }
  const request = payload && typeof payload === "object" && !Array.isArray(payload)
    ? payload as Record<string, unknown> : {};
  if (call.toolName === "antlogs") {
    const identifiers = Array.isArray(request.identifiers)
      ? request.identifiers.filter((item): item is string => typeof item === "string").slice(0, 5)
      : [];
    return identifiers.length
      ? `AgentClaw 后端日志（按 ${identifiers.join("、")} 检索）`
      : "授权范围内的 AgentClaw 后端日志";
  }
  if (call.toolName === "baas_read" || call.toolName === "arca_read") {
    if (call.operation === "shell_exec") return "当前 Bot 的运行容器（深度诊断 Shell）";
    const path = typeof request.path === "string" ? redactText(request.path, 1_024) : null;
    if (call.operation === "http_get") {
      const port = Number.isSafeInteger(request.port) ? Number(request.port) : null;
      return port != null && path ? `Bot 容器本地服务 127.0.0.1:${port}${path}` : "Bot 容器本地服务";
    }
    if (call.operation === "process_list") {
      const pattern = typeof request.pattern === "string" ? redactText(request.pattern, 256) : null;
      return pattern ? `Bot 容器进程（筛选：${pattern}）` : "Bot 容器全部进程";
    }
    if (call.operation === "port_list") return "Bot 容器监听端口";
    return path ? `Bot 容器路径 ${path}` : "当前 Bot 的运行容器";
  }
  if (call.toolName === "ocb_read") {
    const params = request.params && typeof request.params === "object" && !Array.isArray(request.params)
      ? request.params as Record<string, unknown> : {};
    const fileType = typeof params.fileType === "string" ? redactText(params.fileType, 128) : null;
    const engineType = typeof params.engineType === "string" ? redactText(params.engineType, 128) : null;
    if (fileType) return `OCB 身份文件 ${fileType}${engineType ? `（${engineType}）` : ""}`;
    if (engineType) return `OCB 引擎配置（${engineType}）`;
    return call.operation === "current_target" ? "OCB 当前运行目标" : "当前 Bot 的 OCB 控制面";
  }
  if (call.toolName === "baas_write" || call.toolName === "arca_write" || call.toolName === "ocb_write") {
    return call.actionId ? `获批方案动作 ${redactText(call.actionId, 128)}` : "获批方案中的修复动作";
  }
  if (call.toolName === "cfuse_login") return "本次 Repair 的 CodeFuse 会话";
  return "本次 Repair 的中央控制记录";
}

function safeReadInvocation(call: RepairToolCall, payload: unknown): Record<string, unknown> | null {
  if (call.isWrite || containsRepairSecret(payload)) return null;
  if (call.toolName === "baas_read" || call.toolName === "arca_read") {
    if (call.operation === "shell_exec"
      && payload && typeof payload === "object" && !Array.isArray(payload)
      && typeof (payload as Record<string, unknown>).command === "string") {
      return {
        kind: "diagnostic_command",
        command: redactText(String((payload as Record<string, unknown>).command), 16_384),
      };
    }
    try {
      return {
        kind: "readonly_command",
        command: buildRepairRuntimeCommand(payload as RepairRuntimeInspectInput),
      };
    } catch {
      return null;
    }
  }
  return null;
}

function resultObject(call: RepairToolCall): Record<string, unknown> {
  return call.result && typeof call.result === "object" && !Array.isArray(call.result)
    ? call.result as Record<string, unknown> : {};
}

function browserToolStatus(call: RepairToolCall): RepairToolCall["status"] {
  if (call.status !== "succeeded"
    || !["baas_read", "baas_write", "arca_read", "arca_write"].includes(call.toolName)) {
    return call.status;
  }
  const result = resultObject(call);
  const nestedStatus = typeof result.status === "string" ? result.status.toLowerCase() : "";
  if (nestedStatus === "unknown") return "unknown";
  if (nestedStatus === "canceled") return "canceled";
  if (nestedStatus === "failed" || nestedStatus === "error"
    || (Number.isSafeInteger(result.exitCode) && Number(result.exitCode) !== 0)) return "failed";
  return "succeeded";
}

function browserResultSummary(call: RepairToolCall, canViewDetails = true): string {
  if (call.status === "pending") return "调用已登记，正在等待执行。";
  if (call.status === "executing") return "调用正在执行，结果尚未确定。";
  if (call.status === "failed") {
    return `调用失败${canViewDetails && call.errorCode ? `（${redactText(call.errorCode, 128)}）` : ""}。`;
  }
  if (call.status === "unknown") return "调用结果未知，不会自动重放写操作。";
  if (call.status === "canceled") return "调用已取消，未取得可用结果。";
  const result = resultObject(call);
  if (call.toolName === "antlogs") {
    const returned = Number.isSafeInteger(result.returnedEntries) ? Number(result.returnedEntries) : null;
    const total = Number.isSafeInteger(result.totalEntries) ? Number(result.totalEntries) : null;
    const coverage = deriveRepairLogSourceCoverage(result.sources);
    const hasCovered = coverage.coveredSources.length > 0;
    const hasUnavailable = coverage.unavailableSources.length > 0;
    const outcome = hasCovered && hasUnavailable
      ? "日志查询部分完成"
      : hasUnavailable
        ? "日志查询调用已结束"
        : "日志查询完成";
    const count = returned != null && returned > 0
      ? `返回 ${returned}${total != null ? ` / ${total}` : ""} 条记录`
      : hasCovered
        ? "未发现匹配条目"
        : returned === 0
          ? "未获得可用条目"
          : null;
    const covered = hasCovered
      ? `已覆盖：${coverage.coveredSources.map((source) =>
        `${source.name}（${source.entriesCount} 条${source.status === "partial" ? "，结果可能不完整" : ""}）`).join("、")}`
      : null;
    const unavailable = hasUnavailable
      ? `未覆盖：${coverage.unavailableSources.map((source) => `${source.name}（${source.reason}）`).join("、")}`
      : null;
    return [count ? `${outcome}，${count}` : outcome, covered, unavailable,
      hasUnavailable ? "未覆盖仅表示本次未取得对应日志源证据，不代表相应服务异常" : null]
      .filter((part): part is string => part != null)
      .join("。") + "。";
  }
  if (["baas_read", "baas_write", "arca_read", "arca_write"].includes(call.toolName)) {
    const exitCode = Number.isSafeInteger(result.exitCode) ? Number(result.exitCode) : null;
    const runtimeFailureReasons: Record<number, string> = {
      44: "路径不存在或无法解析",
      45: "路径解析后超出允许范围",
      46: "目标不是目录",
      47: "目标不是文件",
    };
    const effectiveStatus = browserToolStatus(call);
    const outcome = effectiveStatus === "unknown"
      ? "目标结果未知"
      : effectiveStatus === "canceled"
        ? "目标调用已取消"
        : effectiveStatus === "failed"
        ? runtimeFailureReasons[exitCode ?? -1] ?? "目标返回失败"
        : "目标执行完成";
    if (call.toolName === "baas_write" || call.toolName === "arca_write" || outcome !== "目标执行完成") {
      return `${outcome}${exitCode != null ? `（退出码 ${exitCode}）` : ""}。`;
    }
    const stdout = typeof result.stdout === "string" ? result.stdout : "";
    const nonEmptyLines = stdout.split(/\r?\n/u).filter((line) => line.trim() && line !== "[TRUNCATED]").length;
    const truncated = stdout.includes("[TRUNCATED]");
    if (call.operation === "fs_list") return `目录检查完成，返回 ${nonEmptyLines} 个条目${truncated ? "，结果已截断" : ""}。`;
    if (call.operation === "fs_find") return `文件查找完成，返回 ${nonEmptyLines} 个匹配项${truncated ? "，结果已截断" : ""}。`;
    if (call.operation === "fs_stat") return `文件元数据读取完成，返回 ${nonEmptyLines} 条记录。`;
    if (call.operation === "fs_read") return `文件片段读取完成，返回 ${nonEmptyLines} 行${truncated ? "，结果已截断" : ""}。`;
    if (call.operation === "fs_search") return `文本搜索完成，返回 ${nonEmptyLines} 个匹配行${truncated ? "，结果已截断" : ""}。`;
    if (call.operation === "process_list") return `进程检查完成，返回 ${nonEmptyLines} 行进程信息。`;
    if (call.operation === "port_list") return `端口检查完成，返回 ${nonEmptyLines} 行监听信息。`;
    if (call.operation === "http_get") return `本地 HTTP 检查成功，响应正文 ${Buffer.byteLength(stdout, "utf8")} 字节（正文未展示）。`;
    return `${outcome}${exitCode != null ? `，退出码 ${exitCode}` : ""}。`;
  }
  if (call.toolName === "ocb_read") {
    if (call.operation === "identity_file_read") return "身份文件读取完成（正文未在审计投影中展示）。";
    if (call.operation === "engine_config_read") return "引擎配置读取完成（配置正文未在审计投影中展示）。";
    return "当前运行目标读取完成。";
  }
  if (call.toolName === "ocb_write") return "获批的 OCB 修复动作已完成。";
  if (call.toolName === "cfuse_login") return "CodeFuse 登录授权流程已结束。";
  return "中央控制记录已完成。";
}

function systemConclusion(call: RepairToolCall): string {
  if (BUSINESS_AUDIT_TOOLS.has(call.toolName)) {
    return call.status === "pending" || call.status === "executing"
      ? "等待结果后记录结论。"
      : "结论尚未记录。";
  }
  return browserResultSummary(call, false);
}

function semanticConclusion(call: RepairToolCall): {
  sourceToolCallId: string;
  sourceResultDigest: string;
  conclusion: BrowserToolConclusion;
} | null {
  if (call.toolName !== "repair_control" || call.operation !== "record_conclusion" || call.status !== "succeeded") {
    return null;
  }
  const payload = unpackToolRequest(call.request).payload;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return null;
  const record = payload as Record<string, unknown>;
  const sourceToolCallId = typeof record.sourceToolCallId === "string" ? record.sourceToolCallId : "";
  const sourceResultDigest = typeof record.sourceResultDigest === "string" ? record.sourceResultDigest : "";
  const text = persistedAuditText(record.conclusionZh, 1_000);
  const nextAction = persistedAuditText(record.nextAction, 500);
  const evidenceToolCallIds = Array.isArray(record.evidenceToolCallIds)
    ? record.evidenceToolCallIds.filter((item): item is string => typeof item === "string")
    : [];
  if (!sourceToolCallId || !/^[a-f0-9]{64}$/u.test(sourceResultDigest)
    || !text || !evidenceToolCallIds.includes(sourceToolCallId)) return null;
  return {
    sourceToolCallId,
    sourceResultDigest,
    conclusion: { recordToolCallId: call.callId, text, nextAction, evidenceToolCallIds },
  };
}

/** Browser audit projection. Raw tool request/result are workload-only data. */
function browserToolCall(call: RepairToolCall, access: {
  canViewDetails: boolean;
  canExecute: boolean;
  conclusion?: BrowserToolConclusion;
}): Record<string, unknown> {
  const request = unpackToolRequest(call.request);
  const invocation = access.canViewDetails ? safeReadInvocation(call, request.payload) : null;
  const loginUrl = access.canViewDetails
    && access.canExecute
    && call.toolName === "cfuse_login"
    && (call.status === "pending" || call.status === "executing")
    && request.payload
    && typeof request.payload === "object"
    && !Array.isArray(request.payload)
    && typeof (request.payload as Record<string, unknown>).loginUrl === "string"
    ? String((request.payload as Record<string, unknown>).loginUrl)
    : null;
  return {
    toolCallId: call.callId,
    stepId: call.stepId,
    toolName: call.toolName,
    operation: call.operation,
    actionId: access.canViewDetails ? call.actionId : null,
    targetVersion: request.targetVersion,
    purpose: access.canViewDetails
      ? request.purpose ?? defaultToolPurpose(call.toolName, call.operation)
      : defaultToolPurpose(call.toolName, call.operation),
    executionTarget: browserExecutionTarget(call, request.payload, access.canViewDetails),
    ...(invocation ? { safeInvocation: invocation } : {}),
    resultSummary: browserResultSummary(call, access.canViewDetails),
    conclusion: access.conclusion
      ? {
        text: access.canViewDetails
          ? access.conclusion.text
          : "本次调用的结论已记录，详细内容仅任务所有者可见。",
        nextAction: access.canViewDetails ? access.conclusion.nextAction : null,
        evidenceToolCallIds: access.canViewDetails ? access.conclusion.evidenceToolCallIds : [],
      }
      : { text: systemConclusion(call), nextAction: null, evidenceToolCallIds: [] },
    status: browserToolStatus(call),
    error: call.errorCode || call.errorMessage
      ? {
        code: call.errorCode,
        message: access.canViewDetails ? call.errorMessage : "调用未成功，详细信息仅任务所有者可见",
      }
      : null,
    deadlineAt: call.deadlineAt,
    createdAt: call.gmtCreate,
    updatedAt: call.gmtModified,
    requiresBrowserRelay: access.canViewDetails && requiresOwnerBrowserRelay(call),
    ...(loginUrl ? { cfuseLoginUrl: loginUrl } : {}),
  };
}

function browserToolCalls(
  calls: RepairToolCall[],
  canViewDetails: boolean,
  canExecute: boolean,
): Array<Record<string, unknown>> {
  const sources = new Map(calls
    .filter((call) => !(call.toolName === "repair_control" && call.operation === "record_conclusion"))
    .map((call) => [call.callId, call]));
  const conclusions = new Map<string, BrowserToolConclusion>();
  for (const call of calls) {
    const parsed = semanticConclusion(call);
    const source = parsed ? sources.get(parsed.sourceToolCallId) : null;
    if (parsed && source
      && source.taskId === call.taskId
      && source.stepId === call.stepId
      && source.executionId === call.executionId
      && source.authorizationScopeDigest === call.authorizationScopeDigest
      && source.resultDigest === parsed.sourceResultDigest
      && !conclusions.has(parsed.sourceToolCallId)) {
      conclusions.set(parsed.sourceToolCallId, parsed.conclusion);
    }
  }
  return calls
    .filter((call) => !(call.toolName === "repair_control" && call.operation === "record_conclusion"))
    .map((call) => browserToolCall(call, {
      canViewDetails,
      canExecute,
      conclusion: conclusions.get(call.callId),
    }));
}

function compactRecoveryToolCall(call: Record<string, unknown>): Record<string, unknown> {
  const conclusion = call.conclusion && typeof call.conclusion === "object" && !Array.isArray(call.conclusion)
    ? call.conclusion as Record<string, unknown>
    : {};
  const error = call.error && typeof call.error === "object" && !Array.isArray(call.error)
    ? call.error as Record<string, unknown>
    : null;
  return {
    toolCallId: call.toolCallId,
    toolName: call.toolName,
    operation: call.operation,
    actionId: call.actionId,
    targetVersion: call.targetVersion,
    purpose: redactText(call.purpose, 500),
    resultSummary: redactText(call.resultSummary, 1_000),
    conclusion: {
      text: redactText(conclusion.text, 1_000),
      nextAction: conclusion.nextAction == null ? null : redactText(conclusion.nextAction, 500),
      evidenceToolCallIds: Array.isArray(conclusion.evidenceToolCallIds)
        ? conclusion.evidenceToolCallIds.filter((item): item is string => typeof item === "string").slice(0, 50)
        : [],
    },
    status: call.status,
    error: error == null ? null : {
      code: error.code == null ? null : redactText(error.code, 128),
      message: error.message == null ? null : redactText(error.message, 500),
    },
    createdAt: call.createdAt,
    updatedAt: call.updatedAt,
  };
}

function browserStepOutput(step: EvolveStepRow): Record<string, unknown> {
  const output = parseOutput(step);
  const recovery = repairRecoveryProgress(output);
  return {
    ...(typeof output.artifactDigest === "string" ? { artifactDigest: output.artifactDigest } : {}),
    ...(typeof output.summary === "string" ? { summary: redactText(output.summary, 4_000) } : {}),
    ...(recovery == null ? {} : { recovery }),
  };
}

function browserStepFailure(step: EvolveStepRow): Record<string, unknown> | null {
  const raw = parseOutput(step).failure;
  const metadata = raw && typeof raw === "object" && !Array.isArray(raw)
    && (raw as Record<string, unknown>).schemaVersion === REPAIR_STEP_FAILURE_VERSION
    ? repairStepFailureMetadata(raw as Record<string, unknown>)
    : null;
  const retryable = step.retryable === 1 ? true : step.retryable === 0 ? false : null;
  if (step.error_code == null && metadata == null && retryable == null) return null;
  return {
    ...(step.error_code == null ? {} : { code: redactText(step.error_code, 128) }),
    ...(metadata?.stage == null ? {} : { stage: metadata.stage }),
    ...(metadata?.reason == null ? {} : { reason: metadata.reason }),
    ...(metadata?.artifactName == null ? {} : { artifactName: metadata.artifactName }),
    ...(metadata?.exitCode == null ? {} : { exitCode: metadata.exitCode }),
    ...(metadata?.httpStatus == null ? {} : { httpStatus: metadata.httpStatus }),
    ...(metadata?.providerCode == null ? {} : { providerCode: metadata.providerCode }),
    ...(metadata?.providerRequestId == null ? {} : { providerRequestId: metadata.providerRequestId }),
    ...(metadata?.retryCount == null ? {} : { retryCount: metadata.retryCount }),
    ...(metadata?.field == null ? {} : { field: metadata.field }),
    ...(metadata?.rule == null ? {} : { rule: metadata.rule }),
    ...(metadata?.retryBranch == null ? {} : { retryBranch: metadata.retryBranch }),
    retryable,
  };
}

function failedPlanCanResume(
  task: EvolveTaskRow,
  config: RepairTaskConfig,
  step: EvolveStepRow | null,
): boolean {
  if (task.status !== "failed"
    || config.current.phase !== "repair_plan"
    || step?.step_id !== config.current.stepId
    || step.status !== "failed") return false;
  return config.execution.state === "ended" || config.execution.state === "waiting_decision";
}

function browserPlan(plan: RepairPlanArtifact | null, canViewDetails: boolean): Record<string, unknown> | null {
  if (!plan) return null;
  return {
    schemaVersion: plan.schemaVersion,
    taskId: plan.taskId,
    stepId: plan.stepId,
    attempt: plan.attempt,
    runtimeTargetVersion: plan.runtimeTargetVersion,
    ...(isRepairPlanV2(plan) ? {
      quality: plan.quality,
      recommendation: {
        disposition: plan.recommendation.disposition,
        summary: redactText(plan.recommendation.summary, 4_000),
        reason: redactText(plan.recommendation.reason, 4_000),
        ...(plan.recommendation.nextSteps
          ? { nextSteps: plan.recommendation.nextSteps.map(item => redactText(item, 4_000)) }
          : {}),
      },
    } : { legacySemantics: true }),
    diagnosis: redactValue(plan.diagnosis),
    actions: plan.actions.map((action) => ({
      actionId: action.actionId,
      type: action.type,
      summary: redactText(action.summary, 4_000),
      risk: redactText(action.risk, 4_000),
      verification: redactText(action.verification, 4_000),
      rollback: action.rollback == null ? null : redactText(action.rollback, 4_000),
      ...(action.dependsOn ? { dependsOn: action.dependsOn } : {}),
      ...(action.rollbackActionId != null ? { rollbackActionId: action.rollbackActionId } : {}),
      ...(canViewDetails && action.command != null
        ? { command: action.command }
        : {}),
      ...(canViewDetails && action.operation != null
        ? { operation: action.operation }
        : {}),
    })),
  };
}

function browserApprovedPlan(plan: ApprovedRepairPlan | null): Record<string, unknown> | null {
  if (!plan) return null;
  return {
    stepId: plan.stepId,
    artifactDigest: plan.artifactDigest,
    approvedAt: plan.approvedAt,
  };
}

function browserPendingDecision(
  decision: RepairPendingDecision | null,
  canViewDetails: boolean,
): Record<string, unknown> | null {
  if (!decision) return null;
  return {
    kind: decision.kind,
    requestedAt: decision.requestedAt,
    artifactDigest: decision.artifactDigest,
    ...(canViewDetails ? { feedback: decision.feedback } : {}),
  };
}

type RepairViewAccess = {
  /** Owner/browser capabilities. Admin state transitions are exposed separately. */
  canOperate: boolean;
  canManageShare: boolean;
  canAdminOperate?: boolean;
};

export class RepairTaskService {
  private readonly runner: AisTaskRunner<RepairDispatchConfig>;
  private readonly now: () => number;
  private readonly cfuseAuthCodes = new Map<string, CfuseAuthCodeSlot>();

  constructor(private readonly deps: RepairTaskServiceDeps) {
    this.runner = new AisTaskRunner(deps.repo, deps.store, deps.ais, definition(deps.config));
    this.now = deps.nowSeconds ?? (() => Math.floor(Date.now() / 1_000));
  }

  async listBots(actorUserId: string, isAdmin = false, requestedOwnerId?: unknown) {
    const actor = requiredText(actorUserId, "actorUserId", 128);
    const selectedOwner = optionalText(requestedOwnerId, "ownerId", 128);
    if (selectedOwner && selectedOwner !== actor && !isAdmin) {
      repairForbidden("repair_admin_required", "只有 ClawEvolve 管理员可以查看其他 Owner 的 Bot");
    }
    const ownerId = selectedOwner ?? actor;
    const bots = await this.deps.repo.listEvolveBots(ownerId);
    const resolved = await Promise.all(bots.map(async (bot) => {
      const runtime = ownerId === actor
        ? await this.deps.repo.resolveEvolveBotRuntime(actor, bot.botId)
        : await this.deps.repo.resolveEvolveBotRuntimeForOwner(ownerId, bot.botId);
      if (!runtime?.env
        || runtime.botType?.toLowerCase() !== "personal") return null;
      let targetEnvironment: RepairTargetEnvironment;
      try {
        targetEnvironment = environment(runtime.env);
      } catch (error) {
        if (error instanceof RepairError && error.code === "unsupported_target_environment") return null;
        throw error;
      }
      return { ...bot, ownerId, env: targetEnvironment };
    }));
    return resolved.filter((bot): bot is NonNullable<typeof bot> => bot !== null);
  }

  async createTask(input: {
    actorUserId: string;
    authHeaders: Record<string, string>;
    body: RepairCreateTaskInput;
    isAdmin?: boolean;
  }): Promise<Record<string, unknown>> {
    this.assertSnapshotConfigured();
    const selectedAgent = initialAgentSelection(input.body);
    const actor = requiredText(input.actorUserId, "actorUserId", 128);
    const allowAdmin = input.isAdmin === true;
    const requestedOwnerId = optionalText(input.body.ownerId, "ownerId", 128);
    const requestedTargetUserId = optionalText(input.body.targetUserId, "targetUserId", 128);
    if (requestedOwnerId && requestedTargetUserId && requestedOwnerId !== requestedTargetUserId) {
      repairValidation("repair_target_owner_mismatch", "ownerId 与 targetUserId 不一致");
    }
    const targetUserId = requestedOwnerId ?? requestedTargetUserId ?? actor;
    if (targetUserId !== actor && !allowAdmin) {
      repairForbidden("repair_admin_required", "只有 ClawEvolve 管理员可以为其他 Owner 创建 Repair");
    }
    const botId = requiredText(input.body.botId, "botId", 128);
    if (!/^[A-Za-z0-9_-]+$/.test(botId)) repairValidation("invalid_bot_id", "botId 格式不合法");
    const runtime = targetUserId === actor
      ? await this.deps.repo.resolveEvolveBotRuntime(actor, botId)
      : await this.deps.repo.resolveEvolveBotRuntimeForOwner(targetUserId, botId);
    if (!runtime) repairNotFound("repair_target_not_found", "所选 Bot 不存在或当前用户无权访问");
    if (targetUserId === actor && (runtime.accessType !== "owner" || runtime.ownerId !== actor)) {
      repairForbidden("repair_target_not_owned", "Repair 普通用户只能选择自己的 Bot");
    }
    if (!runtime.env) {
      repairValidation("unsupported_target_environment", "所选 Bot 的运行环境缺失，Repair 当前仅支持 pre 和 prod Bot");
    }
    const targetEnvironment = environment(runtime.env);
    if (hasInput(input.body.targetEnvironment)) {
      let requestedEnvironment: RepairTargetEnvironment;
      try {
        requestedEnvironment = environment(input.body.targetEnvironment);
      } catch (error) {
        if (!(error instanceof RepairError) || error.code !== "unsupported_target_environment") throw error;
        throw new RepairError(
          409,
          "target_environment_mismatch",
          `请求环境与 Bot 当前运行环境 ${targetEnvironment} 不一致`,
        );
      }
      if (requestedEnvironment !== targetEnvironment) {
        throw new RepairError(
          409,
          "target_environment_mismatch",
          `请求环境 ${requestedEnvironment} 与 Bot 当前运行环境 ${targetEnvironment} 不一致`,
        );
      }
    }
    const insightImprovementId = optionalPositiveInteger(input.body.insightImprovementId, "insightImprovementId");
    const insightRequestId = typeof input.body.insightRequestId === "string"
      ? input.body.insightRequestId.trim()
      : "";
    const requestedRepairDirection = typeof input.body.repairDirection === "string"
      ? input.body.repairDirection.replace(/\r\n?/gu, "\n").trim()
      : "";
    if (requestedRepairDirection.length > 5_000) {
      repairValidation("invalid_repair_input", "repairDirection 不能超过 5000 个字符");
    }
    const adminOverrideReason = typeof input.body.adminOverrideReason === "string"
      ? input.body.adminOverrideReason.trim()
      : "";
    const persistAutoRepairGrant = input.body.persistAutoRepairGrant === true;
    const requestedAuthorizationGrantId = optionalPositiveInteger(input.body.authorizationGrantId, "authorizationGrantId");
    const adminConsentToken = typeof input.body.adminConsentToken === "string"
      ? input.body.adminConsentToken.trim()
      : "";
    if (targetUserId !== actor && persistAutoRepairGrant) {
      repairValidation("invalid_repair_input", "管理员代处理不能创建持续授权");
    }
    if (targetUserId !== actor && insightImprovementId !== null
      && (!adminOverrideReason || adminOverrideReason.length > 1_000)) {
      repairValidation("invalid_repair_input", "管理员代处理必须提供 1 到 1000 个字符的 adminOverrideReason");
    }
    if (insightImprovementId !== null && !insightRequestId) {
      repairValidation("invalid_repair_input", "insightImprovementId 必须同时提供 insightRequestId");
    }
    if (insightRequestId.length > 128) {
      repairValidation("invalid_repair_input", "insightRequestId 不能超过 128 个字符");
    }

    let insightDetail: ImprovementDetail | null = null;
    let insightSource: RepairInsightSource | undefined;
    let insightCrossBot = false;
    if (insightImprovementId !== null) {
      const bridge = this.deps.insightBridge;
      if (!bridge) repairUnavailable("repair_insight_not_configured", "Repair 未配置 Insight 改进项来源");
      insightDetail = await bridge.getDetail(targetUserId, insightImprovementId, allowAdmin);
      if (!insightDetail) repairNotFound("repair_insight_not_found", "关联的 Insight 改进项不存在或无权访问");
      if (insightDetail.ownerUserId !== targetUserId) {
        repairValidation("repair_insight_target_mismatch", "Insight 改进项与 Repair 目标用户不一致");
      }
      insightCrossBot = insightDetail.botId !== botId;
      if (insightCrossBot && input.body.crossBotConfirmed !== true) {
        repairValidation("repair_insight_cross_bot_confirmation_required", "跨 Bot Repair 必须明确确认 Evidence 来源与执行目标不同");
      }
      const existingLink = await bridge.findLinkByRequest(insightImprovementId, insightRequestId);
      if (existingLink) {
        const existingTask = await this.deps.repo.findTask(existingLink.evolve_task_id);
        if (!existingTask || existingTask.task_type !== "repair") {
          repairValidation("repair_insight_link_conflict", "该幂等键已关联非 Repair 任务或任务不存在");
        }
        return this.view(existingTask, this.actorViewAccess(existingTask, actor, allowAdmin));
      }
      const latestTaskStatus = insightDetail.latestEvolveTaskStatus?.toLowerCase() ?? null;
      const activeTask = latestTaskStatus != null
        && !["completed", "failed", "canceled", "cancelled"].includes(latestTaskStatus);
      if (activeTask) {
        repairValidation("repair_insight_already_running", "该改进项已有正在运行的修复任务");
      }
      const retryable = insightDetail.status.toUpperCase() === "ACTIVE"
        || (insightDetail.status.toUpperCase() === "IN_PROGRESS"
          && (["failed", "canceled", "cancelled"].includes(latestTaskStatus ?? "")
            || ["STILL_PRESENT", "INSUFFICIENT_DATA"].includes(insightDetail.verificationStatus)));
      if (!retryable) {
        repairValidation("repair_insight_rerun_not_allowed", "当前改进项没有可重新发起的 Repair Run");
      }
      const repairDirection = requestedRepairDirection
        || insightDetail.suggestedAction
        || insightDetail.userGuidance
        || null;
      let authorizationGrantId = requestedAuthorizationGrantId ?? undefined;
      if (requestedAuthorizationGrantId !== null) {
        if (targetUserId !== actor) {
          repairForbidden("repair_authorization_owner_required", "持续授权只能由目标 Bot Owner 使用");
        }
        if (!this.deps.insightBridge?.validatePersistentAuthorization) {
          repairUnavailable("repair_insight_authorization_not_configured", "Repair 未配置自动修复授权校验服务");
        }
        await this.deps.insightBridge.validatePersistentAuthorization({
          ownerUserId: targetUserId,
          botId,
          improvement: insightDetail,
          grantId: requestedAuthorizationGrantId,
        });
      }
      if (persistAutoRepairGrant) {
        if (!this.deps.insightBridge?.ensurePersistentAuthorization) {
          repairUnavailable("repair_insight_authorization_not_configured", "Repair 未配置自动修复授权服务");
        }
        const grant = await this.deps.insightBridge.ensurePersistentAuthorization({
          ownerUserId: targetUserId,
          botId,
          improvement: insightDetail,
          grantedBy: actor,
          ...(adminConsentToken ? { adminConsentToken } : {}),
        });
        authorizationGrantId = grant.grantId;
      }
      insightSource = repairInsightSource(
        insightDetail,
        insightRequestId,
        repairDirection,
        persistAutoRepairGrant || requestedAuthorizationGrantId != null ? "PERSISTENT" : "ONCE",
        authorizationGrantId,
        targetUserId !== actor
          ? { operatorUserId: actor, reason: adminOverrideReason }
          : undefined,
      );
    }

    const target = await this.deps.targets.resolve({
      environment: targetEnvironment,
      ownerId: targetUserId,
      botId,
    });
    const scope = authorizationScope(
      actor,
      target,
      targetUserId === actor ? "OWNER" : "ADMIN_ONCE",
    );
    if (scope.ownerId !== targetUserId) repairForbidden("repair_target_not_owned", "Repair 目标 Owner 校验失败");
    const taskId = `REPAIR-${randomUUID()}`;
    const stepId = `${taskId}-PLAN-1`;
    const artifacts = artifactsFor(taskId, stepId, "repair_plan");
    const ticket = issueRepairExecutionTicket();
    const now = this.now();
    const snapshot = targetSnapshot(target, 1, "task_created");
    const config: RepairTaskConfig = {
      schemaVersion: REPAIR_CONTRACT_VERSION,
      taskId,
      controlPlaneEnvironment: this.deps.config.controlPlaneEnvironment,
      shared: false,
      issue: issueOf({
        ...input.body,
        symptom: input.body.symptom ?? insightDetail?.title ?? insightSource?.repairDirection,
        errorText: input.body.errorText ?? insightDetail?.rootCauseSummary ?? insightDetail?.suggestedAction,
      }),
      authorizationScope: scope,
      authorizationScopeDigest: authorizationScopeDigest(scope),
      target,
      targetFingerprint: snapshot.fingerprint,
      runtimeTarget: snapshot,
      runtimeTargetHistory: [snapshot],
      current: { stepId, stepNo: 1, attempt: 1, phase: "repair_plan", artifacts },
      history: [],
      approvedPlan: null,
      pendingDecision: null,
      diagnosticMode: diagnosticMode(input.body.diagnosticMode),
      agentMode: selectedAgent.agentMode,
      llmUseDefault: selectedAgent.llmUseDefault,
      llmModel: selectedAgent.llmModel,
      openclawUsesCustomApiKey: selectedAgent.openclawUsesCustomApiKey,
      cfuseEngine: selectedAgent.cfuseEngine,
      cfuseModel: selectedAgent.cfuseModel,
      execution: {
        executionId: `exec-${randomUUID()}`,
        ticketDigest: ticket.digest,
        jobId: null,
        ccSessionId: null,
        state: "dispatching",
        stepId,
        phase: "repair_plan",
        leaseExpiresAt: now + this.deps.config.decisionGraceSeconds,
        decisionDeadlineAt: null,
        lastHeartbeatAt: null,
        invalidatedAt: null,
      },
      publicBaseUrl: this.deps.config.publicBaseUrl,
      artifacts,
      ...(insightSource ? { insightSource } : {}),
    };
    await this.deps.repairRepo.createTaskWithStep({
      task: {
        taskId,
        userId: targetUserId,
        botId,
        taskName: typeof input.body.taskName === "string" && input.body.taskName.trim()
          ? input.body.taskName.trim().slice(0, 128)
          : `Repair ${botId}`,
        remark: config.issue.symptom,
        config,
        createdBy: actor,
      },
      step: { stepId, stepType: "repair_plan", stepNo: 1, command: "repair_plan" },
    });
    if (insightSource && insightDetail && this.deps.insightBridge) {
      try {
        await this.deps.insightBridge.freezeTask({
          taskId,
          detail: insightDetail,
          repairDirection: insightSource.repairDirection,
          target: {
            ownerUserId: targetUserId,
            botId,
            selectedBy: actor,
            crossBotConfirmed: insightCrossBot,
          },
          ...(targetUserId !== actor ? {
            adminOverride: {
              mode: "ADMIN_ONCE",
              operatorUserId: actor,
              reason: adminOverrideReason,
              repairDirection: insightSource.repairDirection,
            },
          } : {}),
        });
        await this.deps.insightBridge.linkTask({
          improvementId: insightSource.improvementId,
          ownerUserId: targetUserId,
          evolveTaskId: taskId,
          requestId: insightSource.requestId,
          createdBy: actor,
        });
      } catch (error) {
        await this.deps.repo.deleteTask(taskId);
        throw error;
      }
    }
    await this.dispatch(taskId, ticket.ticket, selectedAgent.llmApiKey);
    const createdTask = await this.deps.repo.findTask(taskId);
    if (!createdTask) repairNotFound("repair_task_not_found", "Repair Task 不存在");
    return this.view(createdTask, this.actorViewAccess(createdTask, actor, allowAdmin));
  }

  async getTask(
    actorUserId: string,
    taskId: string,
    isAdmin = false,
  ): Promise<Record<string, unknown>> {
    const access = await this.readableTask(actorUserId, taskId, isAdmin);
    return this.view(access.task, {
      canOperate: access.isOwner || isAdmin,
      canManageShare: access.isOwner || isAdmin,
      canAdminOperate: isAdmin && !access.isOwner,
    });
  }

  async getStepPlan(
    actorUserId: string,
    taskId: string,
    stepId: string,
    isAdmin = false,
  ): Promise<Record<string, unknown>> {
    const { task } = await this.readableTask(actorUserId, taskId, isAdmin);
    const config = taskConfig(task);
    const requestedStepId = requiredText(stepId, "stepId", 256);
    const history = config.history.find(item => item.stepId === requestedStepId);
    if (!history || history.phase !== "repair_plan" || history.status !== "succeeded") {
      repairNotFound("repair_historical_plan_not_found", "历史 Repair Plan 不存在");
    }
    const step = await this.deps.repo.findStep(requestedStepId);
    if (!step
      || step.task_id !== task.task_id
      || step.step_id !== history.stepId
      || step.step_no !== history.stepNo
      || step.step_type !== history.phase
      || step.status !== "succeeded") {
      repairNotFound("repair_historical_plan_not_found", "历史 Repair Plan 不存在");
    }
    const plan = await this.loadHistoricalPlan(config, history, step);
    return {
      taskId: config.taskId,
      step: {
        stepId: history.stepId,
        stepNo: history.stepNo,
        attempt: history.attempt,
        status: history.status,
        artifactDigest: history.artifactDigest,
      },
      source: "history",
      readOnly: true,
      approvable: false,
      plan: browserPlan(plan, true),
    };
  }

  async setTaskShared(input: {
    actorUserId: string;
    isAdmin?: boolean;
    taskId: string;
    shared: boolean;
  }): Promise<Record<string, unknown>> {
    if (typeof input.shared !== "boolean") {
      return repairValidation("invalid_repair_shared", "shared 必须为布尔值");
    }
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const task = await this.ownedTask(input.actorUserId, input.taskId, input.isAdmin === true);
      const config = taskConfig(task);
      if (config.shared === input.shared) {
        return this.view(task, { canOperate: true, canManageShare: true });
      }
      const updated = await this.deps.repairRepo.compareAndSetTaskConfig({
        taskId: task.task_id,
        expectedTaskStatuses: [task.status],
        expectedCurrentStepId: config.current.stepId,
        expectedTaskConfigDigest: storedConfigDigest(task.config_json),
        nextConfig: { ...config, shared: input.shared },
      });
      if (!updated) continue;
      const reloaded = await this.deps.repo.findTask(task.task_id);
      if (!reloaded || reloaded.task_type !== "repair") {
        repairNotFound("repair_task_not_found", "Repair Task 不存在");
      }
      return this.view(reloaded, { canOperate: true, canManageShare: true });
    }
    throw new RepairError(409, "repair_share_conflict", "Repair 分享状态发生并发变化，请重试");
  }

  async terminateTask(input: {
    actorUserId: string;
    taskId: string;
    reason?: unknown;
    isAdmin?: boolean;
  }): Promise<Record<string, unknown>> {
    const reason = optionalText(input.reason, "reason", 500) ?? "用户终止本次 Repair 实验";
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const task = await this.ownedTask(input.actorUserId, input.taskId, input.isAdmin === true);
      const config = taskConfig(task);
      const step = await this.currentStep(task, config);
      const jobId = config.execution.jobId ?? step.bot_run_id;
      if (task.status === "canceled") {
        const termination = await this.stopTerminatedAisJob(step.step_id, jobId);
        return {
          ...await this.view(task, { canOperate: true, canManageShare: true }),
          termination,
        };
      }
      if (new Set(["completed", "failed"]).has(task.status)) {
        throw new RepairError(409, "repair_task_not_terminable", `当前 Repair 状态不允许终止: ${task.status}`);
      }
      if (!new Set([
        "pending", "running", "waiting_approval", "waiting_acceptance", "waiting_context",
      ]).has(task.status)) {
        throw new RepairError(409, "repair_task_not_terminable", `当前 Repair 状态不允许终止: ${task.status}`);
      }

      const activeCalls = await this.deps.repairRepo.listToolCalls(task.task_id, {
        stepId: step.step_id,
        statuses: ["pending", "executing"],
        limit: 500,
      });
      for (const call of activeCalls) {
        await this.finishTerminatedToolCall(call).catch(() => null);
      }

      const now = this.now();
      const cancelStep = new Set(["created", "dispatching", "dispatched", "running"]).has(step.status);
      const nextConfig: RepairTaskConfig = {
        ...config,
        pendingDecision: null,
        execution: {
          ...config.execution,
          state: "ended",
          leaseExpiresAt: now,
          decisionDeadlineAt: null,
          invalidatedAt: now,
        },
      };
      const transitioned = await this.deps.repairRepo.transitionStep({
        taskId: task.task_id,
        expectedTaskStatuses: [task.status],
        expectedCurrentStepId: step.step_id,
        expectedTaskConfigDigest: storedConfigDigest(task.config_json),
        previousStep: {
          stepId: step.step_id,
          expectedStatuses: [step.status],
          status: cancelStep ? "canceled" : step.status,
          ...(cancelStep ? {
            errorCode: "REPAIR_TERMINATED_BY_USER",
            errorMessage: reason,
            retryable: false,
          } : {}),
        },
        nextTaskStatus: "canceled",
        nextConfig,
      });
      if (transitioned.outcome === "conflict") continue;

      const termination = await this.stopTerminatedAisJob(step.step_id, jobId);
      const reloaded = await this.deps.repo.findTask(task.task_id);
      if (!reloaded) repairNotFound("repair_task_not_found", "Repair Task 不存在");
      return {
        ...await this.view(reloaded, this.actorViewAccess(reloaded, input.actorUserId, input.isAdmin === true)),
        termination,
      };
    }
    throw new RepairError(409, "repair_termination_conflict", "Repair 状态正在变化，请重试终止");
  }

  async decidePlan(input: {
    actorUserId: string;
    authHeaders: Record<string, string>;
    taskId: string;
    body: RepairDecisionInput;
    isAdmin?: boolean;
  }): Promise<Record<string, unknown>> {
    let task = await this.ownedTask(input.actorUserId, input.taskId, input.isAdmin === true);
    let config = taskConfig(task);
    assertExecutionSupported(config);
    assertAgentSelectionEcho(config, input.body);
    if (task.status !== "waiting_approval" || config.current.phase !== "repair_plan") {
      throw new RepairError(409, "repair_not_waiting_approval", "Repair Task 当前不等待方案决策");
    }
    await this.refreshTarget(input.actorUserId, task, config, "before_action", false, input.isAdmin === true);
    const refreshedTask = await this.deps.repo.findTask(task.task_id);
    if (!refreshedTask || refreshedTask.task_type !== "repair") {
      repairNotFound("repair_task_not_found", "Repair Task 不存在");
    }
    task = refreshedTask;
    config = taskConfig(task);
    if (task.status !== "waiting_approval" || config.current.phase !== "repair_plan") {
      throw new RepairError(409, "repair_not_waiting_approval", "Repair Task 当前不等待方案决策");
    }
    const expectedConfigDigest = storedConfigDigest(task.config_json);
    if (config.pendingDecision) {
      throw new RepairError(409, "repair_decision_already_recorded", "当前 Repair 方案已经记录了用户决策");
    }
    const decision = requiredText(input.body.decision, "decision", 32).toLowerCase();
    const output = parseOutput(await this.currentStep(task, config));
    const actualDigest = artifactDigest(output.artifactDigest);
    let pending: RepairPendingDecision;
    let approvedPlan: ApprovedRepairPlan | null;
    if (decision === "approve") {
      const requestedDigest = artifactDigest(input.body.artifactDigest);
      if (requestedDigest !== actualDigest) {
        throw new RepairError(409, "repair_plan_digest_mismatch", "批准的 digest 不是当前不可变方案");
      }
      const plan = await this.loadAndValidatePlan(config, actualDigest);
      if (!isRepairPlanV2(plan) && plan.actions.length === 0) {
        throw new RepairError(
          409,
          "repair_legacy_empty_plan_not_approvable",
          "旧版空方案未声明是无需变更还是证据不足，请重新生成方案后再决策",
        );
      }
      if (isRepairPlanV2(plan) && plan.recommendation.disposition === "insufficient_evidence") {
        throw new RepairError(
          409,
          "repair_plan_not_approvable",
          "当前方案明确为证据不足，不能批准为无需修复；请补充线索并重新规划",
        );
      }
      approvedPlan = {
        stepId: config.current.stepId,
        artifactDigest: actualDigest,
        objectKey: config.current.artifacts.plan.objectKey,
        approvedAt: new Date().toISOString(),
      };
      if (isRepairPlanV2(plan) && plan.recommendation.disposition === "no_change") {
        const now = this.now();
        const completed = this.archiveCurrent(config, null, actualDigest);
        completed.approvedPlan = approvedPlan;
        completed.pendingDecision = null;
        completed.execution = {
          ...completed.execution,
          state: "ended",
          invalidatedAt: now,
          leaseExpiresAt: now,
          decisionDeadlineAt: null,
        };
        const transitioned = await this.deps.repairRepo.transitionStep({
          taskId: task.task_id,
          expectedTaskStatuses: ["waiting_approval"],
          expectedCurrentStepId: config.current.stepId,
          expectedTaskConfigDigest: expectedConfigDigest,
          previousStep: {
            stepId: config.current.stepId,
            expectedStatuses: ["succeeded"],
            status: "succeeded",
          },
          nextTaskStatus: "completed",
          nextConfig: completed,
        });
        if (transitioned.outcome !== "transitioned") {
          throw new RepairError(409, "repair_decision_conflict", `Repair 方案决策冲突: ${transitioned.reason}`);
        }
        const updatedTask = await this.deps.repo.findTask(task.task_id);
        if (!updatedTask) repairNotFound("repair_task_not_found", "Repair Task 不存在");
        return this.view(updatedTask, this.actorViewAccess(updatedTask, input.actorUserId, input.isAdmin === true));
      }
      pending = {
        kind: "approve_plan",
        requestedBy: input.actorUserId,
        requestedAt: new Date().toISOString(),
        artifactDigest: actualDigest,
        feedback: null,
      };
    } else if (decision === "reject") {
      pending = {
        kind: "reject_plan",
        requestedBy: input.actorUserId,
        requestedAt: new Date().toISOString(),
        artifactDigest: actualDigest,
        feedback: redactText(requiredText(input.body.reason, "reason", 4_000), 4_000),
      };
      approvedPlan = null;
    } else {
      return repairValidation("invalid_repair_decision", "decision 必须是 approve 或 reject");
    }
    const next = { ...config, approvedPlan, pendingDecision: pending };
    if (this.executionCanClaim(next)) {
      const transitioned = await this.deps.repairRepo.transitionStep({
        taskId: task.task_id,
        expectedTaskStatuses: ["waiting_approval"],
        expectedCurrentStepId: config.current.stepId,
        expectedTaskConfigDigest: expectedConfigDigest,
        nextTaskStatus: "waiting_approval",
        nextConfig: next,
      });
      if (transitioned.outcome !== "transitioned") {
        throw new RepairError(409, "repair_decision_conflict", `Repair 方案决策冲突: ${transitioned.reason}`);
      }
      const updatedTask = await this.deps.repo.findTask(task.task_id);
      if (!updatedTask) repairNotFound("repair_task_not_found", "Repair Task 不存在");
      return this.view(updatedTask, this.actorViewAccess(updatedTask, input.actorUserId, input.isAdmin === true));
    }
    return this.startNewExecution(
      task,
      next,
      this.phaseForDecision(pending),
      newExecutionApiKey(next, input.body),
      input.actorUserId,
      input.isAdmin === true,
    );
  }

  async decideResult(input: {
    actorUserId: string;
    authHeaders: Record<string, string>;
    taskId: string;
    body: RepairDecisionInput;
    isAdmin?: boolean;
  }): Promise<Record<string, unknown>> {
    let task = await this.ownedTask(input.actorUserId, input.taskId, input.isAdmin === true);
    let config = taskConfig(task);
    const decision = requiredText(input.body.decision, "decision", 32).toLowerCase();
    if (decision !== "accept" && decision !== "retry") {
      return repairValidation("invalid_repair_decision", "decision 必须是 accept 或 retry");
    }
    if (decision === "retry") assertExecutionSupported(config);
    assertAgentSelectionEcho(config, input.body);
    if (task.status !== "waiting_acceptance" || config.current.phase !== "repair_apply") {
      throw new RepairError(409, "repair_not_waiting_acceptance", "Repair Task 当前不等待结果决策");
    }
    if (executionSupported(config)) {
      await this.refreshTarget(input.actorUserId, task, config, "after_action", false, input.isAdmin === true);
    }
    const refreshedTask = await this.deps.repo.findTask(task.task_id);
    if (!refreshedTask || refreshedTask.task_type !== "repair") {
      repairNotFound("repair_task_not_found", "Repair Task 不存在");
    }
    task = refreshedTask;
    config = taskConfig(task);
    if (task.status !== "waiting_acceptance" || config.current.phase !== "repair_apply") {
      throw new RepairError(409, "repair_not_waiting_acceptance", "Repair Task 当前不等待结果决策");
    }
    const expectedConfigDigest = storedConfigDigest(task.config_json);
    if (config.pendingDecision) {
      throw new RepairError(409, "repair_decision_already_recorded", "当前 Repair 结果已经记录了用户决策");
    }
    if (decision === "accept") {
      const step = await this.currentStep(task, config);
      const completed = this.archiveCurrent(config, null, optionalArtifactDigest(parseOutput(step).artifactDigest));
      completed.execution = {
        ...completed.execution,
        state: "ended",
        invalidatedAt: this.now(),
        leaseExpiresAt: this.now(),
        decisionDeadlineAt: null,
      };
      completed.pendingDecision = null;
      const transitioned = await this.deps.repairRepo.transitionStep({
        taskId: task.task_id,
        expectedTaskStatuses: ["waiting_acceptance"],
        expectedCurrentStepId: config.current.stepId,
        expectedTaskConfigDigest: expectedConfigDigest,
        previousStep: {
          stepId: config.current.stepId,
          expectedStatuses: ["succeeded"],
          status: "succeeded",
        },
        nextTaskStatus: "completed",
        nextConfig: completed,
      });
      if (transitioned.outcome !== "transitioned") {
        throw new RepairError(409, "repair_result_decision_conflict", `Repair 结果决策冲突: ${transitioned.reason}`);
      }
      if (config.insightSource && this.deps.insightBridge) {
        try {
          await this.deps.insightBridge.markApplied({
            taskId: task.task_id,
            improvementId: config.insightSource.improvementId,
            requestId: config.insightSource.requestId,
            appliedBy: "repair-agent",
          });
        } catch (error) {
          console.warn(`[clawweb][repair][insight-apply] callback failed task=${task.task_id}: ${error instanceof Error ? error.message : String(error)}`);
        }
      }
      const updatedTask = await this.deps.repo.findTask(task.task_id);
      if (!updatedTask) repairNotFound("repair_task_not_found", "Repair Task 不存在");
      return this.view(updatedTask, this.actorViewAccess(updatedTask, input.actorUserId, input.isAdmin === true));
    }
    const pending: RepairPendingDecision = {
      kind: "retry_result",
      requestedBy: input.actorUserId,
      requestedAt: new Date().toISOString(),
      artifactDigest: optionalArtifactDigest(parseOutput(await this.currentStep(task, config)).artifactDigest),
      feedback: redactText(requiredText(input.body.reason, "reason", 4_000), 4_000),
    };
    const next = { ...config, approvedPlan: null, pendingDecision: pending };
    if (this.executionCanClaim(next)) {
      const transitioned = await this.deps.repairRepo.transitionStep({
        taskId: task.task_id,
        expectedTaskStatuses: ["waiting_acceptance"],
        expectedCurrentStepId: config.current.stepId,
        expectedTaskConfigDigest: expectedConfigDigest,
        nextTaskStatus: "waiting_acceptance",
        nextConfig: next,
      });
      if (transitioned.outcome !== "transitioned") {
        throw new RepairError(409, "repair_result_decision_conflict", `Repair 结果决策冲突: ${transitioned.reason}`);
      }
      const updatedTask = await this.deps.repo.findTask(task.task_id);
      if (!updatedTask) repairNotFound("repair_task_not_found", "Repair Task 不存在");
      return this.view(updatedTask, this.actorViewAccess(updatedTask, input.actorUserId, input.isAdmin === true));
    }
    return this.startNewExecution(
      task,
      next,
      "repair_plan",
      newExecutionApiKey(next, input.body),
      input.actorUserId,
      input.isAdmin === true,
    );
  }

  async resumeTask(input: {
    actorUserId: string;
    authHeaders: Record<string, string>;
    taskId: string;
    body: RepairResumeInput;
    isAdmin?: boolean;
  }): Promise<Record<string, unknown>> {
    let task = await this.ownedTask(input.actorUserId, input.taskId, input.isAdmin === true);
    let config = taskConfig(task);
    assertExecutionSupported(config);
    assertAgentSelectionEcho(config, input.body);
    await this.refreshTarget(input.actorUserId, task, config, "resume", false, input.isAdmin === true);
    const refreshedTask = await this.deps.repo.findTask(task.task_id);
    if (!refreshedTask || refreshedTask.task_type !== "repair") {
      repairNotFound("repair_task_not_found", "Repair Task 不存在");
    }
    task = refreshedTask;
    config = taskConfig(task);
    if (task.status === "waiting_context") {
      return this.startNewExecution(
        task,
        config,
        config.current.phase,
        newExecutionApiKey(config, input.body),
        input.actorUserId,
        input.isAdmin === true,
      );
    }
    if ((task.status === "waiting_approval" || task.status === "waiting_acceptance")
      && config.pendingDecision && !this.executionCanClaim(config)) {
      return this.startNewExecution(
        task,
        config,
        this.phaseForDecision(config.pendingDecision),
        newExecutionApiKey(config, input.body),
        input.actorUserId,
        input.isAdmin === true,
      );
    }
    if (task.status === "failed" && config.current.phase === "repair_plan") {
      const failedStep = await this.currentStep(task, config);
      if (failedPlanCanResume(task, config, failedStep)) {
        if (this.executionCanClaim(config) && config.execution.jobId != null) {
          return this.retryFailedPlanInCurrentExecution(
            task,
            config,
            failedStep,
            input.actorUserId,
            input.isAdmin === true,
          );
        }
        return this.startNewExecution(
          task,
          config,
          "repair_plan",
          newExecutionApiKey(config, input.body),
          input.actorUserId,
          input.isAdmin === true,
        );
      }
    }
    throw new RepairError(409, "repair_not_resumable", "Repair Task 当前不需要拉起新容器");
  }

  async bootstrap(identity: RepairWorkloadIdentity): Promise<Record<string, unknown>> {
    const { context, config } = await this.activeWorkloadContext(identity);
    assertExecutionSupported(config);
    const recoveryCheckpoint = await this.loadRecoveryCheckpoint(config);
    const approvedPlan = context.phase === "repair_apply" && config.approvedPlan
      ? {
        ...config.approvedPlan,
        actionIds: (await this.loadApprovedPlan(config)).actions.map((action) => action.actionId),
      }
      : config.approvedPlan;
    const recoveryContext = await this.recoveryContext(identity, context, config, approvedPlan);
    let insightPlanSource: unknown = null;
    if (config.insightSource && this.deps.insightBridge && context.phase === "repair_plan") {
      try {
        insightPlanSource = projectInsightPlanSourceForRepair(
          await this.deps.insightBridge.resolvePlanSource(config.taskId),
        );
      } catch {
        insightPlanSource = unavailableInsightPlanSource("source_unavailable");
      }
    }
    let result: Record<string, unknown> = {
      ...context,
      executionId: identity.executionId,
      history: agentVisibleHistory(config),
      investigationRequirements: currentInvestigationRequirements(config),
      ...(config.insightSource ? { insightSource: config.insightSource } : {}),
      ...(insightPlanSource ? { insightPlanSource } : {}),
      approvedPlan,
      recoveryCheckpoint,
      recoveryContext,
      normalizedTimeRange: {
        fromUnixSeconds: context.issue.timeRange.from,
        toUnixSeconds: context.issue.timeRange.to,
        fromIsoUtc: new Date(context.issue.timeRange.from * 1_000).toISOString(),
        toIsoUtc: new Date(context.issue.timeRange.to * 1_000).toISOString(),
        queryUsesUnixSeconds: true,
      },
      timings: {
        heartbeatSeconds: this.deps.config.heartbeatIntervalSeconds,
        contextWaitSeconds: this.deps.config.contextWaitSeconds,
        decisionGraceSeconds: this.deps.config.decisionGraceSeconds,
        agentTimeoutSeconds: this.deps.config.agentTimeoutSeconds,
        agentCloseoutTimeoutSeconds: this.deps.config.agentCloseoutTimeoutSeconds,
        maxAgentAutoRecoveries: this.deps.config.maxAgentAutoRecoveries,
        agentCorrectionTimeoutSeconds: this.deps.config.agentCorrectionTimeoutSeconds,
        maxAgentOutputCorrectionRetries: this.deps.config.maxAgentOutputCorrectionRetries,
        maxAgentRateLimitRetries: this.deps.config.maxAgentRateLimitRetries,
        agentRateLimitRetryBaseSeconds: this.deps.config.agentRateLimitRetryBaseSeconds,
      },
      tools: {
        logs: true,
        ocbRead: [],
        ocbApply: [...WRITE_OCB_OPERATIONS],
        runtimeRead: [
          "fs_list", "fs_find", "fs_stat", "fs_read", "fs_search", "process_list", "port_list", "http_get",
          ...(config.diagnosticMode === "deep" ? ["shell_exec"] : []),
        ],
        applyAction: context.phase === "repair_apply",
        filesystemScope: "container_user_readable",
        resultEvidenceLocators: "verified_result_v1",
        shellObservedLocators: "unverified_confirm_v1",
        rawShell: config.diagnosticMode === "deep",
        rawHttp: false,
      },
    };
    if (insightPlanSource) {
      try {
        assertRepairAuditPersistable({
          status: "succeeded",
          result,
          error: null,
          downstreamTraceId: null,
        }, "terminalEnvelope");
      } catch {
        insightPlanSource = unavailableInsightPlanSource("unsafe_projection");
        result = { ...result, insightPlanSource };
      }
    }
    // The audit call is intentionally idempotent and keeps its first result,
    // while bootstrap itself must always expose the live target/context.
    await this.recordFact(identity, "bootstrap", `bootstrap:${identity.executionId}:${identity.stepId}`, {}, result);
    return result;
  }

  async refreshArtifactUpload(
    identity: RepairWorkloadIdentity,
    input: RepairArtifactRefreshInput,
  ): Promise<Record<string, unknown>> {
    const { config } = await this.activeWorkloadContext(identity);
    const artifactName = requiredText(input.artifactName, "artifactName", 64);
    const canonicalArtifacts = artifactsFor(config.taskId, config.current.stepId, config.current.phase);
    const canonical = canonicalArtifacts[artifactName];
    if (!canonical) {
      repairValidation("invalid_repair_artifact_name", "artifactName 不属于当前 Repair Step");
    }
    const current = config.current.artifacts[artifactName];
    const persisted = config.artifacts[artifactName];
    const contentType = canonical.contentType ?? repairArtifactContentType(artifactName);
    if (!current || !persisted
      || current.objectKey !== canonical.objectKey
      || persisted.objectKey !== canonical.objectKey
      || (current.contentType ?? contentType) !== contentType
      || (persisted.contentType ?? contentType) !== contentType) {
      throw new RepairError(
        500,
        "invalid_repair_task_config",
        "Repair artifact 配置与当前 Task/Step 不一致",
      );
    }
    const expiresInSeconds = 86_400;
    const putUrl = await this.deps.store.createSignedUrl(
      canonical.objectKey,
      "PUT",
      expiresInSeconds,
      { "Content-Type": contentType },
    );
    console.info(
      `[repair] artifact_upload_target_refreshed taskId=${config.taskId}`
      + ` stepId=${config.current.stepId} artifactName=${artifactName}`
      + ` expiresInSeconds=${expiresInSeconds} contentType=${contentType}`,
    );
    return {
      artifact: {
        name: artifactName,
        objectKey: canonical.objectKey,
        contentType,
        putUrl,
      },
      expiresInSeconds,
    };
  }

  async heartbeat(identity: RepairWorkloadIdentity, body: { ccSessionId?: unknown }): Promise<Record<string, unknown>> {
    const { task, config } = await this.credentialWorkloadContext(identity);
    const now = this.now();
    if (config.execution.state === "ended" || config.execution.invalidatedAt != null) {
      throw new RepairError(409, "repair_execution_ended", "Repair execution 已结束");
    }
    const ccSessionId = body.ccSessionId == null || body.ccSessionId === ""
      ? config.execution.ccSessionId
      : requiredText(body.ccSessionId, "ccSessionId", 255);
    const next: RepairTaskConfig = {
      ...config,
      execution: {
        ...config.execution,
        ccSessionId,
        state: config.execution.state === "dispatching" ? "running" : config.execution.state,
        leaseExpiresAt: now + this.deps.config.executionLeaseSeconds,
        lastHeartbeatAt: now,
      },
    };
    const updated = await this.deps.repairRepo.compareAndSetTaskConfig({
      taskId: task.task_id,
      expectedTaskStatuses: [task.status],
      expectedCurrentStepId: config.current.stepId,
      expectedTaskConfigDigest: storedConfigDigest(task.config_json),
      nextConfig: next,
    });
    if (!updated) {
      throw new RepairError(409, "repair_heartbeat_conflict", "Repair execution 状态已变化，请重试 heartbeat");
    }
    return { ok: true, leaseExpiresAt: next.execution.leaseExpiresAt, state: next.execution.state };
  }

  async claimDecision(identity: RepairWorkloadIdentity): Promise<Record<string, unknown>> {
    const { task, step, config } = await this.credentialWorkloadContext(identity);
    assertExecutionSupported(config);
    if (!config.pendingDecision) {
      if (identity.requestedStepId && identity.requestedStepId !== identity.stepId) {
        if (!this.canRecoverDecisionClaimAlias(identity, task, step, config)) {
          throw new RepairError(403, "repair_decision_claim_alias_invalid", "Repair decision claim 恢复路径已失效");
        }
        const phase = config.current.phase;
        const decisionKind = this.recoveredDecisionKind(config);
        const continuation = await this.continuationEnvelope(config);
        await this.recordFact(
          identity,
          "decision_claim",
          `decision:${config.execution.executionId}:${config.current.stepId}`,
          { kind: decisionKind },
          { reusedJob: true, phase },
        );
        return {
          status: "claimed",
          reusedJob: true,
          stepId: config.current.stepId,
          phase,
          continuation,
        };
      }
      return { status: "waiting", decisionDeadlineAt: config.execution.decisionDeadlineAt };
    }
    if (!this.executionCanClaim(config) || step.status !== "succeeded") {
      return { status: "new_job_required" };
    }
    if (!config.execution.jobId) {
      return { status: "waiting", decisionDeadlineAt: config.execution.decisionDeadlineAt };
    }
    const phase = this.phaseForDecision(config.pendingDecision);
    const next = this.nextStepConfig(task, config, phase, config.pendingDecision.feedback, config.approvedPlan, false);
    if (next.history.length) {
      next.history[next.history.length - 1].artifactDigest = optionalArtifactDigest(parseOutput(step).artifactDigest);
    }
    next.execution = {
      ...config.execution,
      state: "running",
      stepId: next.current.stepId,
      phase,
      leaseExpiresAt: this.now() + this.deps.config.executionLeaseSeconds,
      decisionDeadlineAt: null,
      lastHeartbeatAt: this.now(),
    };
    next.pendingDecision = null;
    const transitioned = await this.deps.repairRepo.transitionStep({
      taskId: task.task_id,
      expectedTaskStatuses: [task.status],
      expectedCurrentStepId: step.step_id,
      expectedTaskConfigDigest: storedConfigDigest(task.config_json),
      previousStep: {
        stepId: step.step_id,
        expectedStatuses: ["succeeded"],
        status: "succeeded",
      },
      nextTaskStatus: "running",
      nextConfig: next,
      nextStep: {
        stepId: next.current.stepId,
        stepType: phase,
        stepNo: next.current.stepNo,
        command: phase,
      },
      reuseJobId: config.execution.jobId,
    });
    if (transitioned.outcome !== "transitioned") {
      throw new RepairError(409, "repair_decision_claim_conflict", `Repair decision claim 冲突: ${transitioned.reason}`);
    }
    const continuation = await this.continuationEnvelope(next);
    await this.recordFact(
      { ...identity, stepId: next.current.stepId },
      "decision_claim",
      `decision:${config.execution.executionId}:${next.current.stepId}`,
      { kind: config.pendingDecision.kind },
      { reusedJob: true, phase },
    );
    return { status: "claimed", reusedJob: true, stepId: next.current.stepId, phase, continuation };
  }

  async reportStep(
    identity: RepairWorkloadIdentity,
    body: {
      status?: unknown;
      output?: unknown;
      error?: unknown;
      summary?: unknown;
      toolCallId?: unknown;
      retryWaitSupported?: unknown;
    },
  ): Promise<Record<string, unknown>> {
    const { task, step, config } = await this.credentialWorkloadContext(identity);
    const status = requiredText(body.status, "status", 32).toLowerCase();
    if (!new Set(["running", "succeeded", "failed", "waiting_context"]).has(status)) {
      repairValidation("invalid_executor_status", "status 必须是 running/succeeded/failed/waiting_context");
    }
    if (TERMINAL_STEP_STATUSES.has(step.status)) {
      const expected = status === "waiting_context" ? "interrupted" : status;
      if (step.status === expected) return {
        ok: true,
        duplicate: true,
        taskId: task.task_id,
        stepId: step.step_id,
        status,
        ...(status === "failed"
          && config.current.phase === "repair_plan"
          && this.executionCanClaim(config)
          ? {
            retryWaitSupported: true,
            decisionDeadlineAt: config.execution.decisionDeadlineAt,
          }
          : {}),
      };
      throw new RepairError(409, "repair_step_already_terminal", `Step 已处于终态: ${step.status}`);
    }
    if (status === "succeeded") {
      await this.assertRequiredSemanticConclusions(identity, config.authorizationScopeDigest);
    }
    const auditId = `report:${status}:${randomUUID()}`;
    const audit = await this.beginToolCall(identity, {
      clientRequestId: auditId,
      toolName: "step_report",
      operation: status,
      request: { status },
      isWrite: false,
    });
    if (!audit.created && audit.call.status === "succeeded") {
      return { ok: true, duplicate: true, taskId: task.task_id, stepId: step.step_id, status };
    }
    const claimed = await this.claimForServer(audit.call);
    if (!claimed) {
      throw new RepairError(409, "repair_report_audit_unavailable", "Repair Step report 审计调用状态未知");
    }
    try {
      let nextTaskStatus: string;
      let nextConfig: RepairTaskConfig;
      let previousStep: Parameters<RepairRepository["transitionStep"]>[0]["previousStep"];
      let validatedApplyResult: RepairApplyResultArtifact | null = null;
      let retryWaitDeadlineAt: number | null = null;
      if (status === "running") {
        const now = this.now();
        const recovery = repairRecoveryProgress(body.output);
        nextTaskStatus = "running";
        nextConfig = {
          ...config,
          execution: {
            ...config.execution,
            state: "running",
            leaseExpiresAt: now + this.deps.config.executionLeaseSeconds,
            lastHeartbeatAt: now,
          },
        };
        previousStep = {
          stepId: step.step_id,
          expectedStatuses: ["created", "dispatched", "running"],
          status: "running",
          summary: typeof body.summary === "string" ? redactText(body.summary, 2_000) : undefined,
          ...(recovery == null ? {} : { output: { recovery } }),
        };
      } else if (status === "waiting_context") {
        await this.cancelExpiredContextCall(task.task_id, body.toolCallId);
        const now = this.now();
        nextTaskStatus = "waiting_context";
        nextConfig = {
          ...config,
          execution: {
            ...config.execution,
            state: "ended",
            invalidatedAt: now,
            leaseExpiresAt: now,
            decisionDeadlineAt: null,
          },
        };
        previousStep = {
          stepId: step.step_id,
          expectedStatuses: ["created", "dispatched", "running"],
          status: "interrupted",
          errorCode: "REPAIR_CONTEXT_TIMEOUT",
          errorMessage: "等待浏览器补充运行态或 OCB context 超时，可恢复",
          retryable: true,
          output: body.output && typeof body.output === "object" && !Array.isArray(body.output)
            ? body.output as Record<string, unknown> : undefined,
        };
      } else if (status === "failed") {
        const error = body.error && typeof body.error === "object" && !Array.isArray(body.error)
          ? body.error as Record<string, unknown> : {};
        const message = typeof error.message === "string"
          ? redactPersistableText(error.message, 2_000) : "Repair AIS 执行失败";
        const failure = repairStepFailureMetadata(error);
        const now = this.now();
        const retryWaitSupported = config.current.phase === "repair_plan"
          && body.retryWaitSupported === true;
        retryWaitDeadlineAt = retryWaitSupported
          ? now + this.deps.config.decisionGraceSeconds
          : null;
        nextTaskStatus = "failed";
        nextConfig = {
          ...config,
          execution: {
            ...config.execution,
            state: retryWaitSupported ? "waiting_decision" : "ended",
            invalidatedAt: retryWaitSupported ? null : now,
            leaseExpiresAt: retryWaitDeadlineAt ?? now,
            decisionDeadlineAt: retryWaitDeadlineAt,
            lastHeartbeatAt: retryWaitSupported ? now : config.execution.lastHeartbeatAt,
          },
        };
        previousStep = {
          stepId: step.step_id,
          expectedStatuses: ["created", "dispatched", "running"],
          status: "failed",
          errorCode: repairStepFailureCode(error.code),
          errorMessage: message,
          retryable: typeof error.retryable === "boolean" ? error.retryable : false,
          ...(failure == null ? {} : { output: { failure } }),
        };
      } else {
        const output = executorOutput(body.output);
        this.validateExecutorOutput(output, config);
        const primaryDigest = artifactDigest(output.artifactDigest);
        const validatedPlan = config.current.phase === "repair_plan"
          ? await this.loadAndValidatePlan(config, primaryDigest)
          : null;
        validatedApplyResult = config.current.phase === "repair_apply"
          ? await this.loadAndValidateApplyResult(config, primaryDigest)
          : null;
        const now = this.now();
        const isPlan = config.current.phase === "repair_plan";
        nextTaskStatus = isPlan ? "waiting_approval" : "waiting_acceptance";
        nextConfig = {
          ...config,
          execution: {
            ...config.execution,
            state: "waiting_decision",
            leaseExpiresAt: now + this.deps.config.executionLeaseSeconds,
            decisionDeadlineAt: now + this.deps.config.decisionGraceSeconds,
            invalidatedAt: null,
            lastHeartbeatAt: now,
          },
        };
        previousStep = {
          stepId: step.step_id,
          expectedStatuses: ["created", "dispatched", "running"],
          status: "succeeded",
          summary: validatedPlan && isRepairPlanV2(validatedPlan)
            ? redactText(validatedPlan.recommendation.summary, 2_000)
            : validatedApplyResult
              ? redactText(validatedApplyResult.summary, 2_000)
            : typeof output.summary === "string"
              ? redactText(output.summary, 2_000)
              : "Repair AIS Step 执行成功",
          output: output as Record<string, unknown>,
        };
      }
      const transition = () => this.deps.repairRepo.transitionStep({
        taskId: task.task_id,
        expectedTaskStatuses: ["pending", "running"],
        expectedCurrentStepId: step.step_id,
        expectedTaskConfigDigest: storedConfigDigest(task.config_json),
        previousStep,
        nextTaskStatus,
        nextConfig,
        ignoreActiveToolCallId: audit.call.callId,
        toolCallLedgerGuard: status === "succeeded"
          ? (calls) => {
            this.assertRequiredSemanticConclusionsFromLedger(
              calls,
              identity,
              config.authorizationScopeDigest,
            );
            if (validatedApplyResult) {
              assertApplyResultMatchesLedger(validatedApplyResult, config, calls);
            }
          }
          : undefined,
      });
      if (status === "failed") {
        await this.finishAbandonedStepCalls(task.task_id, step.step_id, audit.call.callId);
      }
      let transitioned = await transition();
      if (status === "failed"
        && transitioned.outcome === "conflict"
        && transitioned.reason === "active_tool_calls") {
        await this.finishAbandonedStepCalls(task.task_id, step.step_id, audit.call.callId);
        transitioned = await transition();
      }
      if (transitioned.outcome !== "transitioned") {
        throw new RepairError(409, "repair_step_transition_conflict", `Repair Step 状态切换冲突: ${transitioned.reason}`);
      }
      await this.finishToolCall(claimed, "succeeded", { ok: true, status });
      return {
        ok: true,
        duplicate: false,
        taskId: task.task_id,
        stepId: step.step_id,
        status,
        ...(retryWaitDeadlineAt == null ? {} : {
          retryWaitSupported: true,
          decisionDeadlineAt: retryWaitDeadlineAt,
        }),
      };
    } catch (error) {
      await this.finishToolCall(claimed, "failed", null, error);
      throw error;
    }
  }

  async searchLogs(identity: RepairWorkloadIdentity, input: RepairLogSearchInput): Promise<Record<string, unknown>> {
    const { context, config } = await this.activeWorkloadContext(identity);
    const verifiedDiscovered = await this.verifyDiscoveredIdentifiers(
      identity.taskId,
      config.authorizationScopeDigest,
      input.discoveredIdentifiers ?? [],
    );
    const requestId = clientRequestId(input.clientRequestId, `logs:${randomUUID()}`);
    const request = { ...input };
    delete request.clientRequestId;
    delete request.purpose;
    return this.runServerTool(identity, {
      clientRequestId: requestId,
      toolName: "antlogs",
      operation: "search",
      purpose: input.purpose,
      request,
      isWrite: false,
    }, () => this.deps.logs.search(context, request, verifiedDiscovered));
  }

  async inspectRuntime(identity: RepairWorkloadIdentity, input: RepairRuntimeInspectInput): Promise<Record<string, unknown>> {
    const { context, config } = await this.activeWorkloadContext(identity);
    if (input.operation === "shell_exec" && config.diagnosticMode !== "deep") {
      repairForbidden("repair_diagnostic_shell_not_authorized", "当前 Repair Task 未授权目标 Bot 深度诊断 Shell");
    }
    const requestId = clientRequestId(input.clientRequestId, `runtime:${randomUUID()}`);
    const request = { ...input };
    delete request.clientRequestId;
    delete request.purpose;
    if (context.target.provider === "arca") {
      const created = await this.beginToolCall(identity, {
        clientRequestId: requestId,
        toolName: "arca_read",
        operation: input.operation,
        purpose: input.purpose,
        request,
        isWrite: false,
        deadlineAt: this.now() + this.deps.config.contextWaitSeconds,
      });
      return publicToolCall(created.call);
    }
    return this.runServerTool(identity, {
      clientRequestId: requestId,
      toolName: "baas_read",
      operation: input.operation,
      purpose: input.purpose,
      request,
      isWrite: false,
    }, () => this.deps.runtimeTool.inspect(context, request as RepairRuntimeInspectInput));
  }

  async applyAction(identity: RepairWorkloadIdentity, input: RepairApplyActionInput): Promise<Record<string, unknown>> {
    const { context, config, task } = await this.activeWorkloadContext(identity);
    if (context.phase !== "repair_apply" || task.status !== "running") {
      throw new RepairError(409, "repair_apply_not_active", "当前不是活动的 repair_apply Step");
    }
    const actionId = requiredText(input.actionId, "actionId", 128);
    const plan = await this.loadApprovedPlan(config);
    const action = plan.actions.find((item) => item.actionId === actionId);
    if (!action) repairNotFound("repair_action_not_approved", "actionId 不在当前获批方案内");
    if (action.type !== "container_command") {
      repairValidation("repair_action_wrong_tool", "OCB action 必须通过 OCB relay 执行");
    }
    await this.assertDependenciesSatisfied(task.task_id, context.stepId, action);
    const requestId = clientRequestId(input.clientRequestId, `baas-action:${randomUUID()}`);
    await this.assertExplicitRetry(task.task_id, context.stepId, actionId, boolean(input.retry));
    if (context.target.provider === "arca") {
      const created = await this.beginToolCall(identity, {
        clientRequestId: requestId,
        toolName: "arca_write",
        operation: `apply_action:${actionId}`,
        purpose: input.purpose,
        actionId,
        request: { actionId, retry: boolean(input.retry), planDigest: config.approvedPlan?.artifactDigest },
        isWrite: true,
        deadlineAt: this.now() + this.deps.config.contextWaitSeconds,
      });
      return publicToolCall(created.call);
    }
    return this.runServerTool(identity, {
      clientRequestId: requestId,
      toolName: "baas_write",
      operation: `apply_action:${actionId}`,
      purpose: input.purpose,
      actionId,
      request: { actionId, retry: boolean(input.retry), planDigest: config.approvedPlan?.artifactDigest },
      isWrite: true,
    }, () => this.deps.runtimeTool.applyApprovedAction(context, action));
  }

  async requestOcbOperation(identity: RepairWorkloadIdentity, input: RepairOcbContextInput): Promise<Record<string, unknown>> {
    const { config, context, task } = await this.activeWorkloadContext(identity);
    const requestId = clientRequestId(input.clientRequestId, `ocb:${randomUUID()}`);
    if (context.phase !== "repair_apply" || task.status !== "running") {
      throw new RepairError(409, "repair_apply_not_active", "Bot 重启只允许在活动 Apply Step 中执行");
    }
    const actionId = requiredText(input.actionId, "actionId", 128);
    const plan = await this.loadApprovedPlan(config);
    const action = plan.actions.find((item) => item.actionId === actionId);
    if (!action || action.type !== "ocb_operation" || !action.operation) {
      repairNotFound("repair_action_not_approved", "actionId 不是获批的 Bot 重启 action");
    }
    const parsedOperation = parseOcbRepairOperation(action.operation);
    await this.assertDependenciesSatisfied(task.task_id, context.stepId, action);
    const existing = await this.deps.repairRepo.findToolCallByClientRequestId(context.stepId, requestId);
    if (!existing) {
      await this.assertExplicitRetry(task.task_id, context.stepId, actionId, boolean(input.retry));
    }
    const created = await this.beginToolCall(identity, {
      clientRequestId: requestId,
      toolName: "ocb_write",
      operation: parsedOperation.type,
      purpose: input.purpose,
      actionId,
      request: {
        operation: parsedOperation.type,
        params: parsedOperation.params ?? {},
        actionId,
        retry: boolean(input.retry),
      },
      isWrite: true,
      deadlineAt: this.now() + this.deps.config.contextWaitSeconds,
    });
    return publicToolCall(created.call);
  }

  async getToolCall(identity: RepairWorkloadIdentity, callId: string): Promise<Record<string, unknown>> {
    await this.credentialWorkloadContext(identity);
    const call = await this.deps.repairRepo.findToolCall(requiredText(callId, "toolCallId", 64));
    if (!call || call.taskId !== identity.taskId || call.stepId !== identity.stepId || call.executionId !== identity.executionId) {
      repairNotFound("repair_tool_call_not_found", "Repair tool call 不存在");
    }
    return publicToolCall(call);
  }

  async recordSemanticConclusion(
    identity: RepairWorkloadIdentity,
    input: RepairSemanticConclusionInput,
  ): Promise<Record<string, unknown>> {
    const { config } = await this.credentialWorkloadContext(identity);
    const sourceToolCallId = requiredText(input.sourceToolCallId, "sourceToolCallId", 64);
    const source = await this.deps.repairRepo.findToolCall(sourceToolCallId);
    if (!source
      || source.taskId !== identity.taskId
      || source.stepId !== identity.stepId
      || source.executionId !== identity.executionId
      || source.authorizationScopeDigest !== config.authorizationScopeDigest
      || !BUSINESS_AUDIT_TOOLS.has(source.toolName)) {
      repairNotFound("repair_conclusion_source_not_found", "结论引用的源工具调用不存在");
    }
    if (!TERMINAL_TOOL_STATUSES.has(source.status) || !source.resultDigest) {
      throw new RepairError(409, "repair_conclusion_source_not_terminal", "只能为已结束的工具调用记录结论");
    }
    if (!Array.isArray(input.evidenceToolCallIds) || input.evidenceToolCallIds.length < 1
      || input.evidenceToolCallIds.length > 20) {
      repairValidation("invalid_repair_conclusion_evidence", "evidenceToolCallIds 必须包含 1 到 20 个工具调用");
    }
    const evidenceToolCallIds = [...new Set(input.evidenceToolCallIds.map((value) =>
      requiredText(value, "evidenceToolCallId", 64)))];
    if (!evidenceToolCallIds.includes(sourceToolCallId)) {
      repairValidation("invalid_repair_conclusion_evidence", "evidenceToolCallIds 必须包含 sourceToolCallId");
    }
    const evidenceResultDigests: Record<string, string> = {};
    for (const evidenceToolCallId of evidenceToolCallIds) {
      const call = evidenceToolCallId === sourceToolCallId
        ? source
        : await this.deps.repairRepo.findToolCall(evidenceToolCallId);
      if (!call
        || call.taskId !== identity.taskId
        || call.stepId !== identity.stepId
        || call.executionId !== identity.executionId
        || call.authorizationScopeDigest !== config.authorizationScopeDigest
        || !TERMINAL_TOOL_STATUSES.has(call.status)
        || !call.resultDigest
        || (call.toolName === "repair_control" && call.operation === "record_conclusion")) {
        throw new RepairError(409, "invalid_repair_conclusion_evidence", "结论引用了无效或越界的证据调用");
      }
      evidenceResultDigests[call.callId] = call.resultDigest;
    }
    const conclusionZh = auditText(input.conclusionZh, "conclusionZh", 1_000);
    const nextAction = input.nextAction == null
      ? null
      : auditText(input.nextAction, "nextAction", 500);
    try {
      return await this.runServerTool(identity, {
        clientRequestId: `conclusion:${sourceToolCallId}`,
        toolName: "repair_control",
        operation: "record_conclusion",
        request: {
          sourceToolCallId,
          sourceResultDigest: source.resultDigest,
          evidenceToolCallIds,
          evidenceResultDigests,
          conclusionZh,
          ...(nextAction == null ? {} : { nextAction }),
        },
        isWrite: false,
      }, async () => ({ recorded: true, sourceToolCallId }));
    } catch (error) {
      if (error instanceof RepairToolCallIdempotencyConflictError) {
        throw new RepairError(409, "repair_conclusion_already_recorded", "该工具调用已记录不可变结论");
      }
      throw error;
    }
  }

  async systemCloseSemanticConclusion(
    identity: RepairWorkloadIdentity,
    input: { sourceToolCallId?: unknown },
  ): Promise<Record<string, unknown>> {
    const { config } = await this.credentialWorkloadContext(identity);
    const sourceToolCallId = requiredText(input.sourceToolCallId, "sourceToolCallId", 64);
    const [sources, records] = await Promise.all([
      this.listStepAuditCalls(identity.taskId, identity.stepId, "source"),
      this.listStepAuditCalls(identity.taskId, identity.stepId, "conclusion"),
    ]);
    const requiredSources = sources
      .filter((call) => call.executionId === identity.executionId
        && call.authorizationScopeDigest === config.authorizationScopeDigest
        && BUSINESS_AUDIT_TOOLS.has(call.toolName)
        && unpackToolRequest(call.request).semanticConclusionRequired)
      .sort((left, right) => left.id - right.id);
    const requestedSource = requiredSources.find((call) => call.callId === sourceToolCallId);
    if (!requestedSource) {
      repairNotFound("repair_conclusion_source_not_found", "系统收口引用的源工具调用不存在");
    }
    if (!TERMINAL_TOOL_STATUSES.has(requestedSource.status) || !requestedSource.resultDigest) {
      throw new RepairError(409, "repair_conclusion_source_not_terminal", "系统收口只允许处理已结束的工具调用");
    }
    if (requiredSources.some((call) => !TERMINAL_TOOL_STATUSES.has(call.status))) {
      throw new RepairError(409, "repair_system_closeout_unsafe", "仍有未结束的业务调用，不能执行系统收口");
    }
    const sourceById = new Map(requiredSources.map((call) => [call.callId, call]));
    const completed = new Set<string>();
    for (const record of records) {
      if (record.executionId !== identity.executionId
        || record.authorizationScopeDigest !== config.authorizationScopeDigest) continue;
      const parsed = semanticConclusion(record);
      if (!parsed) continue;
      const source = sourceById.get(parsed.sourceToolCallId);
      if (source && record.id > source.id && source.resultDigest === parsed.sourceResultDigest) {
        completed.add(source.callId);
      }
    }
    const missing = requiredSources.filter((call) => !completed.has(call.callId));
    if (missing.length !== 1
      || missing[0].callId !== sourceToolCallId
      || requiredSources.at(-1)?.callId !== sourceToolCallId) {
      throw new RepairError(
        409,
        "repair_system_closeout_unsafe",
        "系统收口只允许关闭当前 execution 唯一且位于尾部的审计缺口",
      );
    }
    const conclusionZh = "调用已终态保存，但 Agent 未追加业务语义结论；本记录仅关闭审计缺口，不确认根因、方案或验证结果。";
    try {
      return await this.runServerTool(identity, {
        clientRequestId: `conclusion:${sourceToolCallId}`,
        toolName: "repair_control",
        operation: "record_conclusion",
        request: {
          sourceToolCallId,
          sourceResultDigest: requestedSource.resultDigest,
          evidenceToolCallIds: [sourceToolCallId],
          evidenceResultDigests: { [sourceToolCallId]: requestedSource.resultDigest },
          conclusionZh,
          systemGenerated: true,
        },
        isWrite: false,
      }, async () => ({ recorded: true, sourceToolCallId, systemGenerated: true }));
    } catch (error) {
      if (error instanceof RepairToolCallIdempotencyConflictError) {
        await this.assertRequiredSemanticConclusions(identity, config.authorizationScopeDigest);
        return { recorded: true, sourceToolCallId, systemGenerated: false, duplicate: true };
      }
      throw error;
    }
  }

  async requestCfuseLogin(
    identity: RepairWorkloadIdentity,
    input: RepairCfuseLoginInput,
  ): Promise<Record<string, unknown>> {
    const { config } = await this.activeWorkloadContext(identity);
    if (config.agentMode !== "cfuse") {
      throw new RepairError(409, "repair_cfuse_login_not_enabled", "当前 Repair Task 未选择 cfuse");
    }
    const requestId = clientRequestId(input.clientRequestId, `cfuse-login:${randomUUID()}`);
    const loginUrl = cfuseLoginUrl(input.loginUrl);
    const created = await this.beginToolCall(identity, {
      clientRequestId: requestId,
      toolName: "cfuse_login",
      operation: "authorize",
      request: { loginUrl },
      isWrite: false,
      deadlineAt: this.now() + this.deps.config.contextWaitSeconds,
    });
    return publicToolCall(created.call);
  }

  async submitCfuseAuthCode(input: {
    actorUserId: string;
    taskId: string;
    toolCallId: string;
    body: RepairCfuseAuthCodeInput;
    isAdmin?: boolean;
  }): Promise<Record<string, unknown>> {
    const task = await this.ownedTask(input.actorUserId, input.taskId, input.isAdmin === true);
    const config = taskConfig(task);
    assertExecutionSupported(config);
    const call = await this.currentCfuseLoginCall(config, input.toolCallId);
    const now = this.now();
    if (call.deadlineAt == null || call.deadlineAt <= now) {
      this.cfuseAuthCodes.delete(call.callId);
      if (call.status === "pending" || call.status === "executing") {
        await this.finishExpiredToolCall(call, "cfuse_login_timeout").catch(() => null);
      }
      throw new RepairError(409, "repair_cfuse_login_expired", "cfuse 登录授权已过期");
    }
    if (call.status !== "pending" && call.status !== "executing") {
      throw new RepairError(409, "repair_cfuse_login_not_pending", "cfuse 登录授权已结束");
    }
    const authCode = cfuseAuthCode(input.body.authCode);
    const existing = this.cfuseAuthCodes.get(call.callId);
    if (existing) {
      this.assertCfuseSlot(existing, call);
      return browserToolCall(call, { canViewDetails: true, canExecute: true });
    }
    const claimed = await this.deps.repairRepo.claimToolCall({
      callId: call.callId,
      executionId: call.executionId,
      authorizationScopeDigest: call.authorizationScopeDigest,
      leaseOwner: this.cfuseLeaseOwner(call.executionId),
      leaseExpiresAt: call.deadlineAt,
      now,
    });
    if (!claimed) {
      throw new RepairError(409, "repair_cfuse_login_claim_conflict", "cfuse 登录授权状态已变化");
    }
    const raced = this.cfuseAuthCodes.get(call.callId);
    if (raced) {
      this.assertCfuseSlot(raced, claimed);
      return browserToolCall(claimed, { canViewDetails: true, canExecute: true });
    }
    this.cfuseAuthCodes.set(call.callId, {
      taskId: call.taskId,
      stepId: call.stepId,
      executionId: call.executionId,
      authorizationScopeDigest: call.authorizationScopeDigest,
      expiresAt: call.deadlineAt,
      state: "available",
      authCode,
    });
    return browserToolCall(claimed, { canViewDetails: true, canExecute: true });
  }

  async takeCfuseAuthCode(
    identity: RepairWorkloadIdentity,
    toolCallId: string,
  ): Promise<Record<string, unknown>> {
    const { config } = await this.credentialWorkloadContext(identity);
    assertExecutionSupported(config);
    const call = await this.currentCfuseLoginCall(config, toolCallId);
    if (call.deadlineAt == null || call.deadlineAt <= this.now()) {
      this.cfuseAuthCodes.delete(call.callId);
      if (call.status === "pending" || call.status === "executing") {
        await this.finishExpiredToolCall(call, "cfuse_login_timeout").catch(() => null);
      }
      return { status: "expired", toolCallId: call.callId };
    }
    const slot = this.cfuseAuthCodes.get(call.callId);
    if (!slot) {
      return {
        status: call.status === "pending" ? "waiting" : "already_taken",
        toolCallId: call.callId,
      };
    }
    this.assertCfuseSlot(slot, call);
    if (slot.state === "taken" || slot.authCode == null) {
      return { status: "already_taken", toolCallId: call.callId };
    }
    const authCode = slot.authCode;
    slot.authCode = null;
    slot.state = "taken";
    return { status: "available", toolCallId: call.callId, authCode };
  }

  async reportCfuseLogin(
    identity: RepairWorkloadIdentity,
    toolCallId: string,
    input: RepairCfuseLoginReportInput,
  ): Promise<Record<string, unknown>> {
    const { config } = await this.credentialWorkloadContext(identity);
    const call = await this.currentCfuseLoginCall(config, toolCallId);
    if (call.status === "pending") {
      throw new RepairError(409, "repair_cfuse_login_not_claimed", "cfuse AuthCode 尚未提交");
    }
    const status = cfuseLoginReportStatus(input.status);
    const errorCode = status === "failed" ? optionalText(input.errorCode, "errorCode", 128) : null;
    const rawErrorMessage = status === "failed"
      ? optionalText(input.errorMessage, "errorMessage", 4_000)
      : null;
    const completed = await this.deps.repairRepo.completeToolCall({
      callId: call.callId,
      executionId: call.executionId,
      authorizationScopeDigest: call.authorizationScopeDigest,
      leaseOwner: this.cfuseLeaseOwner(call.executionId),
      status,
      result: { loginStatus: status },
      errorCode,
      errorMessage: rawErrorMessage == null ? null : redactPersistableText(rawErrorMessage, 4_000),
      now: this.now(),
    });
    if (!completed) repairNotFound("repair_cfuse_login_not_found", "cfuse 登录授权不存在");
    this.cfuseAuthCodes.delete(call.callId);
    return publicToolCall(completed.call);
  }

  async fulfillToolCall(input: {
    actorUserId: string;
    authHeaders: Record<string, string>;
    taskId: string;
    toolCallId: string;
    isAdmin?: boolean;
  }): Promise<Record<string, unknown>> {
    const task = await this.ownedTask(input.actorUserId, input.taskId, input.isAdmin === true);
    let config = taskConfig(task);
    assertExecutionSupported(config);
    const call = await this.deps.repairRepo.findToolCall(requiredText(input.toolCallId, "toolCallId", 64));
    if (!call || call.taskId !== task.task_id || call.stepId !== config.current.stepId
      || call.executionId !== config.execution.executionId || !OWNER_BROWSER_RELAY_TOOLS.has(call.toolName)) {
      repairNotFound("repair_tool_call_not_found", "Repair browser relay tool call 不存在");
    }
    if (call.status !== "pending" && call.status !== "executing") {
      return browserToolCall(call, { canViewDetails: true, canExecute: true });
    }
    if (call.isWrite && call.status === "executing"
      && call.leaseExpiresAt != null && call.leaseExpiresAt <= this.now()) {
      const unknown = await this.finishExpiredToolCall(call, "execution_lease_expired");
      return browserToolCall(unknown, { canViewDetails: true, canExecute: true });
    }
    if (call.deadlineAt != null && call.deadlineAt <= this.now()) {
      await this.finishExpiredToolCall(call, "context_timeout").catch(() => null);
      throw new RepairError(409, "repair_tool_call_expired", "Repair browser relay tool call 已超过等待时间");
    }
    config = await this.refreshTarget(
      input.actorUserId,
      task,
      config,
      "browser_relay",
      false,
      input.isAdmin === true,
    );
    const leaseOwner = `browser-${randomUUID()}`;
    const claimed = await this.deps.repairRepo.claimToolCall({
      callId: call.callId,
      executionId: call.executionId,
      authorizationScopeDigest: call.authorizationScopeDigest,
      leaseOwner,
      leaseExpiresAt: this.now() + this.deps.config.executionLeaseSeconds,
      now: this.now(),
    });
    if (!claimed) {
      return browserToolCall((await this.deps.repairRepo.findToolCall(call.callId))!, {
        canViewDetails: true,
        canExecute: true,
      });
    }
    try {
      const requestEnvelope = unpackToolRequest(claimed.request);
      const request = requestEnvelope.payload;
      const requestObject = request && typeof request === "object" && !Array.isArray(request)
        ? request as Record<string, unknown>
        : {};
      if (claimed.toolName === "arca_read" || claimed.toolName === "arca_write") {
        if (requestEnvelope.targetVersion == null
          || requestEnvelope.targetVersion !== config.runtimeTarget.version) {
          throw new RepairError(
            409,
            "repair_runtime_target_changed",
            "ARCA 运行目标已变化，必须在当前目标上重新发起调用",
          );
        }
        if (config.runtimeTarget.target.provider !== "arca" || !config.runtimeTarget.target.sandboxId) {
          throw new RepairError(409, "repair_runtime_target_changed", "当前运行目标已不再是可访问的 ARCA sandbox");
        }
        const runtimeContext: RepairTaskContext = {
          schemaVersion: REPAIR_CONTRACT_VERSION,
          taskId: task.task_id,
          stepId: claimed.stepId,
          attempt: config.current.attempt,
          phase: config.current.phase,
          issue: config.issue,
          authorizationScope: config.authorizationScope,
          authorizationScopeDigest: config.authorizationScopeDigest,
          target: config.runtimeTarget.target,
          targetFingerprint: config.runtimeTarget.fingerprint,
          runtimeTargetVersion: config.runtimeTarget.version,
        };
        let runtimeResult: Record<string, unknown>;
        if (claimed.toolName === "arca_write") {
          const actionId = claimed.actionId;
          const plan = await this.loadApprovedPlan(config);
          const action = actionId ? plan.actions.find((item) => item.actionId === actionId) : null;
          if (!action || action.type !== "container_command") {
            repairNotFound("repair_action_not_approved", "ARCA action 不在当前获批方案内");
          }
          runtimeResult = await this.deps.runtimeTool.applyApprovedAction(runtimeContext, action, input.authHeaders);
        } else {
          if (requestObject.operation !== claimed.operation) {
            repairValidation("repair_tool_request_mismatch", "ARCA 只读调用与已登记 operation 不一致");
          }
          runtimeResult = await this.deps.runtimeTool.inspect(
            runtimeContext,
            requestObject as RepairRuntimeInspectInput,
            input.authHeaders,
          );
        }
        const terminalStatus: RepairToolCallTerminalStatus = claimed.isWrite && runtimeResult.status === "unknown"
          ? "unknown"
          : claimed.isWrite && runtimeResult.status === "failed" ? "failed" : "succeeded";
        const completed = await this.deps.repairRepo.completeToolCall({
          callId: claimed.callId,
          executionId: claimed.executionId,
          authorizationScopeDigest: claimed.authorizationScopeDigest,
          leaseOwner,
          status: terminalStatus,
          result: runtimeResult,
          now: this.now(),
        });
        return browserToolCall(completed!.call, { canViewDetails: true, canExecute: true });
      }
      if (claimed.toolName !== "ocb_write" || !claimed.isWrite || claimed.operation !== "restart_bot") {
        throw new RepairError(
          409,
          "repair_ocb_operation_retired",
          "该历史 OCB 调用已停用；Repair 现在仅通过 OCB 执行获批的 Bot 重启",
        );
      }
      const result: OcbRepairGatewayResult = await this.deps.ocb.execute({
        scope: config.authorizationScope,
        operation: {
          type: claimed.operation,
          params: objectParams(requestObject.params),
        } as OcbRepairOperation,
        authHeaders: input.authHeaders,
        callerUserId: input.actorUserId,
        callerIsAdmin: input.isAdmin === true,
      });
      if (result.requiresTargetRefresh) {
        config = await this.refreshTarget(
          input.actorUserId,
          task,
          config,
          "after_action",
          true,
          input.isAdmin === true,
        );
      }
      const completed = await this.deps.repairRepo.completeToolCall({
        callId: claimed.callId,
        executionId: claimed.executionId,
        authorizationScopeDigest: claimed.authorizationScopeDigest,
        leaseOwner,
        status: "succeeded",
        result: result.result,
        now: this.now(),
      });
      return browserToolCall(completed!.call, { canViewDetails: true, canExecute: true });
    } catch (error) {
      const terminal: RepairToolCallTerminalStatus = claimed.isWrite && this.unknownWriteOutcome(error) ? "unknown" : "failed";
      const errorCode = error instanceof RepairError
        ? error.code
        : claimed.toolName.startsWith("arca_") ? "repair_arca_failed" : "repair_ocb_failed";
      const errorMessage = persistableSingleLineError(
        error instanceof Error ? error.message : String(error),
        "OCB operation 调用失败",
      );
      const completed = await this.deps.repairRepo.completeToolCall({
        callId: claimed.callId,
        executionId: claimed.executionId,
        authorizationScopeDigest: claimed.authorizationScopeDigest,
        leaseOwner,
        status: terminal,
        result: null,
        errorCode,
        errorMessage,
        now: this.now(),
      });
      if (terminal === "unknown") {
        return browserToolCall(completed!.call, { canViewDetails: true, canExecute: true });
      }
      throw new RepairError(error instanceof RepairError ? error.status : 502, errorCode, errorMessage);
    }
  }

  private async currentCfuseLoginCall(
    config: RepairTaskConfig,
    toolCallId: string,
  ): Promise<RepairToolCall> {
    const call = await this.deps.repairRepo.findToolCall(requiredText(toolCallId, "toolCallId", 64));
    if (!call
      || call.taskId !== config.taskId
      || call.stepId !== config.current.stepId
      || call.executionId !== config.execution.executionId
      || call.authorizationScopeDigest !== config.authorizationScopeDigest
      || call.toolName !== "cfuse_login"
      || call.operation !== "authorize"
      || config.execution.invalidatedAt != null
      || config.execution.state === "ended") {
      repairNotFound("repair_cfuse_login_not_found", "cfuse 登录授权不存在");
    }
    return call;
  }

  private assertCfuseSlot(slot: CfuseAuthCodeSlot, call: RepairToolCall): void {
    if (slot.taskId !== call.taskId
      || slot.stepId !== call.stepId
      || slot.executionId !== call.executionId
      || slot.authorizationScopeDigest !== call.authorizationScopeDigest
      || slot.expiresAt !== call.deadlineAt) {
      this.cfuseAuthCodes.delete(call.callId);
      throw new RepairError(403, "repair_cfuse_login_scope_mismatch", "cfuse 登录授权作用域不匹配");
    }
  }

  private cfuseLeaseOwner(executionId: string): string {
    return `cfuse-${executionId}`;
  }

  private async readableTask(
    actorUserId: string,
    taskId: string,
    isAdmin = false,
  ): Promise<{
    task: EvolveTaskRow;
    isOwner: boolean;
  }> {
    const task = await this.deps.repo.findTask(taskId);
    if (!task || task.task_type !== "repair") {
      repairNotFound("repair_task_not_found", "Repair Task 不存在");
    }
    const config = taskConfig(task);
    if (config.authorizationScope.botId !== task.bot_id
      || config.authorizationScope.ownerId !== task.user_id
      || config.authorizationScope.actorUserId !== task.created_by) {
      repairForbidden("repair_scope_forbidden", "Repair 授权范围与 Task 不匹配");
    }
    const isOwner = task.created_by === actorUserId && task.user_id === actorUserId;
    if (isOwner) {
      if (config.authorizationScope.actorUserId !== actorUserId
        || config.authorizationScope.ownerId !== actorUserId) {
        repairForbidden("repair_scope_forbidden", "Repair 授权范围与当前用户不匹配");
      }
      return { task, isOwner: true };
    }
    if (isAdmin) return { task, isOwner: false };
    if (config.shared !== true) {
      repairForbidden("repair_task_not_shared", "权限不足，请联系任务 Owner 开启分享");
    }
    return { task, isOwner: false };
  }

  private async ownedTask(actorUserId: string, taskId: string, isAdmin = false): Promise<EvolveTaskRow> {
    const task = await this.deps.repo.findTask(taskId);
    if (!task || task.task_type !== "repair") repairNotFound("repair_task_not_found", "Repair Task 不存在");
    if (task.created_by !== actorUserId && !isAdmin) {
      repairForbidden("repair_task_forbidden", "无权访问该 Repair Task");
    }
    if (isAdmin) return task;
    const config = taskConfig(task);
    if (config.authorizationScope.actorUserId !== task.created_by
      || config.authorizationScope.ownerId !== task.user_id
      || config.authorizationScope.botId !== task.bot_id
      || config.authorizationScope.actorUserId !== actorUserId) {
      repairForbidden("repair_scope_forbidden", "Repair 授权范围与当前用户不匹配");
    }
    return task;
  }

  private async currentStep(task: EvolveTaskRow, config: RepairTaskConfig): Promise<EvolveStepRow> {
    const step = await this.deps.repo.findStep(config.current.stepId);
    if (!step || step.task_id !== task.task_id || step.step_type !== config.current.phase) {
      throw new RepairError(500, "repair_current_step_mismatch", "Repair 当前 Step 与 Task config 不匹配");
    }
    return step;
  }

  private async credentialWorkloadContext(identity: RepairWorkloadIdentity): Promise<{
    task: EvolveTaskRow;
    step: EvolveStepRow;
    config: RepairTaskConfig;
    context: RepairTaskContext;
  }> {
    const task = await this.deps.repo.findTask(identity.taskId);
    const step = await this.deps.repo.findStep(identity.stepId);
    if (!task || task.task_type !== "repair" || !step || step.task_id !== task.task_id) {
      repairNotFound("repair_workload_not_found", "Repair workload 不存在");
    }
    const config = taskConfig(task);
    if (config.current.stepId !== step.step_id
      || config.current.phase !== step.step_type
      || config.execution.stepId !== step.step_id
      || config.execution.executionId !== identity.executionId) {
      throw new RepairError(403, "repair_workload_scope_mismatch", "AIS execution 与当前 Task/Step 不匹配");
    }
    assertExecutionSupported(config);
    return {
      task,
      step,
      config,
      context: {
        schemaVersion: REPAIR_CONTRACT_VERSION,
        taskId: task.task_id,
        stepId: step.step_id,
        attempt: config.current.attempt,
        phase: config.current.phase,
        issue: config.issue,
        authorizationScope: config.authorizationScope,
        authorizationScopeDigest: config.authorizationScopeDigest,
        target: config.runtimeTarget.target,
        targetFingerprint: config.runtimeTarget.fingerprint,
        runtimeTargetVersion: config.runtimeTarget.version,
      },
    };
  }

  private async activeWorkloadContext(identity: RepairWorkloadIdentity) {
    const value = await this.credentialWorkloadContext(identity);
    assertExecutionSupported(value.config);
    if (!new Set(["created", "dispatched", "running"]).has(value.step.status)
      || !new Set(["pending", "running"]).has(value.task.status)) {
      throw new RepairError(409, "repair_workload_not_active", "Repair workload 已不处于活动状态");
    }
    return value;
  }

  private validateExecutorOutput(output: RepairExecutorOutput, config: RepairTaskConfig): void {
    if (output.schemaVersion !== REPAIR_CONTRACT_VERSION
      || output.taskId !== config.taskId
      || output.stepId !== config.current.stepId
      || output.attempt !== config.current.attempt
      || output.phase !== config.current.phase) {
      repairValidation("repair_executor_identity_mismatch", "Executor output 与 Task/Step/attempt/phase 不匹配");
    }
    const digest = artifactDigest(output.artifactDigest);
    if (!output.artifacts || typeof output.artifacts !== "object" || Array.isArray(output.artifacts)) {
      repairValidation("invalid_repair_artifacts", "Executor output 缺少 artifacts");
    }
    const actual = output.artifacts as Record<string, unknown>;
    for (const [name, expected] of Object.entries(config.current.artifacts)) {
      const value = actual[name];
      if (name === "checkpoint" && value == null) continue;
      if (!value || typeof value !== "object" || Array.isArray(value)) {
        repairValidation("invalid_repair_artifacts", `Executor output 缺少 ${name}`);
      }
      const item = value as Record<string, unknown>;
      if (item.objectKey !== expected.objectKey
        || !Number.isSafeInteger(item.size) || Number(item.size) <= 0
        || typeof item.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(item.sha256)) {
        repairValidation("invalid_repair_artifacts", `Executor ${name} 元数据不合法`);
      }
    }
    const primaryName = config.current.phase === "repair_plan" ? "plan" : "applyResult";
    const primary = actual[primaryName] as Record<string, unknown>;
    if (primary.sha256 !== digest) repairValidation("artifact_digest_mismatch", "artifactDigest 与主产物 SHA-256 不匹配");
  }

  private async loadAndValidatePlan(config: RepairTaskConfig, digest: string): Promise<RepairPlanArtifact> {
    const key = config.current.artifacts.plan?.objectKey;
    if (!key) throw new RepairError(500, "repair_plan_artifact_missing", "Repair Plan objectKey 缺失");
    const object = await this.deps.store.getObject(key);
    if (object.content.length > 1024 * 1024 || sha256(object.content) !== digest) {
      repairValidation("repair_plan_digest_mismatch", "OSS Repair Plan 内容与 digest 不匹配");
    }
    let plan: RepairPlanArtifact;
    try {
      plan = JSON.parse(object.content.toString("utf8")) as RepairPlanArtifact;
    } catch {
      return repairValidation("invalid_repair_plan", "Repair Plan 不是合法 JSON");
    }
    validatePlan(plan, config);
    return plan;
  }

  private async loadHistoricalPlan(
    config: RepairTaskConfig,
    history: RepairHistoryItem,
    step: EvolveStepRow,
  ): Promise<RepairPlanArtifact> {
    const output = parseOutput(step);
    const historyDigest = artifactDigest(history.artifactDigest);
    const outputDigest = artifactDigest(output.artifactDigest);
    const rawArtifacts = output.artifacts;
    const rawPlanMetadata = rawArtifacts && typeof rawArtifacts === "object" && !Array.isArray(rawArtifacts)
      ? (rawArtifacts as Record<string, unknown>).plan
      : null;
    const metadata = rawPlanMetadata && typeof rawPlanMetadata === "object" && !Array.isArray(rawPlanMetadata)
      ? rawPlanMetadata as Record<string, unknown>
      : null;
    const canonicalObjectKey = artifactsFor(config.taskId, history.stepId, "repair_plan").plan?.objectKey;
    if (historyDigest !== outputDigest
      || metadata == null
      || typeof canonicalObjectKey !== "string"
      || metadata.objectKey !== canonicalObjectKey
      || metadata.sha256 !== historyDigest
      || !Number.isSafeInteger(metadata.size)
      || Number(metadata.size) <= 0
      || Number(metadata.size) > 1024 * 1024) {
      throw new RepairError(
        409,
        "repair_historical_plan_artifact_invalid",
        "历史 Repair Plan 元数据与不可变审计记录不匹配",
      );
    }
    const object = await this.deps.store.getObject(canonicalObjectKey);
    if (object.content.length !== Number(metadata.size)
      || object.content.length > 1024 * 1024
      || sha256(object.content) !== historyDigest) {
      throw new RepairError(
        409,
        "repair_historical_plan_artifact_changed",
        "历史 Repair Plan 内容与不可变审计记录不匹配",
      );
    }
    let plan: RepairPlanArtifact;
    try {
      plan = JSON.parse(object.content.toString("utf8")) as RepairPlanArtifact;
    } catch {
      return repairValidation("invalid_repair_plan", "历史 Repair Plan 不是合法 JSON");
    }
    validatePlanBody(plan, {
      allowHistoricalUnsafeProcessActions: true,
      allowHistoricalEngineConfigReplace: true,
      allowHistoricalUnvalidatedOcbOperationParams: true,
    });
    const targetVersionRecorded = config.runtimeTargetHistory.some(snapshot => (
      snapshot.version === plan.runtimeTargetVersion
    ));
    if (plan.taskId !== config.taskId
      || plan.stepId !== history.stepId
      || plan.attempt !== history.attempt
      || plan.authorizationScopeDigest !== config.authorizationScopeDigest
      || !targetVersionRecorded) {
      throw new RepairError(
        409,
        "repair_historical_plan_identity_mismatch",
        "历史 Repair Plan 与 Task/Step/授权范围/目标版本不匹配",
      );
    }
    return plan;
  }

  private async loadApprovedPlan(config: RepairTaskConfig): Promise<RepairPlanArtifact> {
    const approved = config.approvedPlan;
    if (!approved) throw new RepairError(409, "repair_plan_not_approved", "当前 Apply 没有绑定获批方案");
    const object = await this.deps.store.getObject(approved.objectKey);
    if (object.content.length > 1024 * 1024 || sha256(object.content) !== approved.artifactDigest) {
      throw new RepairError(409, "approved_plan_changed", "获批方案内容已变化，拒绝执行");
    }
    let plan: RepairPlanArtifact;
    try {
      plan = JSON.parse(object.content.toString("utf8")) as RepairPlanArtifact;
    } catch {
      return repairValidation("invalid_repair_plan", "获批 Repair Plan 不是合法 JSON");
    }
    validatePlanBody(plan);
    if (plan.taskId !== config.taskId
      || plan.stepId !== approved.stepId
      || plan.authorizationScopeDigest !== config.authorizationScopeDigest) {
      throw new RepairError(409, "approved_plan_scope_mismatch", "获批方案作用域不匹配");
    }
    if ((isRepairPlanV2(plan) && plan.recommendation.disposition !== "execute_actions")
      || (!isRepairPlanV2(plan) && plan.actions.length === 0)) {
      throw new RepairError(409, "approved_plan_not_executable", "获批方案没有可执行的 Repair 操作");
    }
    return plan;
  }

  private async loadAndValidateApplyResult(
    config: RepairTaskConfig,
    digest: string,
  ): Promise<RepairApplyResultArtifact> {
    const key = config.current.artifacts.applyResult?.objectKey;
    if (!key) throw new RepairError(500, "repair_apply_artifact_missing", "Repair Apply result objectKey 缺失");
    const object = await this.deps.store.getObject(key);
    if (object.content.length > 1024 * 1024 || sha256(object.content) !== digest) {
      repairValidation("repair_apply_result_digest_mismatch", "OSS Repair Apply 结果与 digest 不匹配");
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(object.content.toString("utf8")) as unknown;
    } catch {
      return repairValidation("invalid_repair_apply_result", "Repair Apply 结果不是合法 JSON");
    }
    const approvedPlan = await this.loadApprovedPlan(config);
    const result = validateApplyResultBody(parsed, config, approvedPlan);
    const calls = await this.deps.repairRepo.listToolCalls(config.taskId, {
      stepId: config.current.stepId,
      isWrite: true,
      limit: 500,
    });
    if (calls.length === 500) {
      const overflow = await this.deps.repairRepo.listToolCalls(config.taskId, {
        stepId: config.current.stepId,
        isWrite: true,
        afterId: calls.at(-1)?.id,
        limit: 1,
      });
      if (overflow.length) {
        repairValidation("invalid_repair_apply_result", "当前 Apply Step 的写操作审计超过 500 条，拒绝收口");
      }
    }
    assertApplyResultMatchesLedger(result, config, calls);
    return result;
  }

  private async loadRecoveryCheckpoint(config: RepairTaskConfig): Promise<{
    sourceStepId: string;
    sha256: string;
    content: Record<string, unknown>;
  } | null> {
    for (const history of [...config.history].reverse()) {
      try {
        if ((history.phase !== "repair_plan" && history.phase !== "repair_apply")
          || !Number.isSafeInteger(history.stepNo) || history.stepNo <= 0) continue;
        const step = await this.deps.repo.findStep(history.stepId);
        if (!step || step.task_id !== config.taskId
          || step.step_type !== history.phase || step.step_no !== history.stepNo) continue;
        const artifacts = parseOutput(step).artifacts;
        if (!artifacts || typeof artifacts !== "object" || Array.isArray(artifacts)) continue;
        const checkpoint = (artifacts as Record<string, unknown>).checkpoint;
        if (!checkpoint || typeof checkpoint !== "object" || Array.isArray(checkpoint)) continue;
        const metadata = checkpoint as Record<string, unknown>;
        const expectedObjectKey = artifactsFor(config.taskId, history.stepId, history.phase).checkpoint?.objectKey;
        if (typeof expectedObjectKey !== "string"
          || metadata.objectKey !== expectedObjectKey
          || typeof metadata.size !== "number"
          || !Number.isSafeInteger(metadata.size)
          || metadata.size <= 0
          || metadata.size > MAX_RECOVERY_CHECKPOINT_BYTES
          || typeof metadata.sha256 !== "string"
          || !/^[a-f0-9]{64}$/.test(metadata.sha256)) continue;
        const object = await this.deps.store.getObject(expectedObjectKey);
        if (object.content.byteLength !== metadata.size
          || object.content.byteLength > MAX_RECOVERY_CHECKPOINT_BYTES
          || sha256(object.content) !== metadata.sha256) continue;
        const parsed = JSON.parse(object.content.toString("utf8")) as unknown;
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) continue;
        const content = redactValue(parsed);
        if (!content || typeof content !== "object" || Array.isArray(content)) continue;
        return {
          sourceStepId: history.stepId,
          sha256: metadata.sha256,
          content: content as Record<string, unknown>,
        };
      } catch {
        // A checkpoint is optional recovery state. Invalid or unavailable
        // history entries are skipped so an older valid checkpoint may win.
      }
    }
    return null;
  }

  private archiveCurrent(config: RepairTaskConfig, feedback: string | null, digest: string | null): RepairTaskConfig {
    return {
      ...config,
      history: [...config.history, {
        stepId: config.current.stepId,
        stepNo: config.current.stepNo,
        attempt: config.current.attempt,
        phase: config.current.phase,
        status: "succeeded",
        artifactDigest: digest,
        feedback,
      }],
    };
  }

  private nextStepConfig(
    task: EvolveTaskRow,
    config: RepairTaskConfig,
    phase: RepairPhase,
    feedback: string | null,
    approvedPlan: ApprovedRepairPlan | null,
    newExecution: boolean,
  ): RepairTaskConfig {
    const stepNo = config.current.stepNo + 1;
    const attempt = config.current.attempt + 1;
    const suffix = phase === "repair_plan" ? "PLAN" : "APPLY";
    const stepId = `${task.task_id}-${suffix}-${attempt}`;
    const artifacts = artifactsFor(task.task_id, stepId, phase);
    const archived = this.archiveCurrent(config, feedback, null);
    return {
      ...archived,
      current: { stepId, stepNo, attempt, phase, artifacts },
      approvedPlan,
      artifacts,
      pendingDecision: newExecution ? config.pendingDecision : null,
    };
  }

  private async startNewExecution(
    task: EvolveTaskRow,
    config: RepairTaskConfig,
    phase: RepairPhase,
    llmApiKey: string | null = null,
    actorUserId?: string,
    isAdmin = false,
  ): Promise<Record<string, unknown>> {
    this.assertSnapshotConfigured();
    assertExecutionSupported(config);
    const previousStep = await this.currentStep(task, config);
    if (!TERMINAL_STEP_STATUSES.has(previousStep.status)) {
      throw new RepairError(409, "repair_previous_step_not_terminal", "上一 Repair Step 尚未结束");
    }
    const digest = optionalArtifactDigest(parseOutput(previousStep).artifactDigest);
    const next = this.nextStepConfig(task, config, phase, config.pendingDecision?.feedback ?? null, config.approvedPlan, true);
    if (next.history.length) {
      next.history[next.history.length - 1].artifactDigest = digest;
      next.history[next.history.length - 1].status = previousStep.status;
    }
    const ticket = issueRepairExecutionTicket();
    const now = this.now();
    next.execution = {
      executionId: `exec-${randomUUID()}`,
      ticketDigest: ticket.digest,
      jobId: null,
      ccSessionId: null,
      state: "dispatching",
      stepId: next.current.stepId,
      phase,
      leaseExpiresAt: now + this.deps.config.decisionGraceSeconds,
      decisionDeadlineAt: null,
      lastHeartbeatAt: null,
      invalidatedAt: null,
    };
    next.pendingDecision = null;
    const transitioned = await this.deps.repairRepo.transitionStep({
      taskId: task.task_id,
      expectedTaskStatuses: [task.status],
      expectedCurrentStepId: previousStep.step_id,
      expectedTaskConfigDigest: storedConfigDigest(task.config_json),
      previousStep: {
        stepId: previousStep.step_id,
        expectedStatuses: [previousStep.status],
        status: previousStep.status,
      },
      nextTaskStatus: "pending",
      nextConfig: next,
      nextStep: {
        stepId: next.current.stepId,
        stepType: phase,
        stepNo: next.current.stepNo,
        command: phase,
      },
    });
    if (transitioned.outcome !== "transitioned") {
      throw new RepairError(409, "repair_step_transition_conflict", `Repair Step 切换冲突: ${transitioned.reason}`);
    }
    await this.dispatch(task.task_id, ticket.ticket, llmApiKey);
    const updatedTask = await this.deps.repo.findTask(task.task_id);
    if (!updatedTask) repairNotFound("repair_task_not_found", "Repair Task 不存在");
    return this.view(updatedTask, actorUserId == null
      ? { canOperate: true, canManageShare: true }
      : this.actorViewAccess(updatedTask, actorUserId, isAdmin));
  }

  private async retryFailedPlanInCurrentExecution(
    task: EvolveTaskRow,
    config: RepairTaskConfig,
    failedStep: EvolveStepRow,
    actorUserId: string,
    isAdmin: boolean,
  ): Promise<Record<string, unknown>> {
    if (!this.executionCanClaim(config) || config.execution.jobId == null) {
      throw new RepairError(409, "repair_retry_execution_expired", "原 Repair AIS 已结束，请重新运行方案步骤");
    }
    const next = this.nextStepConfig(task, config, "repair_plan", null, null, false);
    if (next.history.length) {
      next.history[next.history.length - 1].status = "failed";
      next.history[next.history.length - 1].artifactDigest = optionalArtifactDigest(
        parseOutput(failedStep).artifactDigest,
      );
    }
    const now = this.now();
    next.execution = {
      ...config.execution,
      ccSessionId: null,
      state: "running",
      stepId: next.current.stepId,
      phase: "repair_plan",
      leaseExpiresAt: now + this.deps.config.executionLeaseSeconds,
      decisionDeadlineAt: null,
      lastHeartbeatAt: now,
      invalidatedAt: null,
    };
    next.pendingDecision = null;
    const transitioned = await this.deps.repairRepo.transitionStep({
      taskId: task.task_id,
      expectedTaskStatuses: ["failed"],
      expectedCurrentStepId: failedStep.step_id,
      expectedTaskConfigDigest: storedConfigDigest(task.config_json),
      previousStep: {
        stepId: failedStep.step_id,
        expectedStatuses: ["failed"],
        status: "failed",
      },
      nextTaskStatus: "running",
      nextConfig: next,
      nextStep: {
        stepId: next.current.stepId,
        stepType: "repair_plan",
        stepNo: next.current.stepNo,
        command: "repair_plan",
      },
      reuseJobId: config.execution.jobId,
    });
    if (transitioned.outcome !== "transitioned") {
      throw new RepairError(409, "repair_step_transition_conflict", `Repair Step 切换冲突: ${transitioned.reason}`);
    }
    const updatedTask = await this.deps.repo.findTask(task.task_id);
    if (!updatedTask) repairNotFound("repair_task_not_found", "Repair Task 不存在");
    return this.view(updatedTask, this.actorViewAccess(updatedTask, actorUserId, isAdmin));
  }

  private async dispatch(
    taskId: string,
    executionTicket: string,
    llmApiKey: string | null = null,
  ): Promise<void> {
    const task = await this.deps.repo.findTask(taskId);
    if (!task) repairNotFound("repair_task_not_found", "Repair Task 不存在");
    const config = taskConfig(task);
    assertExecutionSupported(config);
    try {
      const jobId = await this.runner.dispatch(task, config.current.stepId, task.created_by, {
        ...config,
        artifacts: repairArtifactsWithContentTypes(config.artifacts),
        executionTicket,
        ...(llmApiKey ? { llmApiKey } : {}),
      });
      const latestTask = await this.deps.repo.findTask(taskId);
      if (!latestTask) return;
      const latest = taskConfig(latestTask);
      if (latestTask.status === "canceled"
        || latest.execution.state === "ended"
        || latest.execution.invalidatedAt != null) {
        await this.stopTerminatedAisJob(config.current.stepId, jobId);
        return;
      }
      if (latest.execution.executionId === config.execution.executionId) {
        let currentTask = latestTask;
        let current = latest;
        for (let attempt = 0; attempt < 5; attempt += 1) {
          if (current.execution.executionId !== config.execution.executionId) return;
          const updated = await this.deps.repairRepo.compareAndSetTaskConfig({
            taskId,
            expectedTaskStatuses: [currentTask.status],
            expectedCurrentStepId: current.current.stepId,
            expectedTaskConfigDigest: storedConfigDigest(currentTask.config_json),
            nextConfig: { ...current, execution: { ...current.execution, jobId } },
          });
          if (updated) break;
          const reloaded = await this.deps.repo.findTask(taskId);
          if (!reloaded) return;
          currentTask = reloaded;
          current = taskConfig(reloaded);
        }
      }
    } catch (error) {
      const latestTask = await this.deps.repo.findTask(taskId);
      if (latestTask?.status === "canceled") return;
      const httpStatus = error instanceof Error
        ? error.message.match(/^AIS executeSnapshot HTTP ([45]\d{2})(?::|$)/)?.[1]
        : undefined;
      const safe = httpStatus
        ? `Repair AIS dispatch failed (HTTP ${httpStatus})`
        : "Repair AIS dispatch failed";
      await this.deps.repo.markDispatchFailed(config.current.stepId, safe);
      throw new RepairError(502, "repair_ais_dispatch_failed", safe);
    }
  }

  private async continuationEnvelope(config: RepairTaskConfig): Promise<Record<string, unknown>> {
    assertExecutionSupported(config);
    const uploads: Record<string, unknown> = {};
    for (const [name, artifact] of Object.entries(config.artifacts)) {
      const contentType = artifact.contentType ?? repairArtifactContentType(name);
      uploads[name] = {
        ...artifact,
        contentType,
        putUrl: await this.deps.store.createSignedUrl(
          artifact.objectKey,
          "PUT",
          86_400,
          { "Content-Type": contentType },
        ),
      };
    }
    return safeTaskEnvelope(config, uploads);
  }

  private executionCanClaim(config: RepairTaskConfig): boolean {
    const now = this.now();
    return config.execution.state === "waiting_decision"
      && config.execution.invalidatedAt == null
      && config.execution.leaseExpiresAt > now
      && (config.execution.decisionDeadlineAt ?? 0) > now;
  }

  private canRecoverDecisionClaimAlias(
    identity: RepairWorkloadIdentity,
    task: EvolveTaskRow,
    step: EvolveStepRow,
    config: RepairTaskConfig,
  ): boolean {
    const previous = config.history.at(-1);
    return identity.requestedStepId != null
      && identity.requestedStepId !== identity.stepId
      && previous?.stepId === identity.requestedStepId
      && Number.isSafeInteger(previous.stepNo)
      && previous.stepNo > 0
      && Number.isSafeInteger(config.current.stepNo)
      && previous.stepNo + 1 === config.current.stepNo
      && task.status === "running"
      && (step.status === "dispatched" || step.status === "running")
      && config.execution.state === "running"
      && config.execution.invalidatedAt == null
      && config.execution.leaseExpiresAt > this.now()
      && config.execution.jobId != null
      && step.bot_run_id === config.execution.jobId;
  }

  private recoveredDecisionKind(
    config: RepairTaskConfig,
  ): RepairPendingDecision["kind"] | "retry_failed_plan" {
    if (config.current.phase === "repair_apply") return "approve_plan";
    if (config.history.at(-1)?.status === "failed") return "retry_failed_plan";
    return config.history.at(-1)?.phase === "repair_apply" ? "retry_result" : "reject_plan";
  }

  private phaseForDecision(decision: RepairPendingDecision): RepairPhase {
    return decision.kind === "approve_plan" ? "repair_apply" : "repair_plan";
  }

  private async refreshTarget(
    actorUserId: string,
    task: EvolveTaskRow,
    config: RepairTaskConfig,
    reason: RepairRuntimeTargetSnapshot["reason"],
    forceVersion = false,
    isAdmin = false,
  ): Promise<RepairTaskConfig> {
    const scope = config.authorizationScope;
    if (actorUserId !== scope.actorUserId && !isAdmin) {
      repairForbidden("repair_scope_forbidden", "当前用户不在 Repair 授权范围内");
    }
    const target = await this.deps.targets.resolve({
      environment: scope.environment,
      ownerId: scope.ownerId,
      botId: scope.botId,
    });
    if (target.ownerId !== scope.ownerId || target.botId !== scope.botId || target.environment !== scope.environment) {
      throw new RepairError(409, "repair_target_scope_changed", "Bot 当前运行目标已超出 Repair 授权范围");
    }
    const fingerprint = targetFingerprint(target);
    let currentTask = await this.deps.repo.findTask(task.task_id);
    if (!currentTask) repairNotFound("repair_task_not_found", "Repair Task 不存在");
    let current = taskConfig(currentTask);
    for (let attempt = 0; attempt < 5; attempt += 1) {
      if (current.authorizationScopeDigest !== config.authorizationScopeDigest
        || (!isAdmin && current.authorizationScope.actorUserId !== actorUserId)) {
        throw new RepairError(409, "repair_scope_changed", "Repair 授权范围已变化");
      }
      if (!forceVersion && fingerprint === current.runtimeTarget.fingerprint) return current;
      const snapshot = targetSnapshot(target, current.runtimeTarget.version + 1, reason);
      const next: RepairTaskConfig = {
        ...current,
        target,
        targetFingerprint: snapshot.fingerprint,
        runtimeTarget: snapshot,
        runtimeTargetHistory: [...current.runtimeTargetHistory.slice(-49), snapshot],
      };
      const updated = await this.deps.repairRepo.compareAndSetTaskConfig({
        taskId: currentTask.task_id,
        expectedTaskStatuses: [currentTask.status],
        expectedCurrentStepId: current.current.stepId,
        expectedTaskConfigDigest: storedConfigDigest(currentTask.config_json),
        nextConfig: next,
      });
      if (updated) return next;
      const reloaded = await this.deps.repo.findTask(task.task_id);
      if (!reloaded) repairNotFound("repair_task_not_found", "Repair Task 不存在");
      currentTask = reloaded;
      current = taskConfig(reloaded);
    }
    throw new RepairError(409, "repair_target_refresh_conflict", "Repair 目标刷新与状态更新冲突，请重试");
  }

  private async listStepAuditCalls(
    taskId: string,
    stepId: string,
    recordKind: "source" | "conclusion",
  ): Promise<RepairToolCall[]> {
    const calls: RepairToolCall[] = [];
    let afterId: number | undefined;
    while (calls.length < MAX_STEP_AUDIT_CALLS) {
      const page = await this.deps.repairRepo.listToolCalls(taskId, {
        stepId,
        afterId,
        limit: 500,
        recordKind,
      });
      calls.push(...page);
      if (page.length < 500) return calls;
      const nextAfterId = page.at(-1)!.id;
      if (afterId != null && nextAfterId <= afterId) {
        throw new RepairError(409, "repair_tool_audit_inconsistent", "Repair 工具审计分页结果不一致");
      }
      afterId = nextAfterId;
    }
    throw new RepairError(409, "repair_tool_audit_too_large", "当前步骤的工具审计记录过多，不能安全继续");
  }

  private async recoveryAuditProjection(input: {
    taskId: string;
    stepId: string;
    authorizationScopeDigest: string;
    executionId?: string;
  }): Promise<{
    sources: RepairToolCall[];
    toolCalls: Record<string, unknown>[];
    unconcludedToolCallIds: string[];
    incompleteToolCallIds: string[];
    truncated: boolean;
  }> {
    const [allSources, allConclusions] = await Promise.all([
      this.listStepAuditCalls(input.taskId, input.stepId, "source"),
      this.listStepAuditCalls(input.taskId, input.stepId, "conclusion"),
    ]);
    const sources = allSources.filter((call) =>
      (input.executionId == null || call.executionId === input.executionId)
      && call.authorizationScopeDigest === input.authorizationScopeDigest
      && BUSINESS_AUDIT_TOOLS.has(call.toolName));
    const selectedSources = sources.length <= MAX_RECOVERY_CONTEXT_TOOL_CALLS
      ? sources
      : [
        ...sources.slice(0, RECOVERY_CONTEXT_HEAD_TOOL_CALLS),
        ...sources.slice(-(MAX_RECOVERY_CONTEXT_TOOL_CALLS - RECOVERY_CONTEXT_HEAD_TOOL_CALLS)),
      ];
    const selectedByConclusionRequestId = new Map(selectedSources.map((call) =>
      [`conclusion:${call.callId}`, call]));
    const conclusions = allConclusions.filter((call) => {
      const source = selectedByConclusionRequestId.get(call.clientRequestId);
      return source != null
        && call.executionId === source.executionId
        && call.authorizationScopeDigest === input.authorizationScopeDigest;
    });
    const concludedIds = new Set(conclusions.map((call) =>
      call.clientRequestId.slice("conclusion:".length)));
    return {
      sources,
      toolCalls: browserToolCalls(
        [...selectedSources, ...conclusions],
        true,
        false,
      ).map(compactRecoveryToolCall),
      unconcludedToolCallIds: selectedSources
        .filter((call) => TERMINAL_TOOL_STATUSES.has(call.status) && !concludedIds.has(call.callId))
        .map((call) => call.callId),
      incompleteToolCallIds: selectedSources
        .filter((call) => !TERMINAL_TOOL_STATUSES.has(call.status))
        .map((call) => call.callId),
      truncated: sources.length > selectedSources.length,
    };
  }

  private async priorStepRecoveryContext(
    identity: RepairWorkloadIdentity,
    config: RepairTaskConfig,
  ): Promise<Record<string, unknown> | null> {
    const history = config.history.at(-1);
    const isFeedbackReplan = history?.status === "succeeded" && Boolean(history.feedback?.trim());
    if (!history
      || !isFeedbackReplan
      || history.phase !== config.current.phase
      || history.attempt + 1 !== config.current.attempt) return null;
    const step = await this.deps.repo.findStep(history.stepId);
    if (!step
      || step.task_id !== identity.taskId
      || step.step_id !== history.stepId
      || step.step_no !== history.stepNo
      || step.step_type !== history.phase
      || step.status !== history.status) return null;
    const projection = await this.recoveryAuditProjection({
      taskId: identity.taskId,
      stepId: history.stepId,
      authorizationScopeDigest: config.authorizationScopeDigest,
    });
    return {
      stepId: history.stepId,
      stepNo: history.stepNo,
      attempt: history.attempt,
      phase: history.phase,
      status: history.status,
      ...(history.feedback ? { feedback: history.feedback } : {}),
      toolCalls: projection.toolCalls,
      unconcludedToolCallIds: projection.unconcludedToolCallIds,
      incompleteToolCallIds: projection.incompleteToolCallIds,
      truncated: projection.truncated,
    };
  }

  private async recoveryContext(
    identity: RepairWorkloadIdentity,
    context: RepairTaskContext,
    config: RepairTaskConfig,
    approvedPlan: unknown,
  ): Promise<Record<string, unknown>> {
    const [projection, priorStep] = await Promise.all([
      this.recoveryAuditProjection({
        taskId: identity.taskId,
        stepId: identity.stepId,
        executionId: identity.executionId,
        authorizationScopeDigest: config.authorizationScopeDigest,
      }),
      this.priorStepRecoveryContext(identity, config),
    ]);
    const writeAttempts = projection.sources
      .filter((call) => call.isWrite)
      .slice(0, MAX_APPLY_ATTEMPTS)
      .map((call) => ({
        toolCallId: call.callId,
        toolName: call.toolName,
        actionId: call.actionId,
        status: repairApplyAttemptStatus(call.status),
      }));
    return {
      schemaVersion: REPAIR_RECOVERY_CONTEXT_VERSION,
      taskId: identity.taskId,
      stepId: identity.stepId,
      executionId: identity.executionId,
      attempt: context.attempt,
      phase: context.phase,
      issue: redactValue(context.issue),
      authorizationScopeDigest: config.authorizationScopeDigest,
      runtimeTargetVersion: context.runtimeTargetVersion,
      target: redactValue(context.target),
      approvedPlan: redactValue(approvedPlan),
      toolCalls: projection.toolCalls,
      unconcludedToolCallIds: projection.unconcludedToolCallIds,
      incompleteToolCallIds: projection.incompleteToolCallIds,
      priorStep,
      writeAttempts,
      writeAttemptsTruncated: projection.sources.filter((call) => call.isWrite).length > writeAttempts.length,
      truncated: projection.truncated,
    };
  }

  private async assertRequiredSemanticConclusions(
    identity: RepairWorkloadIdentity,
    authorizationScopeDigest: string,
  ): Promise<void> {
    const [sources, records] = await Promise.all([
      this.listStepAuditCalls(identity.taskId, identity.stepId, "source"),
      this.listStepAuditCalls(identity.taskId, identity.stepId, "conclusion"),
    ]);
    this.assertRequiredSemanticConclusionsFromLedger(
      [...sources, ...records].sort((left, right) => left.id - right.id),
      identity,
      authorizationScopeDigest,
    );
  }

  private assertRequiredSemanticConclusionsFromLedger(
    calls: readonly RepairToolCall[],
    identity: RepairWorkloadIdentity,
    authorizationScopeDigest: string,
  ): void {
    const requiredSources = calls.filter((call) =>
      call.executionId === identity.executionId
      && call.authorizationScopeDigest === authorizationScopeDigest
      && BUSINESS_AUDIT_TOOLS.has(call.toolName)
      && unpackToolRequest(call.request).semanticConclusionRequired);
    if (requiredSources.length === 0) return;

    const active = requiredSources.find((call) => !TERMINAL_TOOL_STATUSES.has(call.status));
    if (active) {
      throw new RepairError(
        409,
        "repair_tool_conclusion_required",
        `工具调用 ${active.callId} 尚未完成，必须等待结果并记录结论`,
        active.callId,
        {
          recoveryClass: "agent_recovery",
          recoveryAction: "complete_missing_conclusions",
          automatic: true,
        },
      );
    }

    const sourceById = new Map(requiredSources.map((call) => [call.callId, call]));
    const completed = new Set<string>();
    for (const record of calls) {
      if (record.executionId !== identity.executionId
        || record.authorizationScopeDigest !== authorizationScopeDigest) continue;
      const parsed = semanticConclusion(record);
      if (!parsed) continue;
      const source = sourceById.get(parsed.sourceToolCallId);
      if (source
        && record.id > source.id
        && source.resultDigest === parsed.sourceResultDigest) {
        completed.add(source.callId);
      }
    }
    const missing = requiredSources.find((call) => !completed.has(call.callId));
    if (missing) {
      throw new RepairError(
        409,
        "repair_tool_conclusion_required",
        `工具调用 ${missing.callId} 必须先记录证据绑定的结论`,
        missing.callId,
        {
          recoveryClass: "agent_recovery",
          recoveryAction: "complete_missing_conclusions",
          automatic: true,
        },
      );
    }
  }

  private async beginToolCall(identity: RepairWorkloadIdentity, input: {
    clientRequestId: string;
    toolName: string;
    operation: string;
    purpose?: unknown;
    request: unknown;
    actionId?: string | null;
    isWrite: boolean;
    deadlineAt?: number | null;
    toolCallLedgerGuard?: (
      calls: readonly RepairToolCall[],
      context: RepairToolCallLedgerContext,
    ) => void;
  }) {
    const { config } = await this.credentialWorkloadContext(identity);
    const hasExplicitPurpose = input.purpose != null && input.purpose !== "";
    const purpose = !hasExplicitPurpose
      ? defaultToolPurpose(input.toolName, input.operation)
      : auditText(input.purpose, "purpose", 200);
    const semanticConclusionRequired = hasExplicitPurpose && BUSINESS_AUDIT_TOOLS.has(input.toolName);
    if (BUSINESS_AUDIT_TOOLS.has(input.toolName) && containsRepairSecret(input.request)) {
      repairValidation("repair_tool_request_secret_forbidden", "Repair 工具请求不能包含凭据或签名链接");
    }
    try {
      return await this.deps.repairRepo.createToolCall({
        callId: `rtc-${randomUUID()}`,
        taskId: identity.taskId,
        stepId: identity.stepId,
        executionId: identity.executionId,
        clientRequestId: input.clientRequestId,
        toolName: input.toolName,
        operation: input.operation,
        actionId: input.actionId ?? null,
        isWrite: input.isWrite,
        authorizationScopeDigest: config.authorizationScopeDigest,
        deadlineAt: input.deadlineAt ?? null,
        request: toolRequestEnvelope(
          input.request,
          config.runtimeTarget.version,
          purpose,
          semanticConclusionRequired,
        ),
        toolCallLedgerGuard: semanticConclusionRequired || input.toolCallLedgerGuard
          ? (calls, ledgerContext) => {
            if (semanticConclusionRequired) {
              this.assertRequiredSemanticConclusionsFromLedger(
                calls,
                identity,
                config.authorizationScopeDigest,
              );
            }
            input.toolCallLedgerGuard?.(calls, ledgerContext);
          }
          : undefined,
      });
    } catch (error) {
      if (error instanceof RepairToolCallWorkloadConflictError) {
        throw new RepairError(409, "repair_workload_not_active", "Repair workload 已不处于活动状态");
      }
      throw error;
    }
  }

  private async claimForServer(call: RepairToolCall): Promise<{ call: RepairToolCall; leaseOwner: string } | null> {
    if (call.isWrite && call.status === "executing"
      && call.leaseExpiresAt != null && call.leaseExpiresAt <= this.now()) {
      await this.finishExpiredToolCall(call, "execution_lease_expired");
      return null;
    }
    const leaseOwner = `clawweb-${randomUUID()}`;
    const claimed = await this.deps.repairRepo.claimToolCall({
      callId: call.callId,
      executionId: call.executionId,
      authorizationScopeDigest: call.authorizationScopeDigest,
      leaseOwner,
      leaseExpiresAt: this.now() + this.deps.config.executionLeaseSeconds,
      now: this.now(),
    });
    if (!claimed) {
      if (call.status === "succeeded") return { call, leaseOwner };
      const latest = await this.deps.repairRepo.findToolCall(call.callId);
      if (latest?.isWrite && latest.status === "executing"
        && latest.leaseExpiresAt != null && latest.leaseExpiresAt <= this.now()) {
        await this.finishExpiredToolCall(latest, "execution_lease_expired");
        return null;
      }
      throw new RepairError(409, "repair_tool_call_busy", "Repair tool call 正在执行或已结束");
    }
    return { call: claimed, leaseOwner };
  }

  private async finishToolCall(
    claimed: { call: RepairToolCall; leaseOwner: string },
    status: RepairToolCallTerminalStatus,
    result: unknown,
    error?: unknown,
  ): Promise<void> {
    await this.deps.repairRepo.completeToolCall({
      callId: claimed.call.callId,
      executionId: claimed.call.executionId,
      authorizationScopeDigest: claimed.call.authorizationScopeDigest,
      leaseOwner: claimed.leaseOwner,
      status,
      result,
      errorCode: error instanceof RepairError ? error.code : error ? "repair_tool_failed" : null,
      errorMessage: error ? redactPersistableText(error instanceof Error ? error.message : String(error), 4_000) : null,
      now: this.now(),
    });
  }

  private async recordFact(
    identity: RepairWorkloadIdentity,
    operation: string,
    requestId: string,
    request: unknown,
    result: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return this.runServerTool(identity, {
      clientRequestId: requestId,
      toolName: "repair_control",
      operation,
      request,
      isWrite: false,
    }, async () => result);
  }

  private async runServerTool(
    identity: RepairWorkloadIdentity,
    input: {
      clientRequestId: string;
      toolName: string;
      operation: string;
      purpose?: unknown;
      request: unknown;
      actionId?: string | null;
      isWrite: boolean;
    },
    invoke: () => Promise<Record<string, unknown>>,
  ): Promise<Record<string, unknown>> {
    const created = await this.beginToolCall(identity, input);
    if (!created.created) {
      if (created.call.status === "succeeded" && created.call.result && typeof created.call.result === "object") {
        return { ...(created.call.result as Record<string, unknown>), toolCallId: created.call.callId };
      }
      if (created.call.status === "unknown") return { status: "unknown", toolCallId: created.call.callId };
      if (created.call.status === "failed" || created.call.status === "canceled") {
        throw new RepairError(
          409,
          "repair_tool_call_already_terminal",
          "该 Repair tool call 已结束，不能自动重放",
          created.call.callId,
        );
      }
    }
    const claimed = await this.claimForServer(created.call);
    if (!claimed) return { status: "unknown", toolCallId: created.call.callId };
    try {
      const result = await invoke();
      if (input.isWrite && result.status === "unknown") {
        await this.finishToolCall(claimed, "unknown", result);
        return { ...result, toolCallId: created.call.callId };
      }
      if (input.isWrite && result.status === "failed") {
        await this.finishToolCall(claimed, "failed", result);
        return { ...result, toolCallId: created.call.callId };
      }
      await this.finishToolCall(claimed, "succeeded", result);
      return { ...result, toolCallId: created.call.callId };
    } catch (error) {
      const status: RepairToolCallTerminalStatus = input.isWrite && this.unknownWriteOutcome(error) ? "unknown" : "failed";
      await this.finishToolCall(claimed, status, null, error);
      if (status === "unknown") return { status: "unknown", toolCallId: created.call.callId };
      if (error instanceof RepairError) {
        throw new RepairError(error.status, error.code, error.message, created.call.callId);
      }
      throw new RepairError(500, "repair_tool_failed", "Repair tool call 执行失败", created.call.callId);
    }
  }

  private unknownWriteOutcome(error: unknown): boolean {
    return error instanceof RepairError
      ? new Set([
        "repair_baas_timeout",
        "repair_baas_failed",
        "repair_arca_timeout",
        "repair_arca_failed",
        "ocb_operation_timeout",
        "ocb_operation_failed",
        "ocb_response_too_large",
        "ocb_engine_config_verification_unknown",
      ]).has(error.code)
      : true;
  }

  private async finishExpiredToolCall(call: RepairToolCall, reason: string): Promise<RepairToolCall> {
    if (call.status === "pending") {
      const completed = await this.deps.repairRepo.cancelPendingToolCall(
        call.callId,
        call.executionId,
        call.authorizationScopeDigest,
        { reason },
        this.now(),
      );
      return completed?.call ?? call;
    }
    if (call.status !== "executing" || !call.leaseOwner) return call;
    const completed = await this.deps.repairRepo.completeToolCall({
      callId: call.callId,
      executionId: call.executionId,
      authorizationScopeDigest: call.authorizationScopeDigest,
      leaseOwner: call.leaseOwner,
      status: call.isWrite ? "unknown" : "canceled",
      result: { reason, outcome: call.isWrite ? "unknown" : "canceled" },
      errorCode: call.isWrite ? "repair_write_outcome_unknown" : "repair_context_timeout",
      errorMessage: call.isWrite
        ? "写操作执行租约已过期，结果未知；不会自动重放"
        : "等待浏览器上下文超时",
      now: this.now(),
    });
    return completed?.call ?? call;
  }

  private async finishTerminatedToolCall(call: RepairToolCall): Promise<RepairToolCall> {
    if (call.status === "pending") {
      const completed = await this.deps.repairRepo.cancelPendingToolCall(
        call.callId,
        call.executionId,
        call.authorizationScopeDigest,
        { reason: "user_terminated", outcome: "canceled" },
        this.now(),
      );
      return completed?.call ?? call;
    }
    if (call.status !== "executing" || !call.leaseOwner) return call;
    const status: RepairToolCallTerminalStatus = call.isWrite ? "unknown" : "canceled";
    const completed = await this.deps.repairRepo.completeToolCall({
      callId: call.callId,
      executionId: call.executionId,
      authorizationScopeDigest: call.authorizationScopeDigest,
      leaseOwner: call.leaseOwner,
      status,
      result: { reason: "user_terminated", outcome: status },
      errorCode: call.isWrite ? "repair_write_outcome_unknown" : "repair_user_terminated",
      errorMessage: call.isWrite
        ? "用户终止任务时写操作仍在执行，结果未知；不会自动重放"
        : "用户已终止本次 Repair 实验",
      now: this.now(),
    });
    return completed?.call ?? call;
  }

  private async finishAbandonedStepCalls(
    taskId: string,
    stepId: string,
    reportCallId: string,
  ): Promise<void> {
    const activeCalls = await this.deps.repairRepo.listToolCalls(taskId, {
      stepId,
      statuses: ["pending", "executing"],
      limit: 500,
    });
    for (const call of activeCalls) {
      if (call.callId === reportCallId) continue;
      if (call.status === "pending") {
        await this.deps.repairRepo.cancelPendingToolCall(
          call.callId,
          call.executionId,
          call.authorizationScopeDigest,
          { reason: "step_failed", outcome: "canceled" },
          this.now(),
        ).catch(() => null);
        continue;
      }
      if (!call.leaseOwner) continue;
      const terminal: RepairToolCallTerminalStatus = call.isWrite ? "unknown" : "canceled";
      await this.deps.repairRepo.completeToolCall({
        callId: call.callId,
        executionId: call.executionId,
        authorizationScopeDigest: call.authorizationScopeDigest,
        leaseOwner: call.leaseOwner,
        status: terminal,
        result: { reason: "step_failed", outcome: terminal },
        errorCode: call.isWrite ? "repair_write_outcome_unknown" : "repair_step_failed",
        errorMessage: call.isWrite
          ? "Repair Step 失败时写操作仍在执行，结果未知；不会自动重放"
          : "Repair Step 已失败，遗留只读调用已取消",
        now: this.now(),
      }).catch(() => null);
    }
  }

  private async stopTerminatedAisJob(
    stepId: string,
    jobId: string | null,
  ): Promise<{ status: string; aisJobId: string | null }> {
    if (!jobId) return { status: "stop_pending", aisJobId: null };
    let result: { status: "remote_stopped" | "remote_stop_failed"; error?: string };
    try {
      await this.deps.ais.stopExecution(jobId);
      result = { status: "remote_stopped" };
    } catch {
      result = { status: "remote_stop_failed", error: "AIS stopExecution failed" };
    }
    try {
      await this.deps.repo.recordCancellationAttempt(stepId, result);
    } catch {
      // The Repair state is already terminal. A secondary audit failure must
      // not reactivate the workload or hide the remote stop outcome.
    }
    return { status: result.status, aisJobId: jobId };
  }

  private async cancelExpiredContextCall(taskId: string, toolCallIdValue: unknown): Promise<void> {
    const callId = toolCallIdValue == null ? null : requiredText(toolCallIdValue, "toolCallId", 64);
    const calls = callId
      ? [await this.deps.repairRepo.findToolCall(callId)].filter((call): call is RepairToolCall => Boolean(call))
      : await this.deps.repairRepo.listPendingToolCalls(taskId, 100);
    for (const call of calls) {
      if ((call.status === "pending" || call.status === "executing")
        && (call.deadlineAt == null || call.deadlineAt <= this.now())) {
        await this.finishExpiredToolCall(call, "context_timeout").catch(() => null);
      }
    }
  }

  private async assertExplicitRetry(taskId: string, stepId: string, actionId: string, retry: boolean): Promise<void> {
    const prior = (await this.deps.repairRepo.listToolCalls(taskId, { limit: 500 }))
      .filter((call) => call.stepId === stepId && call.actionId === actionId && call.status !== "canceled");
    if (prior.length && !retry) {
      throw new RepairError(409, "repair_action_retry_requires_confirmation", "该 action 已有执行记录；再次执行必须显式 retry");
    }
  }

  private async assertDependenciesSatisfied(taskId: string, stepId: string, action: RepairPlanAction): Promise<void> {
    const dependencies = action.dependsOn ?? [];
    if (!dependencies.length) return;
    const calls = await this.deps.repairRepo.listToolCalls(taskId, { limit: 500 });
    const missing = dependencies.filter((dependency) => !calls.some((call) =>
      call.stepId === stepId && call.actionId === dependency && call.status === "succeeded"));
    if (missing.length) {
      throw new RepairError(409, "repair_action_dependency_unsatisfied", `尚未成功执行依赖 action: ${missing.join(", ")}`);
    }
  }

  private async verifyDiscoveredIdentifiers(
    taskId: string,
    scopeDigest: string,
    requested: NonNullable<RepairLogSearchInput["discoveredIdentifiers"]>,
  ): Promise<RepairDiscoveredIdentifierCandidate[]> {
    if (!Array.isArray(requested) || requested.length > 5) {
      repairValidation("invalid_discovered_identifiers", "discoveredIdentifiers 最多 5 项");
    }
    const keyKinds = new Map<string, RepairDiscoveredIdentifierCandidate["kind"]>([
      ["bindingid", "bindingId"],
      ["publishid", "publishId"],
      ["deviceuuid", "deviceUuid"],
      ["sessionid", "sessionId"],
      ["traceid", "traceId"],
      ["taskid", "taskId"],
    ]);
    const result: RepairDiscoveredIdentifierCandidate[] = [];
    for (const item of requested) {
      const evidenceId = requiredText(item?.evidenceId, "evidenceId", 64);
      const kind = item?.kind;
      const value = requiredText(item?.value, "discoveredIdentifier.value", 256);
      if (![...keyKinds.values()].includes(kind)) {
        repairValidation("invalid_discovered_identifier", "discovered identifier kind 不受支持");
      }
      const evidence = await this.deps.repairRepo.findToolCall(evidenceId);
      if (!evidence || evidence.taskId !== taskId
        || evidence.authorizationScopeDigest !== scopeDigest
        || evidence.status !== "succeeded") {
        throw new RepairError(409, "invalid_identifier_evidence", "discovered identifier 的证据调用无效");
      }
      let matched = false;
      let visited = 0;
      const visit = (candidate: unknown, depth = 0) => {
        if (matched || candidate == null || depth > 6 || visited > 1_000) return;
        visited += 1;
        if (Array.isArray(candidate)) {
          candidate.slice(0, 100).forEach(child => visit(child, depth + 1));
          return;
        }
        if (typeof candidate !== "object") return;
        for (const [key, child] of Object.entries(candidate as Record<string, unknown>).slice(0, 200)) {
          const discoveredKind = keyKinds.get(key.toLowerCase().replaceAll(/[^a-z0-9]/g, ""));
          if (discoveredKind === kind && String(child) === value) {
            matched = true;
            return;
          }
          visit(child, depth + 1);
        }
      };
      visit(evidence.result);
      if (!matched) {
        throw new RepairError(409, "identifier_not_in_evidence", "discovered identifier 未出现在指定证据结果中");
      }
      result.push({ kind, value });
    }
    return result;
  }

  private assertSnapshotConfigured(): void {
    if (Object.values(this.deps.config.aisSnapshotIds)
      .some(snapshotId => !Number.isSafeInteger(snapshotId) || snapshotId <= 0)) {
      repairUnavailable("repair_ais_not_configured", "Repair AIS Snapshot ID 未配置");
    }
  }

  private actorViewAccess(task: EvolveTaskRow, actorUserId: string, isAdmin = false): RepairViewAccess {
    const isOwner = task.created_by === actorUserId && task.user_id === actorUserId;
    return {
      canOperate: isOwner || isAdmin,
      canManageShare: isOwner || isAdmin,
      canAdminOperate: isAdmin && !isOwner,
    };
  }

  private async view(
    task: EvolveTaskRow,
    access: RepairViewAccess,
  ): Promise<Record<string, unknown>> {
    const config = taskConfig(task);
    const canExecute = executionSupported(config);
    const decisionWindowExpired = config.execution.state === "waiting_decision"
      && ((config.execution.decisionDeadlineAt ?? 0) <= this.now()
        || config.execution.leaseExpiresAt <= this.now());
    const [step, steps, calls] = await Promise.all([
      this.deps.repo.findStep(config.current.stepId),
      this.deps.repo.listSteps(task.task_id),
      this.deps.repairRepo.listToolCalls(task.task_id, { limit: 500, recordKind: "source" }),
    ]);
    const conclusionClientRequestIds = calls.map((call) => `conclusion:${call.callId}`);
    const [conclusionCalls, hasMoreSourceCalls] = await Promise.all([
      conclusionClientRequestIds.length > 0
        ? this.deps.repairRepo.listToolCalls(task.task_id, {
          clientRequestIds: conclusionClientRequestIds,
          limit: 500,
          recordKind: "conclusion",
        })
        : [],
      calls.length === 500
        ? this.deps.repairRepo.listToolCalls(task.task_id, {
          afterId: calls.at(-1)!.id,
          limit: 1,
          recordKind: "source",
        }).then((items) => items.length > 0)
        : false,
    ]);
    let plan: RepairPlanArtifact | null = null;
    try {
      if (config.current.phase === "repair_plan" && step?.status === "succeeded") {
        plan = await this.loadAndValidatePlan(config, artifactDigest(parseOutput(step).artifactDigest));
      } else if (config.approvedPlan) {
        plan = await this.loadApprovedPlan(config);
      }
    } catch {
      plan = null;
    }
    let applyResult: unknown = null;
    if (config.current.phase === "repair_apply" && step?.status === "succeeded") {
      const key = config.current.artifacts.applyResult?.objectKey;
      if (key) {
        try {
          const object = await this.deps.store.getObject(key);
          if (object.content.length <= 1024 * 1024
            && sha256(object.content) === artifactDigest(parseOutput(step).artifactDigest)) {
            applyResult = redactValue(JSON.parse(object.content.toString("utf8")) as unknown);
          }
        } catch {
          applyResult = null;
        }
      }
    }
    const resumeAvailable = canExecute && (task.status === "waiting_context"
      || (Boolean(config.pendingDecision) && !this.executionCanClaim(config))
      || failedPlanCanResume(task, config, step));
    return {
      taskId: task.task_id,
      taskType: task.task_type,
      taskName: task.task_name,
      status: task.status,
      botId: task.bot_id,
      shared: config.shared === true,
      canOperate: access.canOperate,
      canManageShare: access.canManageShare,
      canAdminOperate: access.canAdminOperate === true,
      canTerminate: (access.canOperate || access.canAdminOperate === true) && new Set([
        "pending", "running", "waiting_approval", "waiting_acceptance", "waiting_context",
      ]).has(task.status),
      executionSupported: canExecute,
      executionBlock: canExecute ? null : {
        code: "repair_legacy_cfuse_engine_unsupported",
        message: "此历史 Repair 使用已停用的 Codex Engine，只能查看或采纳已有结果，不能继续执行",
      },
      targetEnvironment: config.authorizationScope.environment,
      controlPlaneEnvironment: resolveRepairTaskControlPlaneEnvironment(
        config,
        this.deps.config.controlPlaneEnvironment,
      ),
      diagnosticMode: config.diagnosticMode ?? "observe",
      agentMode: config.agentMode,
      llmUseDefault: config.llmUseDefault,
      llmModel: config.llmModel,
      openclawUsesCustomApiKey: config.openclawUsesCustomApiKey,
      cfuseEngine: config.cfuseEngine,
      cfuseModel: config.cfuseModel,
      issue: config.issue,
      target: {
        environment: config.runtimeTarget.target.environment,
        botId: config.runtimeTarget.target.botId,
      },
      ...(config.insightSource ? { insightSource: config.insightSource } : {}),
      currentStep: step ? {
        stepId: step.step_id,
        stepNo: step.step_no,
        attempt: config.current.attempt,
        phase: step.step_type,
        status: step.status,
        aisJobId: step.bot_run_id,
        output: browserStepOutput(step),
        summary: step.summary == null ? null : redactText(step.summary, 4_000),
        error: step.error_message == null ? null : redactText(step.error_message, 4_000),
        failure: browserStepFailure(step),
      } : null,
      steps: steps.map((item) => ({
        stepId: item.step_id,
        stepNo: item.step_no,
        phase: item.step_type,
        status: item.status,
        aisJobId: item.bot_run_id,
        summary: item.summary == null ? null : redactText(item.summary, 4_000),
        output: browserStepOutput(item),
        error: item.error_message == null ? null : redactText(item.error_message, 4_000),
        failure: browserStepFailure(item),
      })),
      history: config.history.map(item => ({
        stepId: item.stepId,
        stepNo: item.stepNo,
        attempt: item.attempt,
        phase: item.phase,
        status: item.status,
        artifactDigest: item.artifactDigest,
        feedback: item.feedback,
      })),
      approvedPlan: browserApprovedPlan(config.approvedPlan),
      pendingDecision: browserPendingDecision(config.pendingDecision, true),
      plan: browserPlan(plan, true),
      applyResult,
      execution: {
        state: decisionWindowExpired ? "ended" : config.execution.state,
        phase: config.execution.phase,
        leaseExpiresAt: config.execution.leaseExpiresAt,
        decisionDeadlineAt: config.execution.decisionDeadlineAt,
        decisionWindowExpired,
      },
      toolCalls: browserToolCalls([...calls, ...conclusionCalls], true, access.canOperate && canExecute),
      toolCallAuditTruncated: hasMoreSourceCalls,
      resumeAvailable,
      canResume: access.canOperate && resumeAvailable,
      error: task.error_message == null ? null : redactText(task.error_message, 4_000),
      createdAt: task.gmt_create,
      updatedAt: task.gmt_modified,
    };
  }
}

export { REPAIR_PARAMS_KEY };
