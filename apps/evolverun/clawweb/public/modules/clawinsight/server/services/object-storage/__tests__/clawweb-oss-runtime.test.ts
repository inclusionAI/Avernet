import { describe, expect, it } from "vitest";
import {
  DEFAULT_CLAWWEB_SESSION_ANALYSIS_AIS_OSS_ENDPOINT,
  DEFAULT_CLAWWEB_EVOLVE_ARCA_OSS_ENDPOINT,
  DEFAULT_CLAWWEB_OSS_SECRET_NAMES,
  normalizeClawWebOssEnvironment,
  resolveClawWebMistRuntimeScope,
  resolveClawWebOssEnvironment,
  resolveClawWebOssMistConfig,
} from "../clawweb-oss-runtime.js";

it("uses the bucket-authoritative OSS endpoint for AIStudio session analysis", () => {
  expect(DEFAULT_CLAWWEB_SESSION_ANALYSIS_AIS_OSS_ENDPOINT).toBe(
    "cn-shanghai-ant-internal.oss-alipay.aliyuncs.com",
  );
});

describe("ClawWeb OSS environment mapping", () => {
  it("uses the OSS endpoint reachable from Arca for Evolve artifact URLs", () => {
    expect(DEFAULT_CLAWWEB_EVOLVE_ARCA_OSS_ENDPOINT).toBe(
      "https://cn-shanghai-ant-office.oss-alipay.aliyuncs.com",
    );
  });

  it("uses the verified pre and planned prod Mist resource names", () => {
    expect(DEFAULT_CLAWWEB_OSS_SECRET_NAMES).toEqual({
      pre: "other_manual_clawweb_agentclaw_oss_pre",
      prod: "other_manual_clawweb_agentclaw_oss",
    });
  });

  it("maps only prod to prod and keeps non-prod traffic on pre", () => {
    expect(normalizeClawWebOssEnvironment("prod")).toBe("prod");
    expect(normalizeClawWebOssEnvironment("pre")).toBe("pre");
    expect(normalizeClawWebOssEnvironment("gray")).toBe("prod");
    expect(normalizeClawWebOssEnvironment(undefined)).toBe("pre");
  });

  it("pins production fallback to the prod Mist scene and secret", () => {
    const preRuntimeEnv = {
      CLAWWEB_MIST_MODE: "pre",
      INSIGHT_MIST_MODE: "pre",
      CLAWWEB_OSS_MIST_SECRET_NAME: "pre-secret-override",
      INSIGHT_OSS_MIST_SECRET_NAME: "another-pre-secret",
    };

    expect(resolveClawWebOssMistConfig("prod", preRuntimeEnv, { strictEnvironment: true })).toEqual({
      mode: "prod",
      secretName: DEFAULT_CLAWWEB_OSS_SECRET_NAMES.prod,
    });
    expect(resolveClawWebOssMistConfig("pre", preRuntimeEnv)).toEqual({
      mode: "pre",
      secretName: "pre-secret-override",
    });
  });

  it("resolves the standard ClawWeb deployment environment variables", () => {
    expect(resolveClawWebOssEnvironment({ SERVER_ENV: "pre" })).toBe("pre");
    expect(resolveClawWebOssEnvironment({ ALIPAY_APP_ENV: "prod" })).toBe("prod");
    expect(resolveClawWebOssEnvironment({ CLAWWEB_OSS_ENV: "pre", SERVER_ENV: "prod" })).toBe("pre");
  });

  it("keeps Mist in the current ClawWeb control-plane scope", () => {
    expect(resolveClawWebMistRuntimeScope("pre", {})).toEqual({
      appName: "clawweb",
      mode: "pre",
    });
    expect(resolveClawWebMistRuntimeScope("prod", {})).toEqual({
      appName: "clawweb",
      mode: "prod",
    });
    expect(resolveClawWebMistRuntimeScope("pre", {
      CLAWWEB_MIST_APP_NAME: "clawweb-canary",
      CLAWWEB_MIST_MODE: "pre-canary",
    })).toEqual({
      appName: "clawweb-canary",
      mode: "pre-canary",
    });
  });
});
