import { describe, expect, it } from "vitest";
import { assertRepairAuditSecretFree } from "../../../repositories/repair-repository.js";
import { digestJson } from "../../evolve/plan-source-contract.js";
import {
  projectInsightPlanSourceForRepair,
} from "../insight-plan-source-projection.js";

function descriptor(content: unknown): Record<string, unknown> {
  return {
    descriptorVersion: "plan-source-descriptor/v2",
    sourceType: "insight_improvement",
    schemaVersion: "plan-source/v2",
    digest: digestJson(content),
    delivery: { type: "inline", content },
  };
}

describe("Repair Insight PlanSource projection", () => {
  it("redacts authentication material while retaining surrounding evidence", () => {
    const rawSecret = "signed-secret-value";
    const input = descriptor({
      schema_version: "plan-source/v2",
      cases: [{
        case_id: "case-1",
        evidence: {
          messages: [{
            message_index: 2,
            role: "tool",
            content: [
              "npm request failed",
              "Authorization: Bearer opaque-owner-credential",
              `artifact=https://oss.example/result?X-Oss-Signature=${rawSecret}&part=1`,
              "retry remained disabled",
            ].join("\n"),
            raw: {
              headers: { Cookie: "SSO=owner-secret", traceId: "trace-1" },
              apiKey: "sk-secret-value-1234567890",
            },
          }],
        },
      }],
    });

    const projected = projectInsightPlanSourceForRepair(input) as Record<string, unknown>;
    const serialized = JSON.stringify(projected);
    const projectedContent = ((projected.delivery as Record<string, unknown>).content) as Record<string, unknown>;

    expect(serialized).toContain("npm request failed");
    expect(serialized).toContain("retry remained disabled");
    expect(serialized).toContain("trace-1");
    expect(serialized).toContain("[REDACTED_SECRET_TEXT]");
    expect(serialized).not.toContain("opaque-owner-credential");
    expect(serialized).not.toContain("owner-secret");
    expect(serialized).not.toContain("sk-secret-value");
    expect(serialized).not.toContain(rawSecret);
    expect(projected.digest).toBe(digestJson(projectedContent));
    expect(() => assertRepairAuditSecretFree(projected, "insightPlanSource")).not.toThrow();
  });

  it("preserves a normal PlanSource without changing its evidence or digest", () => {
    const content = {
      schema_version: "plan-source/v2",
      cases: [{
        case_id: "case-normal",
        evidence: {
          messages: [{
            message_index: 1,
            role: "assistant",
            content: "读取配置完成，共发现 8 个 stdio MCP；token count 为 128。",
            raw: { traceId: "trace-normal", exitCode: 0 },
          }],
        },
      }],
    };
    const input = descriptor(content);

    const projected = projectInsightPlanSourceForRepair(input);

    expect(projected).toEqual(input);
    expect(() => assertRepairAuditSecretFree(projected, "insightPlanSource")).not.toThrow();
  });

  it("degrades only the optional Insight source when a safe projection cannot be produced", () => {
    const circular: Record<string, unknown> = { status: "ready" };
    circular.delivery = circular;

    const projected = projectInsightPlanSourceForRepair(circular);

    expect(projected).toEqual({
      status: "unavailable",
      reason: "unsafe_projection",
      error: "Insight 可选证据无法生成安全投影，本次 Repair 将继续但不使用该数据源。",
    });
    expect(() => assertRepairAuditSecretFree(projected, "insightPlanSource")).not.toThrow();
  });
});
