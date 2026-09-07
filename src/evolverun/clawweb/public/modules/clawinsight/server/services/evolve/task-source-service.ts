import type { EvolveTaskSourceRow } from "../../repositories/evolve-task-source-repository.js";
import { EvolveTaskSourceRepository } from "../../repositories/evolve-task-source-repository.js";
import type {
  ImprovementDetail,
  ImprovementEvidenceSnapshot,
  SessionEvidence,
} from "../insight/contracts.js";
import {
  buildInsightPlanSource,
  INSIGHT_ADAPTER_VERSION,
  LEGACY_INSIGHT_ADAPTER_VERSION,
  InsightPlanSourceAdapterError,
  type InsightExecutionTarget,
  type InsightAdminOverride,
  type InsightSourceRef,
} from "./adapters/insight-plan-source-adapter.js";
import {
  digestJson,
  digestPlanSource,
  PLAN_SOURCE_DESCRIPTOR_VERSION,
  PLAN_SOURCE_SCHEMA_VERSION,
  type PlanSource,
} from "./plan-source-contract.js";

const MAX_PLAN_SOURCE_BYTES = 8 * 1024 * 1024;

export type FrozenEvidenceReader = (
  snapshot: ImprovementEvidenceSnapshot,
) => Promise<SessionEvidence>;

export class TaskSourceError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly stage: "freeze" | "evidence_read" | "adapter" | "interface",
    readonly retryable: boolean,
  ) {
    super(message);
  }
}

export type TaskSourceView = {
  sourceType: string;
  sourceId: string;
  schemaVersion: string;
  adapterVersion: string | null;
  status: string;
  digest: string | null;
  evidenceCount: number | null;
  error: { code: string | null; message: string | null; stage: string | null } | null;
  resolvedAt: number | string | null;
};

export type PlanSourceDescriptor = {
  descriptorVersion: typeof PLAN_SOURCE_DESCRIPTOR_VERSION;
  sourceType: "insight_improvement";
  schemaVersion: typeof PLAN_SOURCE_SCHEMA_VERSION;
  digest: string;
  delivery: { type: "inline"; content: PlanSource };
};

function timestamp(value: number | string): string {
  if (typeof value === "number") return new Date(value * 1000).toISOString();
  return String(value);
}

function evidenceReferenceBasis(evidence: ImprovementEvidenceSnapshot[]) {
  return evidence.map((item) => ({
    ordinal: item.ordinal,
    sessionId: item.sessionId,
    taskIndex: item.taskIndex,
    payloadRef: item.payloadRef,
    payloadEtag: item.payloadEtag,
    payloadVersionId: item.payloadVersionId,
  }));
}

function parseSourceRef(row: EvolveTaskSourceRow): InsightSourceRef {
  try {
    const value = JSON.parse(row.source_ref_json) as InsightSourceRef;
    if (
      !["evolve-source-ref/v1", "evolve-source-ref/v2"].includes(value.sourceRefVersion)
      || value.sourceType !== "insight_improvement"
    ) {
      throw new Error("SourceRef version/type 不支持");
    }
    if (!Array.isArray(value.evidence)) throw new Error("SourceRef evidence 必须是数组");
    const actualDigest = digestJson(evidenceReferenceBasis(value.evidence));
    if (value.evidenceRefsDigest !== actualDigest) {
      throw new Error(
        `SourceRef Evidence 引用 digest 不一致: expected=${value.evidenceRefsDigest}, actual=${actualDigest}`,
      );
    }
    if (value.sourceRefVersion === "evolve-source-ref/v2") {
      if (!value.target || !value.targetRefDigest) throw new Error("SourceRef v2 缺少冻结执行目标");
      const actualTargetDigest = digestJson(value.target);
      if (value.targetRefDigest !== actualTargetDigest) {
        throw new Error(
          `SourceRef 执行目标 digest 不一致: expected=${value.targetRefDigest}, actual=${actualTargetDigest}`,
        );
      }
      const expectedRelationship = value.target.ownerUserId === value.improvement.botOwnerUserId
        && value.target.botId === value.improvement.botId ? "same_bot" : "cross_bot";
      if (
        !value.target.ownerUserId || !value.target.botId || !value.target.selectedBy || !value.target.selectedAt
        || value.target.relationship !== expectedRelationship
        || value.target.crossBotConfirmed !== (expectedRelationship === "cross_bot")
      ) {
        throw new Error("SourceRef 执行目标审计信息不完整或不一致");
      }
    } else if (value.target || value.targetRefDigest) {
      throw new Error("SourceRef v1 不支持执行目标字段");
    }
    return value;
  } catch (error) {
    throw new TaskSourceError(
      "SOURCE_SNAPSHOT_INVALID",
      error instanceof Error ? error.message : String(error),
      "freeze",
      false,
    );
  }
}

function publicError(row: EvolveTaskSourceRow): TaskSourceView["error"] {
  if (!row.error_code && !row.error_message) return null;
  const stage = row.error_code === "PLAN_SOURCE_WRITE_FAILED" ? "resolver"
    : row.error_code === "PLAN_SOURCE_SCHEMA_INVALID" ? "schema_validation"
      : row.error_code === "PLAN_SOURCE_DIGEST_MISMATCH" ? "digest_validation"
        : row.error_code === "PLAN_SOURCE_INPUT_UNAVAILABLE" ? "interface"
          : row.error_code?.startsWith("EVIDENCE_") ? "evidence_read"
            : row.error_code?.startsWith("PLAN_SOURCE_") ? "adapter"
              : row.error_code?.startsWith("SOURCE_") ? "freeze" : "interface";
  return { code: row.error_code, message: row.error_message, stage };
}

export class TaskSourceService {
  constructor(
    private readonly repo: EvolveTaskSourceRepository,
    private readonly evidenceReader: FrozenEvidenceReader,
  ) {}

  async freezeInsight(
    taskId: string,
    detail: ImprovementDetail,
    target?: { ownerUserId: string; botId: string; selectedBy: string; crossBotConfirmed: boolean },
    adminOverride?: InsightAdminOverride,
    repairDirection?: string | null,
  ): Promise<TaskSourceView> {
    if (!detail.evidence.length || detail.evidence.length !== detail.evidenceCount) {
      throw new TaskSourceError(
        "SOURCE_SNAPSHOT_INVALID",
        `Improvement Evidence 计数不一致: expected=${detail.evidenceCount}, actual=${detail.evidence.length}`,
        "freeze",
        false,
      );
    }
    const identities = new Set<string>();
    for (const item of detail.evidence) {
      const identity = `${item.sessionId}:${item.taskIndex}`;
      if (identities.has(identity) || !item.payloadRef.trim() || !item.payloadEtag.trim()) {
        throw new TaskSourceError("SOURCE_SNAPSHOT_INVALID", `Evidence 冻结引用无效: ${identity}`, "freeze", false);
      }
      identities.add(identity);
    }
    const frozenAt = new Date().toISOString();
    const frozenTarget: InsightExecutionTarget | undefined = target ? {
      ownerUserId: target.ownerUserId,
      botId: target.botId,
      relationship: target.ownerUserId === detail.botOwnerUserId && target.botId === detail.botId
        ? "same_bot" : "cross_bot",
      selectedBy: target.selectedBy,
      selectedAt: frozenAt,
      crossBotConfirmed: target.crossBotConfirmed,
    } : undefined;
    const sourceRef: InsightSourceRef = {
      sourceRefVersion: frozenTarget ? "evolve-source-ref/v2" : "evolve-source-ref/v1",
      sourceType: "insight_improvement",
      improvement: {
        improvementId: detail.improvementId,
        version: detail.version,
        title: detail.title,
        userGuidance: detail.userGuidance,
        ownerUserId: detail.ownerUserId,
        botOwnerUserId: detail.botOwnerUserId,
        botId: detail.botId,
        sourceType: detail.sourceType,
        sourceRuleId: detail.sourceRuleId,
        dataStartTime: detail.dataStartTime,
        dataEndTime: detail.dataEndTime,
        dataAsOf: detail.dataAsOf,
        batchId: detail.batchId,
        createdBy: detail.createdBy,
        createdAt: timestamp(detail.gmtCreate),
        updatedAt: timestamp(detail.gmtModified),
      },
      evidence: detail.evidence,
      evidenceRefsDigest: digestJson(evidenceReferenceBasis(detail.evidence)),
      ...(repairDirection?.trim() ? { repairDirection: repairDirection.trim() } : {}),
      ...(frozenTarget ? {
        target: frozenTarget,
        targetRefDigest: digestJson(frozenTarget),
      } : {}),
      ...(adminOverride ? { adminOverride } : {}),
      frozenAt,
    };
    const row = await this.repo.createFrozen({
      taskId,
      sourceType: "insight_improvement",
      sourceId: `improvement:${detail.improvementId}`,
      sourceSchemaVersion: PLAN_SOURCE_SCHEMA_VERSION,
      adapterVersion: frozenTarget ? INSIGHT_ADAPTER_VERSION : LEGACY_INSIGHT_ADAPTER_VERSION,
      sourceRef: sourceRef as unknown as Record<string, unknown>,
    });
    return this.view(row);
  }

  async findView(taskId: string): Promise<TaskSourceView | null> {
    const row = await this.repo.findByTaskId(taskId);
    return row ? this.view(row) : null;
  }

  async markRuntimeFailure(taskId: string, code: string, message: string): Promise<void> {
    if (!code.startsWith("PLAN_SOURCE_") && !code.startsWith("EVIDENCE_") && !code.startsWith("SOURCE_")) {
      return;
    }
    const row = await this.repo.findByTaskId(taskId);
    if (!row) return;
    parseSourceRef(row);
    await this.repo.markFailed(taskId, code, message);
  }

  async resolvePlanSource(taskId: string): Promise<PlanSourceDescriptor> {
    const row = await this.repo.findByTaskId(taskId);
    if (!row) throw new TaskSourceError("PLAN_SOURCE_INPUT_UNAVAILABLE", "Task Source 不存在", "interface", true);
    if (row.source_type !== "insight_improvement") {
      throw new TaskSourceError("PLAN_SOURCE_SCHEMA_INVALID", `不支持的 Source 类型: ${row.source_type}`, "interface", false);
    }
    await this.repo.markResolving(taskId);
    try {
      const sourceRef = parseSourceRef(row);
      let evidence: SessionEvidence[];
      try {
        evidence = await Promise.all(sourceRef.evidence.map((item) => this.evidenceReader(item)));
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        const lowered = message.toLowerCase();
        if (lowered.includes("etag mismatch") || lowered.includes("version mismatch")) {
          throw new TaskSourceError("EVIDENCE_VERSION_MISMATCH", message, "evidence_read", false);
        }
        if (lowered.includes("schema_version") || lowered.includes("schema version")) {
          throw new TaskSourceError("EVIDENCE_SCHEMA_UNSUPPORTED", message, "evidence_read", false);
        }
        throw new TaskSourceError("EVIDENCE_UNAVAILABLE", message, "evidence_read", true);
      }
      let source: PlanSource;
      try {
        source = buildInsightPlanSource(sourceRef, evidence);
      } catch (error) {
        if (error instanceof InsightPlanSourceAdapterError) {
          throw new TaskSourceError(error.code, error.message, "adapter", false);
        }
        throw error;
      }
      const serializedBytes = Buffer.byteLength(JSON.stringify(source), "utf8");
      if (serializedBytes > MAX_PLAN_SOURCE_BYTES) {
        throw new TaskSourceError(
          "PLAN_SOURCE_TOO_LARGE",
          `Plan Source 超过 ${MAX_PLAN_SOURCE_BYTES} bytes: ${serializedBytes}`,
          "adapter",
          false,
        );
      }
      const digest = digestPlanSource(source);
      if (!["plan-source/v1", PLAN_SOURCE_SCHEMA_VERSION].includes(row.source_schema_version)) {
        throw new TaskSourceError(
          "PLAN_SOURCE_SCHEMA_INVALID",
          `冻结 Source schema 不支持迁移: ${row.source_schema_version}`,
          "adapter",
          false,
        );
      }
      const adapterVersion = source.source.adapter_version ?? null;
      const isCurrentDerivedContract = row.source_schema_version === PLAN_SOURCE_SCHEMA_VERSION
        && row.adapter_version === adapterVersion;
      if (isCurrentDerivedContract && row.source_digest && row.source_digest !== digest) {
        throw new TaskSourceError(
          "PLAN_SOURCE_DIGEST_MISMATCH",
          `重建 Source digest 与冻结值不一致: expected=${row.source_digest}, actual=${digest}`,
          "adapter",
          false,
        );
      }
      await this.repo.markReady(taskId, {
        digest,
        sourceSchemaVersion: PLAN_SOURCE_SCHEMA_VERSION,
        adapterVersion,
      });
      return {
        descriptorVersion: PLAN_SOURCE_DESCRIPTOR_VERSION,
        sourceType: "insight_improvement",
        schemaVersion: PLAN_SOURCE_SCHEMA_VERSION,
        digest,
        delivery: { type: "inline", content: source },
      };
    } catch (error) {
      const failure = error instanceof TaskSourceError
        ? error
        : new TaskSourceError("PLAN_SOURCE_SCHEMA_INVALID", error instanceof Error ? error.message : String(error), "adapter", false);
      await this.repo.markFailed(taskId, failure.code, failure.message);
      throw failure;
    }
  }

  private view(row: EvolveTaskSourceRow): TaskSourceView {
    const evidenceCount = parseSourceRef(row).evidence.length;
    return {
      sourceType: row.source_type,
      sourceId: row.source_id,
      schemaVersion: row.source_schema_version,
      adapterVersion: row.adapter_version,
      status: row.status,
      digest: row.source_digest,
      evidenceCount,
      error: publicError(row),
      resolvedAt: row.resolved_at,
    };
  }
}
