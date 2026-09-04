import type { ObjectStore } from "../../object-storage/oss-object-store.js";
import type { SessionEvidence } from "../contracts.js";
import {
  DEFAULT_INSIGHT_OSS_BUCKET,
  parseInsightEvidenceRef,
  type InsightEvidenceEnvironment,
} from "../evidence-ref.js";
import { validateSessionEvidence } from "../evidence-validation.js";
import { InsightDataNotReadyError } from "./insight-read-provider.js";
import type { EvidenceProvider, EvidenceReadOptions } from "./evidence-provider.js";

export type OssEvidenceProviderOptions = {
  objectStore: ObjectStore;
  bucketName?: string;
  expectedEnvironment?: string;
  productionObjectStore?: ObjectStore;
  allowProductionFallback?: boolean;
};

function normalizeEtag(value: unknown): string | null {
  if (typeof value !== "string") return null;
  return value.trim().replace(/^W\//, "").replace(/^"|"$/g, "").toLowerCase() || null;
}

function isObjectNotFound(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const record = error as Record<string, unknown>;
  const code = String(record.code ?? record.name ?? "").toLowerCase();
  const response = record.response && typeof record.response === "object"
    ? record.response as Record<string, unknown>
    : undefined;
  const status = Number(record.status ?? record.statusCode ?? response?.status);
  return status === 404 || code === "nosuchkey" || code === "notfound" || code === "no_such_key";
}

function productionObjectKey(objectKey: string): string {
  return objectKey.replace(/^evolution\/pre\/evidence\//, "evolution/prod/evidence/");
}

export class OssEvidenceProvider implements EvidenceProvider {
  private readonly objectStore: ObjectStore;
  private readonly bucketName: string;
  private readonly expectedEnvironment?: string;
  private readonly productionObjectStore?: ObjectStore;
  private readonly allowProductionFallback: boolean;

  constructor(options: OssEvidenceProviderOptions) {
    this.objectStore = options.objectStore;
    this.bucketName = options.bucketName ?? DEFAULT_INSIGHT_OSS_BUCKET;
    this.expectedEnvironment = options.expectedEnvironment?.trim() || undefined;
    this.productionObjectStore = options.productionObjectStore;
    this.allowProductionFallback = options.allowProductionFallback === true;
  }

  async readEvidence(payloadRef: string, options: EvidenceReadOptions = {}): Promise<SessionEvidence> {
    let parsedRef: ReturnType<typeof parseInsightEvidenceRef>;
    try {
      const allowPreAndProd = this.allowProductionFallback && this.expectedEnvironment === "pre";
      parsedRef = parseInsightEvidenceRef(payloadRef, {
        expectedBucket: this.bucketName,
        ...(allowPreAndProd
          ? { allowedEnvironments: ["pre", "prod"] satisfies InsightEvidenceEnvironment[] }
          : { expectedEnvironment: this.expectedEnvironment }),
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      throw new InsightDataNotReadyError(`Evidence 路径不合法: ${message}`);
    }

    try {
      const getOptions = { versionId: options.versionId ?? undefined };
      const directStore = parsedRef.environment === "prod"
        ? this.productionObjectStore ?? this.objectStore
        : this.objectStore;
      let result;
      try {
        result = await directStore.getObject(parsedRef.objectKey, getOptions);
      } catch (error) {
        const canFallback = this.allowProductionFallback
          && parsedRef.environment === "pre"
          && this.productionObjectStore
          && isObjectNotFound(error);
        if (!canFallback) throw error;
        result = await this.productionObjectStore!.getObject(productionObjectKey(parsedRef.objectKey), getOptions);
      }

      const expectedEtag = normalizeEtag(options.expectedEtag);
      const actualEtag = normalizeEtag(result.etag);
      if (expectedEtag && actualEtag !== expectedEtag) {
        throw new Error(`etag mismatch: expected ${expectedEtag}, got ${actualEtag ?? "missing"}`);
      }

      const parsed = JSON.parse(result.content.toString("utf8")) as unknown;
      const evidence = parsed && typeof parsed === "object" && !Array.isArray(parsed) && "data" in parsed
        ? (parsed as { data: unknown }).data
        : parsed;
      return validateSessionEvidence(evidence);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      throw new InsightDataNotReadyError(`Evidence 不可用: ${payloadRef}: ${message}`);
    }
  }
}
