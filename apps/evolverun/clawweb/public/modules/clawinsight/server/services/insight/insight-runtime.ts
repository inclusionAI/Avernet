import { join } from "node:path";
import { resolveDingTalkBotConfig, resolveInsightHandoffConfig, type IDatabase } from "@avernet/clawweb-shared/server/db";
import { InsightImprovementRepository } from "../../repositories/insight-improvement-repository.js";
import { InsightMetricDailyRepository } from "../../repositories/insight-metric-daily-repository.js";
import { InsightTaskIndexRepository } from "../../repositories/insight-task-index-repository.js";
import { InsightAutoRepairRepository } from "../../repositories/insight-auto-repair-repository.js";
import { InsightService } from "./insight-service.js";
import { DEFAULT_INSIGHT_OSS_BUCKET } from "./evidence-ref.js";
import { DbInsightReadProvider } from "./providers/db-insight-read-provider.js";
import type { EvidenceProvider } from "./providers/evidence-provider.js";
import { FileEvidenceProvider } from "./providers/file-evidence-provider.js";
import { FixtureInsightReadProvider } from "./providers/fixture-insight-read-provider.js";
import { createClawWebOssObjectStore, resolveClawWebOssEnvironment } from "../object-storage/clawweb-oss-runtime.js";
import { OssEvidenceProvider } from "./providers/oss-evidence-provider.js";
import { DingTalkSender } from "./dingtalk-sender.js";
import { GovernanceRuleProvider } from "./governance-rule-provider.js";

export type InsightRuntime = {
  improvementRepo: InsightImprovementRepository | null;
  metricRepo: InsightMetricDailyRepository | null;
  taskRepo: InsightTaskIndexRepository | null;
  autoRepairRepo: InsightAutoRepairRepository | null;
  service: InsightService | null;
  providerMode: string;
  providerDescription: string;
  dingTalkNotificationEnabled: boolean;
  evidenceAccessEnabled: boolean;
  ruleProvider: GovernanceRuleProvider | null;
};

function normalizeMode(value: string | undefined): string {
  return (value ?? "db").trim().toLowerCase();
}

export function resolveInsightEvidenceProviderMode(
  dbType: IDatabase["dbType"],
  env: Record<string, string | undefined> = process.env,
): string {
  const configured = env.INSIGHT_EVIDENCE_PROVIDER?.trim();
  if (configured) return normalizeMode(configured);
  return dbType === "sqlite" ? "file" : "oss";
}


export function createInsightRuntime(
  db: IDatabase,
  env: Record<string, string | undefined> = process.env,
): InsightRuntime {
  if (db.dbType === "noop") {
    return {
      improvementRepo: null,
      metricRepo: null,
      taskRepo: null,
      autoRepairRepo: null,
      service: null,
      providerMode: "disabled",
      providerDescription: "disabled: database unavailable",
      dingTalkNotificationEnabled: false,
      evidenceAccessEnabled: false,
      ruleProvider: null,
    };
  }

  const improvementRepo = new InsightImprovementRepository(db);
  const autoRepairRepo = new InsightAutoRepairRepository(db);
  const dingTalkEnabled = !["0", "false", "off", "disabled", "no"].includes(
    (env.INSIGHT_DINGTALK_ENABLED ?? "true").trim().toLowerCase(),
  );
  const dingTalkSender = new DingTalkSender(dingTalkEnabled
    ? resolveDingTalkBotConfig()
    : { appKey: "", appSecret: "", robotCode: "", apiBaseUrl: "", publicBaseUrl: "" });
  const handoffConfig = resolveInsightHandoffConfig();
  const metricRepo = new InsightMetricDailyRepository(db);
  const taskRepo = new InsightTaskIndexRepository(db);
  const providerMode = normalizeMode(env.INSIGHT_READ_PROVIDER ?? env.INSIGHT_PROVIDER);
  const evidenceRoot = env.INSIGHT_EVIDENCE_ROOT ?? env.INSIGHT_FIXTURE_ROOT ?? join(process.cwd(), "server/fixtures/insight/v1");
  const governanceEnvironment = resolveClawWebOssEnvironment(env);
  let ruleProvider: GovernanceRuleProvider;
  if (db.dbType === "sqlite" || env.INSIGHT_GOVERNANCE_RULES_FILE) {
    ruleProvider = new GovernanceRuleProvider({
      environment: governanceEnvironment,
      filePath: env.INSIGHT_GOVERNANCE_RULES_FILE ?? join(evidenceRoot, "governance-rules.json"),
    });
  } else {
    const governanceObjectStore = createClawWebOssObjectStore(governanceEnvironment, env);
    ruleProvider = new GovernanceRuleProvider({
      environment: governanceEnvironment,
      objectStore: governanceObjectStore,
      objectKey: env.INSIGHT_GOVERNANCE_RULES_OBJECT_KEY
        ?? `governance/rules/${governanceEnvironment}/current.json`,
      ...(governanceEnvironment === "pre" ? {
        // PRE reuses its own Mist credentials to read the shared production
        // rules object, matching the existing Evidence production fallback.
        productionObjectStore: governanceObjectStore,
        productionObjectKey: env.INSIGHT_GOVERNANCE_RULES_PRODUCTION_OBJECT_KEY
          ?? "governance/rules/prod/current.json",
        allowProductionFallback: true,
      } : {}),
    });
  }

  if (providerMode === "disabled" || providerMode === "off" || providerMode === "none") {
    return {
      improvementRepo,
      metricRepo,
      taskRepo,
      autoRepairRepo,
      service: null,
      providerMode: "disabled",
      providerDescription: "disabled by INSIGHT_READ_PROVIDER",
      dingTalkNotificationEnabled: dingTalkSender.enabled,
      evidenceAccessEnabled: false,
      ruleProvider,
    };
  }

  const evidenceBucket = env.CLAWWEB_OSS_BUCKET ?? env.INSIGHT_OSS_BUCKET ?? DEFAULT_INSIGHT_OSS_BUCKET;

  if (providerMode === "fixture") {
    return {
      improvementRepo,
      metricRepo,
      taskRepo,
      autoRepairRepo,
      service: new InsightService(
        new FixtureInsightReadProvider(evidenceRoot),
        new FileEvidenceProvider(evidenceRoot, evidenceBucket),
        improvementRepo,
        dingTalkSender.enabled ? dingTalkSender : null,
        handoffConfig.publicBaseUrl,
      ),
      providerMode,
      providerDescription: `fixture root=${evidenceRoot}`,
      dingTalkNotificationEnabled: dingTalkSender.enabled,
      evidenceAccessEnabled: true,
      ruleProvider,
    };
  }

  if (providerMode === "db") {
    const evidenceMode = resolveInsightEvidenceProviderMode(db.dbType, env);
    let evidenceProvider: EvidenceProvider;
    let evidenceDescription: string;
    if (evidenceMode === "file") {
      evidenceProvider = new FileEvidenceProvider(evidenceRoot, evidenceBucket);
      evidenceDescription = `file root=${evidenceRoot}`;
    } else if (evidenceMode === "oss") {
      const environment = resolveClawWebOssEnvironment(env);
      const allowProductionFallback = environment === "pre";
      const objectStore = createClawWebOssObjectStore(environment, env);
      evidenceProvider = new OssEvidenceProvider({
        objectStore,
        bucketName: evidenceBucket,
        expectedEnvironment: environment,
        ...(allowProductionFallback ? {
          // The pre runtime may read production Evidence object keys from the
          // shared OSS bucket, but it must keep using the pre runtime's Mist
          // credentials. A pre instance cannot query a prod Mist scene.
          productionObjectStore: objectStore,
          allowProductionFallback: true,
        } : {}),
      });
      evidenceDescription = `oss bucket=${evidenceBucket}, env=${environment}${allowProductionFallback ? ", prod-fallback=enabled" : ""}`;
    } else {
      return {
        improvementRepo,
        metricRepo,
        taskRepo,
        autoRepairRepo,
        service: null,
        providerMode,
        providerDescription: `db metrics/task index, unsupported evidence provider: ${evidenceMode}`,
        dingTalkNotificationEnabled: dingTalkSender.enabled,
        evidenceAccessEnabled: false,
        ruleProvider,
      };
    }
    return {
      improvementRepo,
      metricRepo,
      taskRepo,
      autoRepairRepo,
      service: new InsightService(
        new DbInsightReadProvider(taskRepo, metricRepo),
        evidenceProvider,
        improvementRepo,
        dingTalkSender.enabled ? dingTalkSender : null,
        handoffConfig.publicBaseUrl,
      ),
      providerMode,
      providerDescription: `db metric snapshot/task index, evidence=${evidenceDescription}`,
      dingTalkNotificationEnabled: dingTalkSender.enabled,
      evidenceAccessEnabled: true,
      ruleProvider,
    };
  }

  return {
    improvementRepo,
    metricRepo,
    taskRepo,
    autoRepairRepo,
    service: null,
    providerMode,
    providerDescription: `unsupported provider: ${providerMode}`,
    dingTalkNotificationEnabled: dingTalkSender.enabled,
    evidenceAccessEnabled: false,
    ruleProvider,
  };
}
