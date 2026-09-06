import { describe, expect, it } from "vitest";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { InsightRuleEvolutionRepository } from "../insight-rule-evolution-repository.js";

describe("Insight rule evolution repository", () => {
  it("creates, refreshes, and reviews one scoped proposal", async () => {
    const db = new SqliteDatabase(new Database(":memory:"));
    await runMigrations(db, "sqlite");
    const repository = new InsightRuleEvolutionRepository(db);
    try {
      const first = await repository.upsertCandidate({
        scopeFingerprint: "scope-1",
        environment: "pre",
        sourceRuleId: "tool.utoo-proxy.unsupported",
        fromRuleVersion: 1,
        proposedRuleVersion: 2,
        actionType: "DIRECT_EVOLUTION",
        allowedTargets: ["tools.md"],
        risk: "low",
        successCount: 2,
        ownerCount: 1,
        botCount: 1,
        lastVerifiedAt: 1_700_000_000,
        rationale: "达到可信规则门槛。",
      });
      expect(first).toEqual(expect.objectContaining({
        proposalId: expect.any(Number),
        status: "PENDING",
        successCount: 2,
      }));

      const refreshed = await repository.upsertCandidate({
        scopeFingerprint: "scope-1",
        environment: "pre",
        sourceRuleId: "tool.utoo-proxy.unsupported",
        fromRuleVersion: 1,
        proposedRuleVersion: 2,
        actionType: "DIRECT_EVOLUTION",
        allowedTargets: ["tools.md"],
        risk: "low",
        successCount: 3,
        ownerCount: 2,
        botCount: 2,
        lastVerifiedAt: 1_700_000_100,
        rationale: "更多成功验收。",
      });
      expect(refreshed).toEqual(expect.objectContaining({
        proposalId: first.proposalId,
        successCount: 3,
        ownerCount: 2,
        botCount: 2,
      }));

      const approved = await repository.review({
        proposalId: first.proposalId,
        expectedVersion: refreshed.version,
        decision: "APPROVE",
        reviewedBy: "admin-1",
        comment: "批准提升为可信规则。",
      });
      expect(approved).toEqual(expect.objectContaining({
        status: "APPROVED",
        reviewedBy: "admin-1",
        reviewComment: "批准提升为可信规则。",
      }));
    } finally {
      await db.close();
    }
  });
});
