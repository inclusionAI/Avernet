import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { resolveDingTalkBotConfig, resolveInsightHandoffConfig } from "../../../db.js";
import { resolveInsightEvidenceProviderMode } from "../insight-runtime.js";

const tempDirs: string[] = [];

function writeDingTalkConfig(options: { appKey: string; appSecret: string; robotCode?: string }): string {
  const dir = mkdtempSync(join(tmpdir(), "clawweb-dingtalk-config-"));
  tempDirs.push(dir);
  const path = join(dir, "application.yaml");
  writeFileSync(path, [
    "dingtalk:",
    `  appKey: ${JSON.stringify(options.appKey)}`,
    `  appSecret: ${JSON.stringify(options.appSecret)}`,
    ...(options.robotCode ? [`  robotCode: ${JSON.stringify(options.robotCode)}`] : []),
    "",
  ].join("\n"));
  return path;
}

afterEach(() => {
  vi.unstubAllEnvs();
  for (const dir of tempDirs.splice(0)) rmSync(dir, { recursive: true, force: true });
});

describe("resolveInsightEvidenceProviderMode", () => {
  it("uses local files automatically for SQLite", () => {
    expect(resolveInsightEvidenceProviderMode("sqlite", {})).toBe("file");
  });

  it("uses OSS automatically for deployed MySQL runtimes", () => {
    expect(resolveInsightEvidenceProviderMode("mysql", {})).toBe("oss");
  });

  it("keeps an explicit override for tests and emergency fallback", () => {
    expect(resolveInsightEvidenceProviderMode("mysql", { INSIGHT_EVIDENCE_PROVIDER: "file" })).toBe("file");
    expect(resolveInsightEvidenceProviderMode("sqlite", { INSIGHT_EVIDENCE_PROVIDER: "oss" })).toBe("oss");
  });
});

describe("resolveInsightHandoffConfig", () => {
  it("uses the shared pre-production environment marker for the default host", () => {
    vi.stubEnv("RUNTIME_MODE", "");
    vi.stubEnv("SERVER_ENV", "prepub");
    vi.stubEnv("REAL_SERVER_ENV", "");
    vi.stubEnv("ALIPAY_APP_ENV", "");

    expect(resolveInsightHandoffConfig().publicBaseUrl).toBe("https://clawweb-pre.alipay.com");
  });
});


describe("resolveDingTalkBotConfig", () => {
  it("keeps the packaged robot enabled when ACI injects empty variables", () => {
    const configPath = writeDingTalkConfig({
      appKey: "packaged-app",
      appSecret: "packaged-secret",
    });
    vi.stubEnv("DINGTALK_BOT_APP_KEY", "");
    vi.stubEnv("DINGTALK_BOT_APP_SECRET", "");
    vi.stubEnv("DINGTALK_BOT_ROBOT_CODE", "");

    expect(resolveDingTalkBotConfig(configPath)).toEqual(expect.objectContaining({
      appKey: "packaged-app",
      appSecret: "packaged-secret",
      robotCode: "packaged-app",
    }));
  });

  it("ignores generic DingTalk login credentials and partial bot overrides", () => {
    const configPath = writeDingTalkConfig({
      appKey: "packaged-app",
      appSecret: "packaged-secret",
      robotCode: "packaged-robot",
    });
    vi.stubEnv("DINGTALK_APP_KEY", "login-app");
    vi.stubEnv("DINGTALK_APP_SECRET", "login-secret");
    vi.stubEnv("DINGTALK_ROBOT_CODE", "login-robot");
    vi.stubEnv("DINGTALK_BOT_APP_KEY", "partial-bot-app");
    vi.stubEnv("DINGTALK_BOT_APP_SECRET", "");
    vi.stubEnv("DINGTALK_BOT_ROBOT_CODE", "partial-bot-robot");

    expect(resolveDingTalkBotConfig(configPath)).toEqual(expect.objectContaining({
      appKey: "packaged-app",
      appSecret: "packaged-secret",
      robotCode: "packaged-robot",
    }));
  });

  it("uses a complete bot-specific override atomically", () => {
    const configPath = writeDingTalkConfig({
      appKey: "packaged-app",
      appSecret: "packaged-secret",
      robotCode: "packaged-robot",
    });
    vi.stubEnv("DINGTALK_BOT_APP_KEY", "override-app");
    vi.stubEnv("DINGTALK_BOT_APP_SECRET", "override-secret");
    vi.stubEnv("DINGTALK_BOT_ROBOT_CODE", "");

    expect(resolveDingTalkBotConfig(configPath)).toEqual(expect.objectContaining({
      appKey: "override-app",
      appSecret: "override-secret",
      robotCode: "override-app",
    }));
  });
});
