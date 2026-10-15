import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { resolveBaasConfig, resolveInsightHandoffConfig } from "@avernet/clawweb-shared/server/db";
import {
  resolveRepairConfig as resolveConfiguredRepairConfig,
  resolveRepairTaskControlPlaneEnvironment,
} from "../config.js";

const temporaryDirectories: string[] = [];
const baseSnapshots = { pre: 62_800_354, prod: 62_900_272 };
const resolveRepairConfig = (configPath?: string) => resolveConfiguredRepairConfig(baseSnapshots, configPath);

function configFile(repairExecution: string): string {
  const directory = mkdtempSync(join(tmpdir(), "clawweb-repair-config-"));
  temporaryDirectories.push(directory);
  const path = join(directory, "application.yaml");
  writeFileSync(path, `
baas:
  evolveScriptPaths:
    dev: /runner/dev.sh
    pre: /runner/pre.sh
    prod: /runner/prod.sh
repair:
  execution:
${repairExecution}
`, "utf8");
  return path;
}

describe("resolveRepairConfig", () => {
  beforeEach(() => {
    vi.stubEnv("CLAWWEB_PUBLIC_BASE_URL", "http://localhost:5173");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    for (const directory of temporaryDirectories.splice(0)) {
      rmSync(directory, { recursive: true, force: true });
    }
  });

  it("uses the host-provided Base Snapshots without parallel AIStudio auth config", () => {
    const config = resolveRepairConfig();
    expect(config.aisSnapshotIds).toEqual(baseSnapshots);
    expect(config.decisionGraceSeconds).toBe(900);
    expect(config.contextWaitSeconds).toBe(1800);
    expect(config.executionLeaseSeconds).toBe(90);
    expect(config.heartbeatIntervalSeconds).toBe(20);
    expect(config.agentTimeoutSeconds).toBe(2100);
    expect(config.agentCloseoutTimeoutSeconds).toBe(180);
    expect(config.maxAgentAutoRecoveries).toBe(2);
    expect(config.agentCorrectionTimeoutSeconds).toBe(120);
    expect(config.maxAgentOutputCorrectionRetries).toBe(3);
    expect(config.maxAgentRateLimitRetries).toBe(3);
    expect(config.agentRateLimitRetryBaseSeconds).toBe(5);
    expect(config).not.toHaveProperty("enabled");
    expect(config).not.toHaveProperty("runTokenSecret");
    expect(config).not.toHaveProperty("runTokenTtlSeconds");
    expect(config).not.toHaveProperty("aisToken");
    expect(config).not.toHaveProperty("aisProjectName");
    expect(config).not.toHaveProperty("aisImage");
    expect(config).not.toHaveProperty("ocbBaseUrls");
    expect(config.publicBaseUrl).toBe(resolveInsightHandoffConfig().publicBaseUrl);
    expect(config.baas).toEqual(resolveBaasConfig());
  });
  it("selects the standard Repair package without changing business waiting deadlines", () => {
    expect(resolveRepairConfig()).toMatchObject({ packageId: "clawevolve-repair", decisionGraceSeconds: 900 });
  });

  it.each([
    ["pre", "pre"],
    ["prepub", "pre"],
    ["prod", "prod"],
    ["gray", "prod"],
    ["dev", "pre"],
  ] as const)("maps the %s deployment to the %s Repair control plane", (serverEnv, expected) => {
    vi.stubEnv("SERVER_ENV", serverEnv);
    expect(resolveRepairConfig().controlPlaneEnvironment).toBe(expected);
  });

  it.each([
    ["https://clawweb-pre.alipay.com", "pre"],
    ["https://clawweb-pre.antgroup-inc.cn", "pre"],
    ["https://clawweb.alipay.com", "prod"],
    ["https://clawweb.antgroup-inc.cn", "prod"],
  ] as const)("recovers a legacy task control plane from %s", (publicBaseUrl, expected) => {
    expect(resolveRepairTaskControlPlaneEnvironment({ publicBaseUrl }, "prod")).toBe(expected);
  });

  it("prefers the frozen task control plane over its historical URL and current deployment", () => {
    expect(resolveRepairTaskControlPlaneEnvironment({
      publicBaseUrl: "https://clawweb.alipay.com",
      controlPlaneEnvironment: "pre",
    }, "prod")).toBe("pre");
  });

  it("falls back to the current deployment only when a legacy task origin is unknown", () => {
    expect(resolveRepairTaskControlPlaneEnvironment({
      publicBaseUrl: "http://localhost:5173",
    }, "prod")).toBe("prod");
  });

  it("allows the Repair decision grace to be configured for Plan and Apply waits", () => {
    vi.stubEnv("REPAIR_DECISION_GRACE_SECONDS", "120");
    expect(resolveRepairConfig().decisionGraceSeconds).toBe(120);
  });

  it("reads Repair Agent recovery limits from YAML", () => {
    const config = resolveRepairConfig(configFile([
      "    contextWaitSeconds: 600",
      "    agentTimeoutSeconds: 1200",
      "    agentCloseoutTimeoutSeconds: 240",
      "    maxAgentAutoRecoveries: 2",
      "    agentCorrectionTimeoutSeconds: 180",
      "    maxAgentOutputCorrectionRetries: 2",
      "    maxAgentRateLimitRetries: 2",
      "    agentRateLimitRetryBaseSeconds: 10",
    ].join("\n")));

    expect(config).toMatchObject({
      agentTimeoutSeconds: 1200,
      agentCloseoutTimeoutSeconds: 240,
      maxAgentAutoRecoveries: 2,
      agentCorrectionTimeoutSeconds: 180,
      maxAgentOutputCorrectionRetries: 2,
      maxAgentRateLimitRetries: 2,
      agentRateLimitRetryBaseSeconds: 10,
    });
  });

  it("uses the 30-minute context wait and compatible Agent defaults for older YAML", () => {
    const config = resolveRepairConfig(configFile("    decisionGraceSeconds: 120"));

    expect(config).toMatchObject({
      decisionGraceSeconds: 120,
      contextWaitSeconds: 1800,
      agentTimeoutSeconds: 2100,
      agentCloseoutTimeoutSeconds: 180,
      maxAgentAutoRecoveries: 2,
      agentCorrectionTimeoutSeconds: 120,
      maxAgentOutputCorrectionRetries: 3,
      maxAgentRateLimitRetries: 3,
      agentRateLimitRetryBaseSeconds: 5,
    });
  });

  it("derives the legacy Agent timeout from an older custom context wait", () => {
    const config = resolveRepairConfig(configFile("    contextWaitSeconds: 1200"));

    expect(config.contextWaitSeconds).toBe(1200);
    expect(config.agentTimeoutSeconds).toBe(1500);
  });

  it("lets Repair-only environment values override YAML Agent recovery limits", () => {
    vi.stubEnv("REPAIR_AGENT_TIMEOUT_SECONDS", "1500");
    vi.stubEnv("REPAIR_AGENT_CLOSEOUT_TIMEOUT_SECONDS", "300");
    vi.stubEnv("REPAIR_MAX_AGENT_AUTO_RECOVERIES", "3");
    vi.stubEnv("REPAIR_AGENT_CORRECTION_TIMEOUT_SECONDS", "240");
    vi.stubEnv("REPAIR_MAX_AGENT_OUTPUT_CORRECTION_RETRIES", "3");
    vi.stubEnv("REPAIR_MAX_AGENT_RATE_LIMIT_RETRIES", "1");
    vi.stubEnv("REPAIR_AGENT_RATE_LIMIT_RETRY_BASE_SECONDS", "15");

    const config = resolveRepairConfig(configFile([
      "    contextWaitSeconds: 600",
      "    agentTimeoutSeconds: 1200",
      "    agentCloseoutTimeoutSeconds: 240",
      "    maxAgentAutoRecoveries: 2",
      "    agentCorrectionTimeoutSeconds: 180",
      "    maxAgentOutputCorrectionRetries: 2",
      "    maxAgentRateLimitRetries: 2",
      "    agentRateLimitRetryBaseSeconds: 10",
    ].join("\n")));

    expect(config).toMatchObject({
      agentTimeoutSeconds: 1500,
      agentCloseoutTimeoutSeconds: 300,
      maxAgentAutoRecoveries: 3,
      agentCorrectionTimeoutSeconds: 240,
      maxAgentOutputCorrectionRetries: 3,
      maxAgentRateLimitRetries: 1,
      agentRateLimitRetryBaseSeconds: 15,
    });
  });

  it.each([
    ["REPAIR_AGENT_TIMEOUT_SECONDS", "59", "agentTimeoutSeconds"],
    ["REPAIR_AGENT_TIMEOUT_SECONDS", "7201", "agentTimeoutSeconds"],
    ["REPAIR_AGENT_TIMEOUT_SECONDS", "not-a-number", "agentTimeoutSeconds"],
    ["REPAIR_AGENT_CLOSEOUT_TIMEOUT_SECONDS", "29", "agentCloseoutTimeoutSeconds"],
    ["REPAIR_AGENT_CLOSEOUT_TIMEOUT_SECONDS", "1801", "agentCloseoutTimeoutSeconds"],
    ["REPAIR_AGENT_CLOSEOUT_TIMEOUT_SECONDS", "12.5", "agentCloseoutTimeoutSeconds"],
    ["REPAIR_MAX_AGENT_AUTO_RECOVERIES", "0", "maxAgentAutoRecoveries"],
    ["REPAIR_MAX_AGENT_AUTO_RECOVERIES", "4", "maxAgentAutoRecoveries"],
    ["REPAIR_AGENT_CORRECTION_TIMEOUT_SECONDS", "59", "agentCorrectionTimeoutSeconds"],
    ["REPAIR_AGENT_CORRECTION_TIMEOUT_SECONDS", "301", "agentCorrectionTimeoutSeconds"],
    ["REPAIR_AGENT_CORRECTION_TIMEOUT_SECONDS", "12.5", "agentCorrectionTimeoutSeconds"],
    ["REPAIR_MAX_AGENT_OUTPUT_CORRECTION_RETRIES", "0", "maxAgentOutputCorrectionRetries"],
    ["REPAIR_MAX_AGENT_OUTPUT_CORRECTION_RETRIES", "4", "maxAgentOutputCorrectionRetries"],
    ["REPAIR_MAX_AGENT_OUTPUT_CORRECTION_RETRIES", "1.5", "maxAgentOutputCorrectionRetries"],
    ["REPAIR_MAX_AGENT_RATE_LIMIT_RETRIES", "-1", "maxAgentRateLimitRetries"],
    ["REPAIR_MAX_AGENT_RATE_LIMIT_RETRIES", "4", "maxAgentRateLimitRetries"],
    ["REPAIR_MAX_AGENT_RATE_LIMIT_RETRIES", "1.5", "maxAgentRateLimitRetries"],
    ["REPAIR_AGENT_RATE_LIMIT_RETRY_BASE_SECONDS", "0", "agentRateLimitRetryBaseSeconds"],
    ["REPAIR_AGENT_RATE_LIMIT_RETRY_BASE_SECONDS", "61", "agentRateLimitRetryBaseSeconds"],
    ["REPAIR_AGENT_RATE_LIMIT_RETRY_BASE_SECONDS", "1.5", "agentRateLimitRetryBaseSeconds"],
  ])("rejects an unsafe Repair Agent recovery value in %s", (envName, value, field) => {
    vi.stubEnv(envName, value);
    expect(() => resolveRepairConfig()).toThrow(`repair.${field}`);
  });

  it("does not let the Agent timeout cut off the OCB context wait", () => {
    vi.stubEnv("REPAIR_CONTEXT_WAIT_SECONDS", "1200");
    vi.stubEnv("REPAIR_AGENT_TIMEOUT_SECONDS", "900");

    expect(() => resolveRepairConfig()).toThrow(
      "repair.agentTimeoutSeconds 必须至少比 contextWaitSeconds 多 300 秒",
    );
  });
});
