import { afterEach, beforeEach, describe, expect, it } from "vitest";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "../../db.js";
import {
  InsightAutoRepairRepository,
  upsertRuleTrust,
} from "../insight-auto-repair-repository.js";
import type { AutoRepairRuleSnapshot } from "../../services/insight/auto-repair-policy.js";

let db: SqliteDatabase;
let repository: InsightAutoRepairRepository;

const rule: AutoRepairRuleSnapshot = {
  environment: "pre",
  sourceRuleId: "tool.utoo-proxy.unsupported",
  ruleVersion: 1,
  actionType: "DIRECT_EVOLUTION",
  allowedTargets: ["tools.md"],
  risk: "low",
  adminPolicyMode: "REVIEW",
};

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  repository = new InsightAutoRepairRepository(db);
});

afterEach(async () => {
  await db.close();
});

describe("InsightAutoRepairRepository", () => {
  it("requires both Admin trust and an exact Owner grant", async () => {
    expect(await repository.isRuleTrusted(rule)).toBe(false);
    await upsertRuleTrust(db, rule, "admin-1");
    expect(await repository.isRuleTrusted(rule)).toBe(true);

    const grant = await repository.grant({
      ownerUserId: "owner-1",
      botId: "bot-1",
      rule,
      sourceImprovementId: 41,
      grantedBy: "owner-1",
    });
    expect(grant).toEqual(expect.objectContaining({
      status: "ACTIVE",
      ownerUserId: "owner-1",
      botId: "bot-1",
      sourceRuleId: rule.sourceRuleId,
      ruleVersion: 1,
      allowedTargets: ["tools.md"],
    }));
    expect((await repository.findActiveGrant("owner-1", "bot-1", rule))?.grantId).toBe(grant.grantId);
    expect(await repository.findActiveGrant("owner-2", "bot-1", rule)).toBeNull();
    expect(await repository.findActiveGrant("owner-1", "bot-2", rule)).toBeNull();
  });

  it("invalidates authorization when the rule snapshot changes and supports revocation", async () => {
    await upsertRuleTrust(db, rule, "admin-1");
    const grant = await repository.grant({
      ownerUserId: "owner-1",
      botId: "bot-1",
      rule,
      sourceImprovementId: 42,
      grantedBy: "owner-1",
    });

    const upgradedRule = { ...rule, ruleVersion: 2 };
    const expandedTargets = { ...rule, allowedTargets: ["skill", "tools.md"] };
    const higherRisk = { ...rule, risk: "medium" as const };
    expect(await repository.findActiveGrant("owner-1", "bot-1", upgradedRule)).toBeNull();
    expect(await repository.findActiveGrant("owner-1", "bot-1", expandedTargets)).toBeNull();
    expect(await repository.findActiveGrant("owner-1", "bot-1", higherRisk)).toBeNull();

    const revoked = await repository.revoke({
      ownerUserId: "owner-1",
      grantId: grant.grantId,
      expectedVersion: grant.version,
      revokedBy: "owner-1",
    });
    expect(revoked).toEqual(expect.objectContaining({ status: "REVOKED", version: grant.version + 1 }));
    expect(await repository.findActiveGrant("owner-1", "bot-1", rule)).toBeNull();
  });
});
