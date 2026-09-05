import { createHash } from "node:crypto";
import { Router, type Request, type Response } from "express";
import { getClawWebPublicBaseUrl } from "../env.js";
import { asyncHandler } from "../middleware/async-handler.js";
import type {
  EvolveRepository,
  EvolveStepRow,
  EvolveTaskRow,
} from "../repositories/evolve-repository.js";
import {
  AistudioService,
  SESSION_ANALYSIS_SNAPSHOT_ID,
} from "../services/aistudio-service.js";
import type { MistOssObjectStore } from "../services/object-storage/oss-object-store.js";

type ExportScope = "single" | "bot";
type ExportStage = "all" | "draft" | "service";

type SessionExportConfig = {
  source: "integration_api";
  apiVersion: "session-export/v1";
  mode: "EXPORT_SINGLE" | "EXPORT_ALL";
  exportScope: ExportScope;
  stage: ExportStage;
  engineType: "openclaw";
  userId: string;
  requestedUserId: string;
  botId: string;
  taskId: string;
  stepId: string;
  attempt: number;
  sessionIdentifier?: string;
  clawwebUrl: string;
  artifactUploadMode: "broker";
  llmAnalysis: false;
  llmUseDefault: true;
  artifacts: Record<string, { objectKey: string }>;
  integration: {
    requestHash: string;
    idempotencyKeyHash: string;
  };
};

type SignedUrlStore = Pick<MistOssObjectStore, "createSignedUrl">;

export type SessionExportIntegrationRouterDeps = {
  repo: EvolveRepository | null;
  ais: Pick<AistudioService, "execute">;
  officeDownloadStore: SignedUrlStore;
  productionDownloadStore: SignedUrlStore;
  now?: () => number;
};

type NormalizedCreateRequest = {
  exportScope: ExportScope;
  target: {
    userId: string;
    botId: string;
    stage: ExportStage;
    engineType: "openclaw";
  };
  sessionIdentifier?: string;
  requestName: string;
};

const API_VERSION = "session-export/v1";
const DOWNLOAD_URL_TTL_SECONDS = 300;

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function stringValue(value: unknown, maxLength: number): string {
  const normalized = typeof value === "string" ? value.trim() : "";
  return normalized.length <= maxLength ? normalized : "";
}

function sha256(value: string): string {
  return createHash("sha256").update(value).digest("hex");
}

function safeFilenamePart(value: string): string {
  return value.replace(/[^A-Za-z0-9_.-]+/g, "_").replace(/^\.+/, "").slice(0, 128) || "session";
}

function publicErrorMessage(value: string | null | undefined): string {
  const message = value?.trim() || "Session 导出失败";
  return message
    .replace(/\/(?:home|tmp|ossfs|Users)\/[^\s"']+/g, "[internal-path]")
    .slice(0, 1_000);
}

function parseConfig(task: EvolveTaskRow): SessionExportConfig | null {
  try {
    const parsed = JSON.parse(task.config_json) as unknown;
    return objectValue(parsed) as SessionExportConfig | null;
  } catch {
    return null;
  }
}

function parseOutput(step: EvolveStepRow | undefined): Record<string, unknown> | null {
  try {
    return step?.output_json ? objectValue(JSON.parse(step.output_json) as unknown) : null;
  } catch {
    return null;
  }
}

function isoTime(value: number | string | null | undefined): string | null {
  if (value == null) return null;
  const date = typeof value === "number"
    ? new Date(value < 10_000_000_000 ? value * 1_000 : value)
    : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}

function error(res: Response, status: number, code: string, message: string): void {
  res.status(status).json({ code, message });
}

function normalizeCreateRequest(body: unknown): NormalizedCreateRequest | null {
  const record = objectValue(body);
  const target = objectValue(record?.target);
  if (!record || !target) return null;
  const exportScope = record.exportScope === "single" || record.exportScope === "bot"
    ? record.exportScope
    : null;
  const userId = stringValue(target.userId, 128);
  const botId = stringValue(target.botId, 128);
  const engineType = target.engineType == null ? "openclaw" : stringValue(target.engineType, 32);
  const rawStage = target.stage == null && exportScope === "single" ? "all" : target.stage;
  const stage = rawStage === "all" || rawStage === "draft" || rawStage === "service"
    ? rawStage
    : null;
  const sessionIdentifier = stringValue(record.sessionIdentifier, 1_024);
  const requestName = stringValue(record.requestName, 128)
    || (exportScope === "single" ? "Session 导出" : "Bot Session 导出");
  const hasSessionIdentifier = Object.prototype.hasOwnProperty.call(record, "sessionIdentifier");
  if (!exportScope || !userId || !botId || engineType !== "openclaw" || !stage) return null;
  if (exportScope === "single" ? !sessionIdentifier : hasSessionIdentifier) return null;
  return {
    exportScope,
    target: { userId, botId, stage, engineType: "openclaw" },
    ...(exportScope === "single" ? { sessionIdentifier } : {}),
    requestName,
  };
}

function idempotencyKey(req: Request): string | null {
  const value = req.header("Idempotency-Key")?.trim() ?? "";
  return /^[\x21-\x7e]{16,128}$/.test(value) ? value : null;
}

function publicStatus(task: EvolveTaskRow, step?: EvolveStepRow): string {
  if (task.status === "completed") return "succeeded";
  if (task.status === "failed" || task.status === "canceled") return "failed";
  if (task.status === "pending") return "pending";
  return step?.status === "dispatched" ? "dispatched" : "running";
}

function publicPhase(task: EvolveTaskRow, step?: EvolveStepRow): string {
  if (task.status === "completed") return "completed";
  if (task.status === "pending" || step?.status === "created") return "queued";
  if (step?.status === "dispatched") return "locating";
  const output = parseOutput(step);
  const raw = typeof output?.phase === "string" ? output.phase : "";
  return ["locating", "packaging", "uploading"].includes(raw) ? raw : "packaging";
}

function createResponse(task: EvolveTaskRow, config: SessionExportConfig) {
  return {
    apiVersion: API_VERSION,
    exportId: task.task_id,
    status: publicStatus(task),
    exportScope: config.exportScope,
    createdAt: isoTime(task.gmt_create),
  };
}

function uploadedRaw(output: Record<string, unknown> | null): Record<string, unknown> | null {
  const artifacts = objectValue(output?.artifacts);
  return objectValue(artifacts?.raw);
}

function resolvedSessionIds(output: Record<string, unknown> | null): string[] {
  return Array.isArray(output?.sessionIds)
    ? output.sessionIds.filter((item): item is string => typeof item === "string" && Boolean(item.trim()))
    : [];
}

function buildTaskParams(config: SessionExportConfig): Record<string, string> {
  const envelope = {
    schemaVersion: "clawevolve-task/v1",
    taskType: "session_export",
    taskId: config.taskId,
    stepId: config.stepId,
    attempt: config.attempt,
    execution: { executor: "ais", action: "package" },
    input: {
      userId: config.userId,
      botId: config.botId,
      stage: config.stage,
      isServiceBot: config.stage === "service",
      engineType: config.engineType,
      env: "prod",
      entityType: "staff",
      ...(config.sessionIdentifier ? { sessionIdentifier: config.sessionIdentifier } : {}),
    },
    runtime: {
      outputDir: `/tmp/${config.taskId}`,
      clawwebUrl: config.clawwebUrl,
      artifactUploadMode: config.artifactUploadMode,
      artifacts: config.artifacts,
    },
  };
  return { "${clawevolve_params}": JSON.stringify(envelope) };
}

export function createSessionExportIntegrationRouter(
  deps: SessionExportIntegrationRouterDeps,
): Router {
  const router = Router();
  const now = deps.now ?? Date.now;

  router.post("/", asyncHandler(async (req, res) => {
    if (!deps.repo) return error(res, 503, "TASK_STORE_UNAVAILABLE", "任务数据库不可用");
    const key = idempotencyKey(req);
    if (!key) return error(res, 400, "INVALID_REQUEST", "Idempotency-Key 必填，长度为 16 到 128 个安全 ASCII 字符");
    const request = normalizeCreateRequest(req.body);
    if (!request) return error(res, 400, "INVALID_REQUEST", "Session 导出请求不合法");

    const requestHash = sha256(JSON.stringify(request));
    const taskId = `SE-${sha256(key).slice(0, 40)}`;
    const existing = await deps.repo.findTask(taskId);
    if (existing) {
      const existingConfig = parseConfig(existing);
      if (!existingConfig || existing.task_type !== "session_export"
        || existingConfig.integration?.requestHash !== requestHash) {
        return error(res, 409, "IDEMPOTENCY_CONFLICT", "同一 Idempotency-Key 已用于不同请求");
      }
      return res.status(202).json(createResponse(existing, existingConfig));
    }

    const accessible = await deps.repo.resolveAccessibleEvolveBotRuntime(
      request.target.userId,
      request.target.botId,
    );
    if (!accessible) {
      return error(res, 403, "TARGET_ACCESS_DENIED", "目标用户或 Bot 不存在或不可导出");
    }
    if (accessible.runtime.activeEngine?.toLowerCase() !== "openclaw") {
      return error(res, 422, "UNSUPPORTED_ENGINE", "一期只支持 OpenClaw Bot");
    }

    const stepId = `${taskId}-AIS`;
    const attempt = 1;
    const prefix = `evolution/${taskId}/session-export/attempt-${attempt}`;
    const archiveName = `${safeFilenamePart(accessible.ownerId)}-${safeFilenamePart(request.target.botId)}-${request.target.stage}-sessions.tar.gz`;
    const rawName = request.exportScope === "single" ? "session.jsonl" : archiveName;
    const artifacts = {
      raw: { objectKey: `${prefix}/${rawName}` },
      manifest: { objectKey: `${prefix}/manifest.json` },
      result: { objectKey: `${prefix}/result.json` },
    };
    const config: SessionExportConfig = {
      source: "integration_api",
      apiVersion: API_VERSION,
      mode: request.exportScope === "single" ? "EXPORT_SINGLE" : "EXPORT_ALL",
      exportScope: request.exportScope,
      stage: request.target.stage,
      engineType: "openclaw",
      userId: accessible.ownerId,
      requestedUserId: request.target.userId,
      botId: request.target.botId,
      taskId,
      stepId,
      attempt,
      ...(request.sessionIdentifier ? { sessionIdentifier: request.sessionIdentifier } : {}),
      clawwebUrl: getClawWebPublicBaseUrl(),
      artifactUploadMode: "broker",
      llmAnalysis: false,
      llmUseDefault: true,
      artifacts,
      integration: {
        requestHash,
        idempotencyKeyHash: sha256(key),
      },
    };

    try {
      await deps.repo.createTaskWithStep({
        task: {
          taskId,
          taskType: "session_export",
          userId: config.userId,
          botId: config.botId,
          taskName: request.requestName,
          configJson: JSON.stringify(config),
          createdBy: "integration:public",
        },
        step: { stepId, stepType: "session_ais", stepNo: 1, command: "package" },
      });
    } catch (creationError) {
      const raced = await deps.repo.findTask(taskId);
      const racedConfig = raced ? parseConfig(raced) : null;
      if (!raced || !racedConfig
        || racedConfig.integration?.requestHash !== requestHash) throw creationError;
      return res.status(202).json(createResponse(raced, racedConfig));
    }

    try {
      const jobId = await deps.ais.execute(
        request.target.userId,
        buildTaskParams(config),
        SESSION_ANALYSIS_SNAPSHOT_ID,
      );
      await deps.repo.markExternalDispatched(stepId, jobId, {
        jobId,
        jobUrl: `https://aistudio.alipay.com/project/job/detail/${jobId}`,
        snapshotId: SESSION_ANALYSIS_SNAPSHOT_ID,
        submittedBy: request.target.userId,
        taskId,
        stepId,
        taskType: "session_export",
        action: "package",
        exportScope: request.exportScope,
        target: {
          userId: config.userId,
          requestedUserId: config.requestedUserId,
          botId: config.botId,
          stage: config.stage,
          engineType: config.engineType,
        },
      });
      return res.status(202).json({
        apiVersion: API_VERSION,
        exportId: taskId,
        status: "dispatched",
        exportScope: request.exportScope,
        createdAt: isoTime((await deps.repo.findTask(taskId))?.gmt_create),
      });
    } catch (dispatchError) {
      const message = dispatchError instanceof Error ? dispatchError.message : String(dispatchError);
      await deps.repo.markDispatchFailed(stepId, message.slice(0, 4_000));
      console.error(`[session-export-api] dispatch failed task=${taskId}: ${message.slice(0, 1_000)}`);
      return res.status(202).json({
        apiVersion: API_VERSION,
        exportId: taskId,
        status: "failed",
        exportScope: request.exportScope,
        error: { code: "DISPATCH_FAILED", message: "AIStudio 任务投递失败", retryable: true },
      });
    }
  }));

  router.get("/:exportId", asyncHandler(async (req, res) => {
    res.set("Cache-Control", "no-store");
    if (!deps.repo) return error(res, 503, "TASK_STORE_UNAVAILABLE", "任务数据库不可用");
    const exportId = Array.isArray(req.params.exportId) ? req.params.exportId[0] : req.params.exportId;
    const task = await deps.repo.findTask(exportId ?? "");
    const config = task ? parseConfig(task) : null;
    if (!task || task.task_type !== "session_export" || !config
      || config.source !== "integration_api") {
      return error(res, 404, "EXPORT_NOT_FOUND", "导出任务不存在");
    }
    const steps = await deps.repo.listSteps(task.task_id);
    const step = steps.at(-1);
    const output = parseOutput(step);
    const status = publicStatus(task, step);
    const sessionIds = resolvedSessionIds(output);
    const raw = uploadedRaw(output);
    let artifact: Record<string, unknown> | null = null;
    if (status === "succeeded" && raw) {
      const objectKey = typeof raw.objectKey === "string" ? raw.objectKey : "";
      if (objectKey !== config.artifacts.raw.objectKey) {
        return error(res, 503, "ARTIFACT_METADATA_INVALID", "导出产物元数据无效");
      }
      const requestedNetwork = req.query.downloadNetwork === "production" ? "production" : "office";
      const store = requestedNetwork === "production"
        ? deps.productionDownloadStore
        : deps.officeDownloadStore;
      let downloadUrl: string;
      try {
        downloadUrl = await store.createSignedUrl(objectKey, "GET", DOWNLOAD_URL_TTL_SECONDS);
      } catch (signError) {
        console.error(`[session-export-api] download URL failed task=${task.task_id}: ${signError instanceof Error ? signError.message : String(signError)}`);
        return error(res, 503, "DOWNLOAD_URL_UNAVAILABLE", "暂时无法生成 OSS 下载地址");
      }
      const filename = config.exportScope === "single"
        ? `${safeFilenamePart(sessionIds[0] ?? config.sessionIdentifier ?? "session")}.jsonl`
        : `${safeFilenamePart(config.requestedUserId)}-${safeFilenamePart(config.botId)}-${config.stage}-sessions.tar.gz`;
      artifact = {
        filename,
        contentType: typeof raw.contentType === "string"
          ? raw.contentType
          : config.exportScope === "single" ? "application/x-ndjson" : "application/gzip",
        size: typeof raw.size === "number" ? raw.size : null,
        sha256: typeof raw.sha256 === "string" ? raw.sha256 : null,
        downloadUrl,
        downloadUrlExpiresAt: new Date(now() + DOWNLOAD_URL_TTL_SECONDS * 1_000).toISOString(),
      };
    }
    const taskError = status === "failed" ? {
      code: step?.error_code ?? "SESSION_EXPORT_FAILED",
      message: step?.error_code === "DISPATCH_FAILED"
        ? "AIStudio 任务投递失败"
        : publicErrorMessage(step?.error_message ?? task.error_message),
      retryable: step?.retryable == null ? true : Boolean(step.retryable),
    } : null;
    res.json({
      apiVersion: API_VERSION,
      exportId: task.task_id,
      status,
      phase: publicPhase(task, step),
      exportScope: config.exportScope,
      target: {
        userId: config.requestedUserId,
        botId: config.botId,
        stage: config.stage,
        engineType: config.engineType,
      },
      resolution: status === "succeeded" ? {
        inputType: config.exportScope === "single"
          ? (sessionIds[0] === config.sessionIdentifier ? "session_id" : "session_key")
          : "all",
        resolvedSessionIds: sessionIds,
        fileCount: sessionIds.length,
        totalBytes: typeof raw?.size === "number" ? raw.size : null,
        warnings: Array.isArray(output?.warnings) ? output.warnings : [],
      } : null,
      artifact,
      error: taskError,
      createdAt: isoTime(task.gmt_create),
      startedAt: isoTime(step?.started_at),
      completedAt: isoTime(step?.completed_at),
    });
  }));

  return router;
}
