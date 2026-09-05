import { randomUUID } from "node:crypto";
import { Router, type Request } from "express";
import { asyncHandler } from "../middleware/async-handler.js";
import type { EvolveRepository, EvolveTaskRow } from "../repositories/evolve-repository.js";
import type { MistOssObjectStore } from "../services/object-storage/oss-object-store.js";
import { AistudioService, SESSION_ANALYSIS_SNAPSHOT_ID } from "../services/aistudio-service.js";
import { AisTaskRunner, type AisTaskDefinition } from "../services/ais-task-runner.js";
import { getClawWebPublicBaseUrl, getCurrentEnv } from "../env.js";

type Config = {
  mode: "ANALYZE_SINGLE" | "EXPORT_SINGLE" | "EXPORT_ALL"; stage: "all" | "draft" | "service"; engineType: "openclaw";
  sessionIdentifier?: string; sessionId?: string; sessionKey?: string; question?: string; attempt: number; stepId?: string;
  sessionLookbackDays?: number | null;
  llmAnalysis?: boolean; llmUseDefault?: boolean; llmModel?: string; llmApiKey?: string;
  clawwebUrl?: string; callbackUrl?: string;
  shared?: boolean;
  artifactUploadMode?: "broker" | "none";
  artifacts: Record<string, { objectKey: string; uploadUrl?: string }>;
};

function isSingleSessionMode(mode: Config["mode"]): boolean { return mode !== "EXPORT_ALL"; }

function actor(req: Request): string | null {
  return req.header("X-Staff-Id")?.trim() || req.header("X-User-Id")?.trim()
    || ((req.get("host") ?? "").includes("localhost") ? "dev_local" : null);
}
function parse<T>(value: string | null): T | null { try { return value ? JSON.parse(value) as T : null; } catch { return null; } }
function param(value: string | string[]): string { return Array.isArray(value) ? value[0] ?? "" : value; }
function configOf(task: EvolveTaskRow): Config { return JSON.parse(task.config_json) as Config; }
function canReadTask(req: Request, task: EvolveTaskRow): boolean {
  const userId = actor(req);
  return task.user_id === userId || task.created_by === userId || req.isClawEvolveAdmin === true
    || configOf(task).shared === true;
}
function safeSessionFilename(value: string | undefined): string {
  const normalized = (value ?? "session").replace(/[^\p{L}\p{N}_.-]+/gu, "_").replace(/^\.+/, "").slice(0, 240);
  return `${normalized || "session"}.jsonl`;
}
function attachmentHeader(filename: string): string {
  const fallback = filename.replace(/[^A-Za-z0-9_.-]+/g, "_") || "session.jsonl";
  return `attachment; filename="${fallback}"; filename*=UTF-8''${encodeURIComponent(filename)}`;
}
function sessionPreview(content: Buffer) {
  const events: unknown[] = []; let eventCount = 0; let parseErrorCount = 0;
  for (const line of content.toString("utf8").split(/\r?\n/)) {
    if (!line.trim()) continue;
    eventCount += 1;
    try {
      const event = JSON.parse(line);
      if (events.length < 200) events.push(event);
    } catch {
      parseErrorCount += 1;
      if (events.length < 200) events.push({ type: "parse_error", raw: line.slice(0, 1000) });
    }
  }
  return { events, eventCount, parseErrorCount, truncated: eventCount > events.length };
}
async function latestStep(repo: EvolveRepository, taskId: string) { const steps = await repo.listSteps(taskId); return steps[steps.length - 1]; }
function sessionArtifactUploadMode(): "broker" | "none" {
  // The stable.alipay.net local development domain may inherit pre/prod-like
  // environment variables. A dev-server process must never depend on Mist OSS;
  // local bring-up only verifies that the AIS snapshot can be launched.
  const isLocalDevProcess = process.env.npm_lifecycle_event === "dev"
    || process.env.NODE_ENV === "development";
  if (isLocalDevProcess || getCurrentEnv() === "dev") return "none";
  const configured = process.env.CLAWWEB_SESSION_ANALYSIS_SIGNED_UPLOAD?.trim().toLowerCase();
  if (configured === "true") return "broker";
  if (configured === "false") return "none";
  return "broker";
}
function validateAisResult(payload: Record<string, unknown>, config: Config & { taskId: string }) {
  if (payload.success !== true) throw new Error("AIS result.success 不是 true");
  if (payload.taskId !== config.taskId || payload.analysisId !== config.taskId) throw new Error("AIS 结果 Task 身份不匹配");
  const uploaded = payload.artifacts;
  if (!uploaded || typeof uploaded !== "object" || Array.isArray(uploaded)) throw new Error("AIS 结果缺少 artifacts");
  for (const [name, expected] of Object.entries(config.artifacts)) {
    if (name === "result") continue;
    const actual = (uploaded as Record<string, unknown>)[name];
    if (!actual || typeof actual !== "object" || Array.isArray(actual)) throw new Error(`AIS 结果缺少 ${name} 产物`);
    const item = actual as Record<string, unknown>;
    if (item.objectKey !== expected.objectKey) throw new Error(`AIS ${name} 产物路径不匹配`);
    if (!Number.isSafeInteger(item.size) || Number(item.size) <= 0) throw new Error(`AIS ${name} 产物 size 无效`);
    if (typeof item.sha256 !== "string" || !/^[a-f0-9]{64}$/.test(item.sha256)) throw new Error(`AIS ${name} 产物 sha256 无效`);
  }
}
function taskView(task: EvolveTaskRow, step?: Awaited<ReturnType<EvolveRepository["findStep"]>>) {
  const config = configOf(task); const output = parse<Record<string, unknown>>(step?.output_json ?? null);
  const botResponse = parse<Record<string, unknown>>(step?.bot_response_json ?? null);
  return { analysisId: task.task_id, taskType: task.task_type, taskName: task.task_name, botId: task.bot_id,
    userId: task.user_id, createdBy: task.created_by, remark: task.remark,
    mode: config.mode, stage: config.stage, engineType: config.engineType,
    sessionIdentifier: config.sessionIdentifier ?? config.sessionId ?? config.sessionKey ?? null,
    shared: config.shared === true,
    sessionId: config.sessionId ?? null,
    sessionKey: config.sessionKey ?? null, question: config.question ?? null,
    sessionLookbackDays: config.mode === "ANALYZE_SINGLE" ? config.sessionLookbackDays ?? 1 : null,
    llmAnalysis: config.mode === "ANALYZE_SINGLE" ? config.llmAnalysis !== false : false,
    llmUseDefault: config.llmUseDefault !== false,
    llmModel: config.llmUseDefault === false ? config.llmModel ?? null : null,
    status: task.status,
    phase: step?.status ?? task.status, aisJobId: step?.bot_run_id ?? null,
    aisJobUrl: step?.bot_run_id ? `https://aistudio.alipay.com/project/job/detail/${step.bot_run_id}` : null,
    stepId: step?.step_id ?? null, stepCommand: step?.command ?? null,
    stepResponse: botResponse, stepOutput: output,
    stepCreatedAt: step?.gmt_create ?? null, stepStartedAt: step?.started_at ?? null,
    stepCompletedAt: step?.completed_at ?? null,
    errorCode: step?.error_code ?? null, summary: step?.summary ?? null,
    result: output, error: step?.error_message ?? task.error_message, gmtCreate: task.gmt_create, gmtModified: task.gmt_modified };
}

export function createSessionAnalysisRouter(
  repo: EvolveRepository | null,
  uploadStore: MistOssObjectStore,
  ais: AistudioService,
  downloadStore: MistOssObjectStore = uploadStore,
): Router {
  const router = Router();
  const artifactUploadMode = sessionArtifactUploadMode();
  const definition: AisTaskDefinition<Config> = {
    taskTypes: ["session_analysis", "session_export"],
    snapshotId: SESSION_ANALYSIS_SNAPSHOT_ID,
    // Phase-3 bring-up mode: launch AIStudio without requiring local ClawWeb
    // to access Mist. AIS writes to its workspace; OSS result convergence is
    // enabled after claw-validation receives its direct OSS configuration.
    // URLs are issued just-in-time after Python has produced each file. This
    // matches the Optimize/Pack artifact flow and keeps AIS dispatch OSS-free.
    artifactTransport: "none",
    dispatchMetadata: config => ({
      taskId: (config as Config & { taskId: string }).taskId,
      stepId: config.stepId,
      taskType: config.mode === "ANALYZE_SINGLE" ? "session_analysis" : "session_export",
      action: config.mode === "ANALYZE_SINGLE" ? "analysis" : "package",
      target: {
        userId: (config as Config & { userId: string }).userId,
        botId: (config as Config & { botId: string }).botId,
        stage: config.stage,
        engineType: config.engineType,
      },
      sessionInput: config.sessionId
        ? { type: "session_id", value: config.sessionId }
        : config.sessionKey
          ? { type: "session_key", value: config.sessionKey }
          : config.sessionIdentifier
            ? { type: "auto", value: config.sessionIdentifier }
          : { type: "all" },
      artifactUploadMode: config.artifactUploadMode ?? "none",
      clawwebUrl: config.clawwebUrl,
    }),
    buildGlobalParams: (config, uploadArtifacts) => {
      const taskParams = { schemaVersion: "clawevolve-task/v1",
        taskType: config.mode === "ANALYZE_SINGLE" ? "session_analysis" : "session_export",
        taskId: (config as Config & { taskId: string }).taskId,
        stepId: config.stepId,
        attempt: config.attempt,
        execution: { executor: "ais", action: config.mode === "ANALYZE_SINGLE" ? "analysis" : "package" },
        input: { userId: (config as Config & { userId: string }).userId,
          botId: (config as Config & { botId: string }).botId,
          stage: config.stage, isServiceBot: config.stage === "service", engineType: "openclaw", env: "prod", entityType: "staff",
          ...(config.sessionIdentifier ? { sessionIdentifier: config.sessionIdentifier } : {}),
          ...(config.sessionId ? { sessionId: config.sessionId } : {}), ...(config.sessionKey ? { sessionKey: config.sessionKey } : {}),
          ...(config.question ? { question: config.question } : {}),
          ...(config.mode === "ANALYZE_SINGLE" ? { sessionLookbackDays: config.sessionLookbackDays ?? 1 } : {}),
          llmAnalysis: config.llmAnalysis !== false,
          llmUseDefault: config.llmUseDefault !== false,
          ...(config.llmUseDefault === false && config.llmModel ? { llmModel: config.llmModel } : {}),
          ...(config.llmUseDefault === false && config.llmApiKey ? { llmApiKey: config.llmApiKey } : {}) },
        runtime: {
          outputDir: `/tmp/${(config as Config & { taskId: string }).taskId}`,
          configPath: process.env.CLAWWEB_SESSION_ANALYSIS_LLM_CONFIG_PATH?.trim() || "../config.yaml",
          clawwebUrl: config.clawwebUrl,
          artifactUploadMode: config.artifactUploadMode ?? "none",
          ...(config.callbackUrl ? { callbackUrl: config.callbackUrl } : {}),
          artifacts: config.artifactUploadMode === "broker" ? config.artifacts : uploadArtifacts,
        } };
      return { "${clawevolve_params}": JSON.stringify(taskParams) };
    },
  };
  console.info(`[session-analysis] artifact upload=${artifactUploadMode}, env=${getCurrentEnv()}, devProcess=${process.env.npm_lifecycle_event === "dev" || process.env.NODE_ENV === "development"}`);
  const runner = repo ? new AisTaskRunner(repo, uploadStore, ais, definition) : null;
  // AIS reports business progress and terminal state through the report endpoint.
  // ClawWeb deliberately does not poll AIStudio or mutate state during detail reads.
  router.post("/internal/:id/steps/:stepId/artifacts/:name/upload-url", asyncHandler(async (req, res) => {
    if (!repo) return res.status(503).json({ error: "任务数据库不可用" });
    const taskId = param(req.params.id); const stepId = param(req.params.stepId); const name = param(req.params.name);
    const task = await repo.findTask(taskId); const step = await repo.findStep(stepId);
    if (!task || !step || step.task_id !== taskId || !["session_analysis", "session_export"].includes(task.task_type))
      return res.status(404).json({ error: "Session Artifact 任务不存在" });
    if (["succeeded", "failed", "canceled"].includes(step.status))
      return res.status(409).json({ error: "终态任务不再签发上传 URL" });
    const target = configOf(task).artifacts[name];
    if (!target) return res.status(404).json({ error: "Artifact 不存在" });
    const expectedContentTypes: Record<string, string> = {
      raw: isSingleSessionMode(configOf(task).mode) ? "application/x-ndjson" : "application/gzip",
      manifest: "application/json", report: "text/markdown; charset=utf-8",
      analysis: "application/json", result: "application/json",
    };
    const size = Number(req.body?.size); const sha256 = String(req.body?.sha256 ?? "");
    const contentType = String(req.body?.contentType ?? "");
    if (!Number.isSafeInteger(size) || size <= 0 || !/^[a-f0-9]{64}$/.test(sha256))
      return res.status(400).json({ error: "Artifact size 或 sha256 不合法" });
    if (contentType !== expectedContentTypes[name])
      return res.status(422).json({ error: "Artifact Content-Type 不合法" });
    const headers = {
      "Content-Type": contentType,
      ...(name === "raw" ? {
        "Content-Disposition": attachmentHeader(
          isSingleSessionMode(configOf(task).mode)
            ? safeSessionFilename(configOf(task).sessionIdentifier || configOf(task).sessionId || configOf(task).sessionKey)
            : "session.tar.gz",
        ),
      } : {}),
    };
    const url = await uploadStore.createSignedUrl(target.objectKey, "PUT", 86_400, headers);
    res.json({ method: "PUT", url, headers, objectKey: target.objectKey, size, sha256, contentType, expiresInSeconds: 86_400 });
  }));
  router.get("/bots", asyncHandler(async (req, res) => {
    if (!repo) return res.status(503).json({ error: "任务数据库不可用" });
    const currentUserId = actor(req); if (!currentUserId) return res.status(401).json({ error: "未识别当前用户" });
    const requestedUserId = String(req.query.userId ?? currentUserId).trim() || currentUserId;
    if (requestedUserId !== currentUserId && !req.isClawEvolveAdmin)
      return res.status(403).json({ error: "只有 ClawEvolve 管理员可以查询其他用户的 Bot" });
    res.json({ userId: requestedUserId, bots: await repo.listAccessibleEvolveBots(requestedUserId) });
  }));
  router.post("/internal/:id/steps/:stepId/report", asyncHandler(async (req, res) => {
    if (!repo) return res.status(503).json({ error: "任务数据库不可用" });
    const taskId = param(req.params.id); const stepId = param(req.params.stepId);
    const task = await repo.findTask(taskId); const step = await repo.findStep(stepId);
    if (!task || !step || step.task_id !== taskId
      || !["session_analysis", "session_export"].includes(task.task_type)) {
      return res.status(404).json({ error: "Session Analysis Step 不存在" });
    }
    const status = String(req.body?.status ?? "").toLowerCase();
    if (!["running", "succeeded", "failed"].includes(status)) {
      return res.status(400).json({ error: "status 必须为 running/succeeded/failed" });
    }
    const output = req.body?.output;
    const error = req.body?.error;
    if (output != null && (typeof output !== "object" || Array.isArray(output))) {
      return res.status(400).json({ error: "output 必须为对象" });
    }
    if (["succeeded", "failed", "canceled"].includes(step.status)) {
      if (step.status === status) return res.json({ ok: true, duplicate: true, taskId, stepId, status });
      return res.status(409).json({ error: `Step 已处于终态: ${step.status}` });
    }
    if (status === "running") {
      await repo.updateStepStatus(stepId, {
        status: "running",
        summary: typeof req.body?.summary === "string" ? req.body.summary : undefined,
        ...(output ? { output: output as Record<string, unknown> } : {}),
      });
      return res.json({ ok: true, duplicate: false, taskId, stepId, status });
    }
    if (status === "failed") {
      const failure = error && typeof error === "object" && !Array.isArray(error)
        ? error as Record<string, unknown> : {};
      await repo.updateStepStatus(stepId, {
        status: "failed",
        errorCode: typeof failure.code === "string" ? failure.code : "AIS_EXECUTION_FAILED",
        errorMessage: typeof failure.message === "string" ? failure.message : "AIS 执行失败",
        retryable: typeof failure.retryable === "boolean" ? failure.retryable : true,
        ...(output ? { output: output as Record<string, unknown> } : {}),
      });
      return res.json({ ok: true, duplicate: false, taskId, stepId, status });
    }
    if (!output) return res.status(422).json({ error: "成功回调必须携带 output" });
    const config = configOf(task);
    if (output.taskId !== taskId || output.analysisId !== taskId || output.success !== true) {
      return res.status(422).json({ error: "AIS 成功结果身份或状态不匹配" });
    }
    if (config.artifactUploadMode === "broker") validateAisResult(
      output as Record<string, unknown>, config as Config & { taskId: string },
    );
    await repo.updateStepStatus(stepId, {
      status: "succeeded",
      summary: typeof req.body?.summary === "string" ? req.body.summary : "AIS 任务执行成功",
      output: output as Record<string, unknown>,
    });
    await repo.completeTask(taskId);
    res.json({ ok: true, duplicate: false, taskId, stepId, status });
  }));
  router.post("/", asyncHandler(async (req, res) => {
    if (!repo) return res.status(503).json({ error: "任务数据库不可用" });
    const creatorUserId = actor(req); if (!creatorUserId) return res.status(401).json({ error: "未识别当前用户" });
    const body = req.body as Record<string, unknown>; const botId = String(body.botId ?? "").trim();
    const botEnv = String(body.botEnv ?? "").trim();
    const requestedUserId = String(body.targetUserId ?? creatorUserId).trim() || creatorUserId;
    if (requestedUserId !== creatorUserId && !req.isClawEvolveAdmin)
      return res.status(403).json({ error: "只有 ClawEvolve 管理员可以为其他用户创建任务" });
    const mode = body.mode === "EXPORT_ALL" ? "EXPORT_ALL" : "ANALYZE_SINGLE";
    const stage = body.stage === "draft" || body.stage === "service" ? body.stage : "all";
    const sessionId = typeof body.sessionId === "string" ? body.sessionId.trim() : "";
    const sessionKey = typeof body.sessionKey === "string" ? body.sessionKey.trim() : "";
    const sessionIdentifier = typeof body.sessionIdentifier === "string" ? body.sessionIdentifier.trim() : "";
    const question = typeof body.question === "string" ? body.question.trim() : "";
    const taskName = typeof body.taskName === "string" ? body.taskName.trim() : "";
    const remark = typeof body.remark === "string" ? body.remark.trim() : "";
    const llmAnalysis = mode === "ANALYZE_SINGLE" ? body.llmAnalysis !== false : false;
    const llmUseDefault = llmAnalysis ? body.llmUseDefault !== false : true;
    const llmModel = typeof body.llmModel === "string" ? body.llmModel.trim() : "";
    const llmApiKey = typeof body.llmApiKey === "string" ? body.llmApiKey.trim() : "";
    const sessionLookbackDays = body.sessionLookbackDays === null ? null : Number(body.sessionLookbackDays ?? 1);
    if (!botId) return res.status(400).json({ error: "botId 必填" });
    if (!taskName || taskName.length > 128) return res.status(400).json({ error: "taskName 必填且不能超过 128 字符" });
    if (remark.length > 1000) return res.status(400).json({ error: "remark 不能超过 1000 字符" });
    if (mode === "ANALYZE_SINGLE"
      && Number(Boolean(sessionIdentifier)) + Number(Boolean(sessionId)) + Number(Boolean(sessionKey)) !== 1)
      return res.status(400).json({ error: "单 Session 分析必须填写一个 Session 标识" });
    if (mode === "EXPORT_ALL" && (sessionIdentifier || sessionId || sessionKey || question))
      return res.status(400).json({ error: "多 Session 导出不能填写 Session 标识或分析问题" });
    if ((!llmAnalysis || llmUseDefault) && (llmModel || llmApiKey))
      return res.status(400).json({ error: "默认或未启用 LLM 时不能填写自定义模型或 Token" });
    if (llmAnalysis && !llmUseDefault && !llmModel)
      return res.status(400).json({ error: "自定义 LLM 必须填写模型名称" });
    if (llmModel.length > 128) return res.status(400).json({ error: "LLM 模型名称不能超过 128 字符" });
    if (llmApiKey.length > 8192) return res.status(400).json({ error: "LLM Token 不能超过 8192 字符" });
    if (mode === "ANALYZE_SINGLE" && sessionLookbackDays !== null
      && (!Number.isSafeInteger(sessionLookbackDays) || sessionLookbackDays < 1 || sessionLookbackDays > 365))
      return res.status(400).json({ error: "Session 时间范围必须为 1 到 365 天，或选择全部" });
    const accessible = requestedUserId === creatorUserId
      ? await repo.resolveAccessibleEvolveBotRuntime(creatorUserId, botId, botEnv)
      : null;
    const userId = accessible?.ownerId ?? requestedUserId;
    const runtime = accessible?.runtime ?? await repo.resolveEvolveBotRuntime(requestedUserId, botId, botEnv);
    if (!runtime && !req.isClawEvolveAdmin) return res.status(403).json({ error: "无权访问该 Bot 或 Bot 不存在" });
    if (!runtime) console.warn(`[session-analysis] admin manual Bot target: creator=${creatorUserId}, user=${userId}, bot=${botId}`);
    if (runtime?.activeEngine && runtime.activeEngine.toLowerCase() !== "openclaw")
      return res.status(422).json({ error: `一期仅支持 OpenClaw，当前为 ${runtime.activeEngine}` });
    const taskId = `SA-${randomUUID()}`; const stepId = `${taskId}-AIS`; const attempt = 1;
    const prefix = `evolution/${taskId}/session-analysis/attempt-${attempt}`;
    const names = mode === "ANALYZE_SINGLE" ? ["raw", "manifest", "report", "analysis", "result"] : ["raw", "manifest", "result"];
    const suffix: Record<string, string> = {
      raw: mode === "ANALYZE_SINGLE" ? safeSessionFilename(sessionIdentifier || sessionId || sessionKey) : "session.tar.gz",
      manifest: "session.manifest.json", report: "report.md", analysis: "analysis.json", result: "result.json",
    };
    const clawwebUrl = getClawWebPublicBaseUrl();
    const artifacts = Object.fromEntries(names.map((name) => [name, {
      objectKey: `${prefix}/${suffix[name]}`,
    }]));
    const config = { mode, stage, engineType: "openclaw", userId, botId, botEnv, taskId, stepId, clawwebUrl,
      ...(sessionIdentifier ? { sessionIdentifier } : {}), ...(sessionId ? { sessionId } : {}),
      ...(sessionKey ? { sessionKey } : {}), ...(question ? { question } : {}),
      llmAnalysis, llmUseDefault, ...(mode === "ANALYZE_SINGLE" ? { sessionLookbackDays } : {}),
      ...(!llmUseDefault ? { llmModel, ...(llmApiKey ? { llmApiKey } : {}) } : {}),
      attempt, artifactUploadMode, artifacts };
    await repo.createTask({ taskId, taskType: mode === "ANALYZE_SINGLE" ? "session_analysis" : "session_export",
      userId, botId, taskName, remark: remark || undefined, configJson: JSON.stringify(config), createdBy: creatorUserId });
    await repo.createStep({ stepId, taskId, stepType: "session_ais", stepNo: 1, command: mode === "ANALYZE_SINGLE" ? "analysis" : "package" });
    try {
      const jobId = await runner!.dispatch((await repo.findTask(taskId))!, stepId, creatorUserId);
      return res.status(202).json({ analysisId: taskId, status: "running", aisJobId: jobId });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      await repo.markDispatchFailed(stepId, message);
      console.error(`[session-analysis] dispatch failed task=${taskId}: ${message}`);
      return res.status(502).json({ error: "AIS_DISPATCH_FAILED", message: "分析任务调度失败，任务已记录，可在任务中心重试",
        reason: message.slice(0, 1500), analysisId: taskId });
    }
  }));

  router.get("/", asyncHandler(async (req, res) => { if (!repo) return res.status(503).json({ error: "任务数据库不可用" }); const userId = actor(req); if (!userId) return res.status(401).json({ error: "未识别当前用户" });
    const tasks = await repo.listTasksByUserAndTypes(userId, ["session_analysis", "session_export"], 100); res.json({ items: await Promise.all(tasks.map(async task => {
      const step = await latestStep(repo, task.task_id);
      return taskView((await repo.findTask(task.task_id))!, step);
    })) }); }));
  router.get("/:id", asyncHandler(async (req, res) => { if (!repo) return res.status(503).json({ error: "任务数据库不可用" }); const task = await repo.findTask(param(req.params.id)); if (!task || !["session_analysis", "session_export"].includes(task.task_type)) return res.status(404).json({ error: "任务不存在" }); if (!canReadTask(req, task)) return res.status(403).json({ code: "TASK_NOT_SHARED", error: "权限不足，请联系任务 Owner 开启分享" });
    const step = await latestStep(repo, task.task_id);
    const refreshedTask = (await repo.findTask(task.task_id))!;
    const view = taskView(refreshedTask, step); const cfg = configOf(task);
    const botName = (await repo.listEvolveBots(task.user_id)).find((bot) => bot.botId === task.bot_id)?.botName ?? null;
    const output = parse<Record<string, unknown>>(step?.output_json ?? null);
    const uploaded = output?.artifacts && typeof output.artifacts === "object" && !Array.isArray(output.artifacts)
      ? output.artifacts as Record<string, unknown> : {};
    let reportMarkdown: string | null = null;
    let preview: ReturnType<typeof sessionPreview> | null = null;
    if (view.status === "completed" && uploaded.report && cfg.artifactUploadMode === "broker") {
      reportMarkdown = (await uploadStore.getObject(cfg.artifacts.report.objectKey)).content.toString("utf8");
    }
    if (view.status === "completed" && isSingleSessionMode(cfg.mode) && uploaded.raw
      && cfg.artifactUploadMode === "broker" && cfg.artifacts.raw.objectKey.endsWith(".jsonl")) {
      preview = sessionPreview((await uploadStore.getObject(cfg.artifacts.raw.objectKey)).content);
    }
    res.json({ ...view, botName, reportMarkdown, sessionPreview: preview, artifacts: Object.keys(uploaded) }); }));
  router.post("/:id/retry", asyncHandler(async (req, res) => { if (!repo) return res.status(503).json({ error: "任务数据库不可用" }); const userId = actor(req); const task = await repo.findTask(param(req.params.id));
    if (!userId) return res.status(401).json({ error: "未识别当前用户" });
    if (!task || (task.created_by !== userId && !req.isClawEvolveAdmin) || !["session_analysis", "session_export"].includes(task.task_type)) return res.status(404).json({ error: "任务不存在" });
    if (task.status !== "failed") return res.status(409).json({ error: "只有失败任务可以重试" });
    const previous = configOf(task); const attempt = previous.attempt + 1;
    const stepNo = (await repo.listSteps(task.task_id)).length + 1; const stepId = `${task.task_id}-AIS-${attempt}`;
    const clawwebUrl = previous.clawwebUrl ?? getClawWebPublicBaseUrl();
    const artifacts = Object.fromEntries(Object.entries(previous.artifacts).map(([name, item]) => {
      let objectKey = item.objectKey.replace(/\/attempt-\d+\//, `/attempt-${attempt}/`);
      if (name === "raw") objectKey = objectKey
        .replace(/\/[^/]+\.(?:jsonl|tar\.gz)$/, isSingleSessionMode(previous.mode)
          ? `/${safeSessionFilename(previous.sessionIdentifier || previous.sessionId || previous.sessionKey)}` : "/session.tar.gz");
      if (name === "manifest") objectKey = objectKey.replace(/\/raw\.manifest\.json$/, "/session.manifest.json");
      return [name, { objectKey }];
    }));
    const next = { ...previous, attempt, stepId, clawwebUrl, callbackUrl: undefined, artifacts }; await repo.prepareTaskRetry(task.task_id, next);
    await repo.createStep({ stepId, taskId: task.task_id, stepType: "session_ais", stepNo, command: previous.mode === "ANALYZE_SINGLE" ? "analysis" : "package" });
    try { const jobId = await runner!.dispatch((await repo.findTask(task.task_id))!, stepId, userId); res.status(202).json({ analysisId: task.task_id, attempt, aisJobId: jobId }); }
    catch (error) { await repo.markDispatchFailed(stepId, error instanceof Error ? error.message : String(error)); throw error; }
  }));
  router.get("/:id/artifacts/:name/download-url", asyncHandler(async (req, res) => { if (!repo) return res.status(503).json({ error: "任务数据库不可用" }); const task = await repo.findTask(param(req.params.id)); if (!task || !["session_analysis", "session_export"].includes(task.task_type)) return res.status(404).json({ error: "任务不存在" }); if (!canReadTask(req, task)) return res.status(403).json({ code: "TASK_NOT_SHARED", error: "权限不足，请联系任务 Owner 开启分享" }); const name = param(req.params.name); const cfg = configOf(task); const item = cfg.artifacts[name]; if (!item) return res.status(404).json({ error: "产物不存在" }); const step = await latestStep(repo, task.task_id); const output = parse<Record<string, unknown>>(step?.output_json ?? null); const resolvedSessionId = typeof output?.sessionId === "string" ? output.sessionId : undefined; const filename = name === "raw" ? (isSingleSessionMode(cfg.mode) ? safeSessionFilename(resolvedSessionId || cfg.sessionIdentifier || cfg.sessionId || cfg.sessionKey) : "session.tar.gz") : item.objectKey.split("/").at(-1) || name; res.json({ url: await downloadStore.createSignedUrl(item.objectKey, "GET", 300), filename, expiresInSeconds: 300 }); }));
  return router;
}
