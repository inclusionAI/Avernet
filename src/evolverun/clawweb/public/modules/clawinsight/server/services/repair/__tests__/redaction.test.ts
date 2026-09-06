import { describe, expect, it } from "vitest";
import {
  containsRepairSecret,
  redactPersistableLines,
  redactPersistableText,
  redactText,
  redactValue,
} from "../redaction.js";

describe("Repair redaction", () => {
  it("removes common PII and inline business bodies before model or OSS output", () => {
    const output = redactText("user=13800138000 email=a@example.com --api-key secret-value\nprompt: 这是一段 完整业务正文\ninput: another private body\noutput: private model answer");
    expect(output).not.toContain("13800138000");
    expect(output).not.toContain("a@example.com");
    expect(output).not.toContain("完整业务正文");
    expect(output).not.toContain("another private body");
    expect(output).not.toContain("private model answer");
    expect(output).not.toContain("secret-value");
  });

  it("removes nested JSON business bodies while keeping diagnostic status", () => {
    expect(redactValue({ status: "failed", payload: { content: "private text" } })).toEqual({
      status: "failed",
      payload: { content: "[REDACTED_BUSINESS_BODY]" },
    });
  });

  it("removes bare execution tickets and API key literals from browser-safe text and values", () => {
    const ticket = "ce_repair_browser_projection_canary_1234567890";
    const apiKey = "sk-browser-projection-canary-1234567890";
    expect(redactText(`failed with ${ticket} and ${apiKey}`)).toBe(
      "failed with [REDACTED] and [REDACTED]",
    );
    expect(redactValue({ summary: `retry ${ticket}`, nested: [`provider ${apiKey}`] })).toEqual({
      summary: "retry [REDACTED]",
      nested: ["provider [REDACTED]"],
    });
  });

  it("removes canonical auth labels before a redacted message reaches the Repair ledger", () => {
    expect(redactPersistableText("upstream Cookie: SSO=terminal-secret"))
      .toBe("upstream [REDACTED_SECRET_TEXT]");
    expect(redactPersistableText("Authorization: Bearer opaque-secret"))
      .toBe("[REDACTED_SECRET_TEXT]");
    expect(redactPersistableText("ordinary text mentioning token rotation without a value"))
      .toBe("ordinary text mentioning token rotation without a value");
    expect(redactPersistableText("apiKey sk-secret-value-123456789"))
      .toBe("[REDACTED_SECRET_TEXT]");
  });

  it("keeps non-secret diagnostic context around a redacted assignment", () => {
    const output = redactPersistableText(
      "engine_config update failed authorization=opaque-secret operation=mcp.patch status=403",
    );

    expect(output).toContain("engine_config update failed");
    expect(output).toContain("operation=mcp.patch status=403");
    expect(output).toContain("[REDACTED_SECRET_TEXT]");
    expect(output).not.toContain("opaque-secret");
    expect(containsRepairSecret(output)).toBe(false);
  });

  it("redacts only credential-bearing lines before persistence", () => {
    const output = redactPersistableLines([
      "configuration section loaded",
      "Authorization: Bearer opaque-secret",
      "next section remains readable",
    ].join("\n"));

    expect(output).toBe([
      "configuration section loaded",
      "[REDACTED_SECRET_TEXT]",
      "next section remains readable",
    ].join("\n"));
  });

  it("detects and redacts signed-query credentials in historical browser and log text", () => {
    const secret = "signed-query-canary-123456";
    const urls = [
      `https://oss.example/object?X-Amz-Signature=${secret}&part=1`,
      `https://oss.example/object?x-oss-signature=${secret}`,
      `http://127.0.0.1:18789/ready?signature=${secret}`,
    ];
    for (const url of urls) {
      expect(containsRepairSecret(url)).toBe(true);
      expect(redactText(url)).toContain("[REDACTED]");
      expect(redactText(url)).not.toContain(secret);
      expect(redactPersistableText(url)).toContain("[REDACTED_SECRET_TEXT]");
      expect(redactPersistableText(url)).not.toContain(secret);
    }
  });

  it.each([
    "TOKEN=plain-token",
    "SECRET=plain-secret",
    "PASSWORD=plain-password",
    "access_key=plain-access-key",
    "access_token=plain-access-token",
    "ANTCHAT_API_KEY=plain-provider-key",
    "repair --token=plain-cli-token",
    '{"ANTCHAT_API_KEY":"plain-json-key"}',
  ])("detects executable credential syntax: %s", (value) => {
    expect(containsRepairSecret(value)).toBe(true);
    expect(redactText(value)).not.toContain("plain-");
  });
});
