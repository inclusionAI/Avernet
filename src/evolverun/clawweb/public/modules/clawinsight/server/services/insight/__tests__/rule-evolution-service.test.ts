import { describe, expect, it, vi } from "vitest";
import { GovernanceRuleProvider } from "../governance-rule-provider.js";
import { RuleEvolutionService } from "../rule-evolution-service.js";
import { InsightRuleEvolutionRepository } from "../../../repositories/insight-rule-evolution-repository.js";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import Database from "better-sqlite3";
import type { InsightImprovementRepository } from "../../../repositories/insight-improvement-repository.js";
import type { ImprovementView } from "../contracts.js";

function ruleDocument(): Buffer {
  return Buffer.from(JSON.stringify({
    schemaVersion: "insight-governance-rules/v1",
    environment: "pre",
    version: 1,
    updatedAt: "2026-08-21T00:00:00+08:00",
    rules: [{
      ruleId: "tool.utoo-proxy.unsupported",
      version: 1,
      enabled: true,
      scope: { environment: "pre", botId: "*" },
      matcher: { failureClass: ["TOOL_FAILURE"] },
      actionType: "DIRECT_EVOLUTION",
      allowedTargets: ["tools.md"],
      risk: "low",
      adminPolicy: { mode: "REVIEW", trustedAfterApprovals: 2 },
    }],
  }));
}

const verifiedImprovement = {
  improvementId: 46,
  status: "RESOLVED",
  verificationStatus: "VERIFIED",
  actionType: "DIRECT_EVOLUTION",
  sourceRuleId: "tool.utoo-proxy.unsupported",
} as ImprovementView;

describe("RuleEvolutionService", () => {
  it("creates a trusted-rule proposal after enough successful verifications and publishes after Admin approval", async () => {
    const db = new SqliteDatabase(new Database(":memory:"));
    await runMigrations(db, "sqlite");
    let content = ruleDocument();
    const provider = new GovernanceRuleProvider({
      environment: "pre",
      objectStore: {
        async getObject() {
          return { content, etag: "rule-etag", contentType: "application/json" };
        },
        async putObject(_key, next) {
          content = Buffer.isBuffer(next) ? next : Buffer.from(next);
          return { etag: "rule-etag-v2" };
        },
      },
    });
    const proposalRepo = new InsightRuleEvolutionRepository(db);
    const improvementRepo = {
      getRuleEvolutionStats: vi.fn().mockResolvedValue({
        successCount: 2,
        ownerCount: 2,
        botCount: 2,
        lastVerifiedAt: 1_700_000_000,
      }),
    } as unknown as InsightImprovementRepository;
    const service = new RuleEvolutionService(improvementRepo, proposalRepo, provider);

    try {
      const proposal = await service.maybeCreateFromVerification(verifiedImprovement);
      expect(proposal).toEqual(expect.objectContaining({
        sourceRuleId: "tool.utoo-proxy.unsupported",
        fromRuleVersion: 1,
        proposedRuleVersion: 2,
        status: "PENDING",
        successCount: 2,
      }));

      const approved = await service.review("admin-1", proposal!.proposalId, {
        decision: "APPROVE",
        version: proposal!.version,
      });
      expect(approved.status).toBe("APPROVED");
      const published = JSON.parse(content.toString("utf8")) as { rules: Array<{ version: number; adminPolicy: { mode: string } }> };
      expect(published.rules[0]).toEqual(expect.objectContaining({
        version: 2,
        adminPolicy: expect.objectContaining({ mode: "TRUSTED" }),
      }));
    } finally {
      await db.close();
    }
  });
});
