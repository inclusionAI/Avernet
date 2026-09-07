import { describe, expect, it, vi } from "vitest";
import { GovernanceRuleProvider } from "../governance-rule-provider.js";

function productionRules(): Buffer {
  return Buffer.from(JSON.stringify({
    schemaVersion: "insight-governance-rules/v1",
    environment: "prod",
    version: 7,
    updatedAt: "2026-08-18T00:00:00+08:00",
    rules: [{
      ruleId: "tool.utoo-proxy.unsupported",
      version: 3,
      enabled: true,
      scope: { environment: "prod", botId: "*" },
      matcher: { failureClass: ["TOOL_FAILURE"] },
      actionType: "DIRECT_EVOLUTION",
      allowedTargets: ["tools.md"],
      risk: "low",
      adminPolicy: { mode: "REVIEW" },
    }],
  }));
}

describe("GovernanceRuleProvider", () => {
  it("publishes a new trusted rule version without changing its repair scope", async () => {
    let content = productionRules();
    const putObject = vi.fn(async (_key: string, next: Buffer | Uint8Array | string) => {
      content = Buffer.isBuffer(next) ? next : Buffer.from(next);
      return { etag: "trusted-etag" };
    });
    const provider = new GovernanceRuleProvider({
      environment: "prod",
      objectStore: {
        async getObject() {
          return { content, etag: "old-etag", contentType: "application/json" };
        },
        putObject,
      },
    });

    const promoted = await provider.promoteRuleToTrusted("tool.utoo-proxy.unsupported", 3);

    expect(promoted.document.version).toBe(8);
    expect(promoted.document.rules[0]).toEqual(expect.objectContaining({
      version: 4,
      actionType: "DIRECT_EVOLUTION",
      allowedTargets: ["tools.md"],
      adminPolicy: { mode: "TRUSTED" },
    }));
    expect(promoted.etag).toBe("trusted-etag");
    expect(putObject).toHaveBeenCalledWith(
      "governance/rules/prod/current.json",
      expect.any(Buffer),
      "application/json",
    );
  });

  it("lets PRE reuse production rules when the PRE object cannot be read", async () => {
    const requestedKeys: string[] = [];
    const warning = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    const sharedStore = {
      async getObject(objectKey: string) {
        requestedKeys.push(objectKey);
        if (objectKey === "governance/rules/pre/current.json") {
          throw Object.assign(
            new Error("You have no right to access this object because of bucket acl."),
            { status: 403 },
          );
        }
        return { content: productionRules(), etag: "prod-etag", contentType: "application/json" };
      },
    };
    const provider = new GovernanceRuleProvider({
      environment: "pre",
      objectStore: sharedStore,
      productionObjectStore: sharedStore,
      allowProductionFallback: true,
    });

    try {
      const snapshot = await provider.read();
      expect(snapshot).toEqual(expect.objectContaining({ source: "oss", etag: "prod-etag" }));
      expect(snapshot.document.environment).toBe("pre");
      expect(snapshot.document.rules[0]).toEqual(expect.objectContaining({
        ruleId: "tool.utoo-proxy.unsupported",
        scope: expect.objectContaining({ environment: "pre" }),
      }));
      expect(requestedKeys).toEqual([
        "governance/rules/pre/current.json",
        "governance/rules/prod/current.json",
      ]);
      expect(warning).toHaveBeenCalledWith(expect.stringContaining("trying PROD fallback"));
    } finally {
      warning.mockRestore();
    }
  });

  it("keeps production strict instead of falling back", async () => {
    const productionRead = vi.fn().mockRejectedValue(new Error("forbidden"));
    const provider = new GovernanceRuleProvider({
      environment: "prod",
      objectStore: { getObject: productionRead },
      productionObjectStore: { getObject: vi.fn() },
      allowProductionFallback: true,
    });

    await expect(provider.read()).rejects.toThrow("forbidden");
    expect(productionRead).toHaveBeenCalledWith("governance/rules/prod/current.json");
  });
});
