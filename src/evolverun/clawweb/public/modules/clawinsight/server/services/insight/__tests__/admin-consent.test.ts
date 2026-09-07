import { describe, expect, it, vi } from "vitest";
import { createAdminConsentToken, verifyAdminConsentToken } from "../admin-consent.js";

describe("admin consent token", () => {
  it("binds owner, bot, rule and expiry", () => {
    vi.stubEnv("INSIGHT_ADMIN_CONSENT_SECRET", "test-secret");
    const token = createAdminConsentToken({
      improvementId: 42,
      ownerUserId: "205357",
      botId: "test-bot",
      sourceRuleId: "tool.example",
      ruleVersion: 3,
      exp: 2_000_000_000,
    });
    expect(verifyAdminConsentToken(token, {
      improvementId: 42,
      ownerUserId: "205357",
      botId: "test-bot",
      sourceRuleId: "tool.example",
      ruleVersion: 3,
    }, 1).ownerUserId).toBe("205357");
    expect(() => verifyAdminConsentToken(token, {
      improvementId: 42,
      ownerUserId: "other",
      botId: "test-bot",
      sourceRuleId: "tool.example",
      ruleVersion: 3,
    }, 1)).toThrow(/作用域/);
    vi.unstubAllEnvs();
  });
});
