import { readFile, writeFile } from "node:fs/promises";
import type { GovernanceRule, GovernanceRuleDocument } from "./contracts.js";
import type { ObjectStore } from "../object-storage/oss-object-store.js";

export type GovernanceRuleSnapshot = {
  document: GovernanceRuleDocument;
  etag: string | null;
  source: "file" | "oss";
};

export type GovernanceRuleProviderOptions = {
  environment: string;
  filePath?: string;
  objectStore?: ObjectStore;
  objectKey?: string;
  productionObjectStore?: ObjectStore;
  productionObjectKey?: string;
  allowProductionFallback?: boolean;
  cacheTtlMs?: number;
};

function asRecord(value: unknown, name: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${name} 必须是对象`);
  }
  return value as Record<string, unknown>;
}

function normalizeRule(value: unknown, index: number): GovernanceRule {
  const raw = asRecord(value, `rules[${index}]`);
  const ruleId = String(raw.ruleId ?? "").trim();
  const version = Number(raw.version);
  const actionType = String(raw.actionType ?? "").trim().toUpperCase();
  const risk = String(raw.risk ?? "").trim().toLowerCase();
  const adminPolicy = asRecord(raw.adminPolicy ?? {}, `rules[${index}].adminPolicy`);
  const adminMode = String(adminPolicy.mode ?? "REVIEW").trim().toUpperCase();
  if (!ruleId || !Number.isInteger(version) || version < 1) {
    throw new Error(`rules[${index}] 缺少合法 ruleId/version`);
  }
  if (actionType !== "DIRECT_EVOLUTION" && actionType !== "ASSIGN_OWNER") {
    throw new Error(`rules[${index}].actionType 不合法`);
  }
  if (!(["low", "medium", "high"] as const).includes(risk as "low" | "medium" | "high")) {
    throw new Error(`rules[${index}].risk 不合法`);
  }
  if (adminMode !== "REVIEW" && adminMode !== "TRUSTED") {
    throw new Error(`rules[${index}].adminPolicy.mode 不合法`);
  }
  return {
    ruleId,
    version,
    enabled: raw.enabled !== false,
    scope: asRecord(raw.scope ?? {}, `rules[${index}].scope`),
    matcher: asRecord(raw.matcher ?? {}, `rules[${index}].matcher`),
    actionType,
    allowedTargets: Array.isArray(raw.allowedTargets)
      ? raw.allowedTargets.map((item) => String(item).trim()).filter(Boolean)
      : [],
    risk: risk as GovernanceRule["risk"],
    adminPolicy: {
      mode: adminMode as GovernanceRule["adminPolicy"]["mode"],
      ...(Number.isInteger(Number(adminPolicy.trustedAfterApprovals))
        ? { trustedAfterApprovals: Number(adminPolicy.trustedAfterApprovals) }
        : {}),
    },
    ...(raw.verification && typeof raw.verification === "object" && !Array.isArray(raw.verification)
      ? { verification: raw.verification as Record<string, unknown> }
      : {}),
    ...(Array.isArray(raw.learnedFixes)
      ? { learnedFixes: raw.learnedFixes.flatMap((item) => {
          if (!item || typeof item !== "object" || Array.isArray(item)) return [];
          const record = item as Record<string, unknown>;
          const summary = String(record.summary ?? "").trim();
          return summary ? [{
            summary,
            ...(Number.isInteger(Number(record.sourceImprovementId)) ? { sourceImprovementId: Number(record.sourceImprovementId) } : {}),
            ...(record.verifiedAt ? { verifiedAt: String(record.verifiedAt) } : {}),
          }] : [];
        }) }
      : {}),
  };
}

function describeError(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function productionObjectKey(objectKey: string): string {
  return objectKey.replace(/governance\/rules\/pre\//, "governance/rules/prod/");
}

function rebaseDocumentEnvironment(
  document: GovernanceRuleDocument,
  targetEnvironment: string,
): GovernanceRuleDocument {
  const sourceEnvironment = document.environment;
  return {
    ...document,
    environment: targetEnvironment,
    rules: document.rules.map((rule) => ({
      ...rule,
      scope: rule.scope.environment === sourceEnvironment
        ? { ...rule.scope, environment: targetEnvironment }
        : rule.scope,
    })),
  };
}

function parseDocument(content: Buffer, expectedEnvironment: string): GovernanceRuleDocument {
  const raw = asRecord(JSON.parse(content.toString("utf8")), "rule document");
  if (raw.schemaVersion !== "insight-governance-rules/v1") {
    throw new Error("治理规则 schemaVersion 不支持");
  }
  const version = Number(raw.version);
  const environment = String(raw.environment ?? "").trim();
  const updatedAt = String(raw.updatedAt ?? "").trim();
  if (!Number.isInteger(version) || version < 1 || !environment || !updatedAt) {
    throw new Error("治理规则 version/environment/updatedAt 不合法");
  }
  if (environment !== expectedEnvironment) {
    throw new Error(`治理规则环境不匹配: expected=${expectedEnvironment}, actual=${environment}`);
  }
  if (!Array.isArray(raw.rules)) throw new Error("治理规则 rules 必须是数组");
  return {
    schemaVersion: "insight-governance-rules/v1",
    environment,
    version,
    updatedAt,
    rules: raw.rules.map(normalizeRule),
  };
}

export class GovernanceRuleProvider {
  private cache: { value: GovernanceRuleSnapshot; expiresAt: number } | null = null;

  constructor(private readonly options: GovernanceRuleProviderOptions) {}

  async read(): Promise<GovernanceRuleSnapshot> {
    const now = Date.now();
    if (this.cache && this.cache.expiresAt > now) return this.cache.value;
    let snapshot: GovernanceRuleSnapshot;
    if (this.options.objectStore) {
      const objectKey = this.options.objectKey
        ?? `governance/rules/${this.options.environment}/current.json`;
      try {
        const object = await this.options.objectStore.getObject(objectKey);
        snapshot = {
          document: parseDocument(object.content, this.options.environment),
          etag: object.etag,
          source: "oss",
        };
      } catch (error) {
        const canFallback = this.options.allowProductionFallback === true
          && this.options.environment === "pre"
          && Boolean(this.options.productionObjectStore);
        if (!canFallback) throw error;
        const fallbackKey = this.options.productionObjectKey ?? productionObjectKey(objectKey);
        console.warn(
          `[clawweb] Governance rules PRE read failed key=${objectKey}; trying PROD fallback key=${fallbackKey}: ${describeError(error)}`,
        );
        const object = await this.options.productionObjectStore!.getObject(fallbackKey);
        const productionDocument = parseDocument(object.content, "prod");
        snapshot = {
          document: rebaseDocumentEnvironment(productionDocument, this.options.environment),
          etag: object.etag,
          source: "oss",
        };
      }
    } else if (this.options.filePath) {
      const content = await readFile(this.options.filePath);
      snapshot = {
        document: parseDocument(content, this.options.environment),
        etag: null,
        source: "file",
      };
    } else {
      throw new Error("治理规则读取器未配置");
    }
    this.cache = {
      value: snapshot,
      expiresAt: now + (this.options.cacheTtlMs ?? 60_000),
    };
    return snapshot;
  }

  async promoteRuleToTrusted(
    sourceRuleId: string,
    expectedRuleVersion: number,
    learnedFix?: { summary: string; sourceImprovementId?: number; verifiedAt?: string } | null,
  ): Promise<GovernanceRuleSnapshot> {
    const current = await this.read();
    const target = current.document.rules.find((rule) => rule.ruleId === sourceRuleId && rule.enabled);
    if (!target) throw new Error(`治理规则不存在或已停用: ${sourceRuleId}`);
    if (target.version !== expectedRuleVersion) {
      throw new Error(`治理规则版本已变化: expected=${expectedRuleVersion}, actual=${target.version}`);
    }
    if (target.adminPolicy.mode === "TRUSTED") return current;

    const document: GovernanceRuleDocument = {
      ...current.document,
      version: current.document.version + 1,
      updatedAt: new Date().toISOString(),
      rules: current.document.rules.map((rule) => rule.ruleId === sourceRuleId
        ? {
            ...rule,
            version: rule.version + 1,
            adminPolicy: { ...rule.adminPolicy, mode: "TRUSTED" },
            ...(learnedFix?.summary ? {
              learnedFixes: [...(rule.learnedFixes ?? []), learnedFix].slice(-20),
            } : {}),
          }
        : rule),
    };
    const content = Buffer.from(`${JSON.stringify(document, null, 2)}\n`, "utf8");
    const objectKey = this.options.objectKey
      ?? `governance/rules/${this.options.environment}/current.json`;
    let etag: string | null = null;
    if (this.options.objectStore) {
      if (!this.options.objectStore.putObject) {
        throw new Error("治理规则存储未开启写入能力");
      }
      ({ etag } = await this.options.objectStore.putObject(objectKey, content, "application/json"));
    } else if (this.options.filePath) {
      await writeFile(this.options.filePath, content);
    } else {
      throw new Error("治理规则发布目标未配置");
    }

    const snapshot: GovernanceRuleSnapshot = {
      document,
      etag,
      source: this.options.objectStore ? "oss" : "file",
    };
    this.cache = {
      value: snapshot,
      expiresAt: Date.now() + (this.options.cacheTtlMs ?? 60_000),
    };
    return snapshot;
  }
}
