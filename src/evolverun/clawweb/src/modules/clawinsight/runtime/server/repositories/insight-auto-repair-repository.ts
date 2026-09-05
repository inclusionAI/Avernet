import { createHash } from "node:crypto";
import type { IDatabase } from "../db.js";
import type { AutoRepairRuleSnapshot } from "../services/insight/auto-repair-policy.js";

export type AutoRepairGrantStatus = "ACTIVE" | "REVOKED";

export type AutoRepairGrantView = {
  grantId: number;
  ownerUserId: string;
  botId: string;
  environment: string;
  sourceRuleId: string;
  ruleVersion: number;
  actionType: string;
  allowedTargets: string[];
  risk: string;
  status: AutoRepairGrantStatus;
  sourceImprovementId: number;
  grantedBy: string;
  grantedAt: number | string;
  revokedBy: string | null;
  revokedAt: number | string | null;
  version: number;
  gmtCreate: number | string;
  gmtModified: number | string;
  autoExecute: boolean;
};

type GrantRow = {
  id: number;
  scope_fingerprint: string;
  owner_user_id: string;
  bot_id: string;
  environment: string;
  source_rule_id: string;
  rule_version: number;
  action_type: string;
  allowed_targets_json: string;
  risk: string;
  status: string;
  source_improvement_id: number;
  granted_by: string;
  granted_at: number | string;
  revoked_by: string | null;
  revoked_at: number | string | null;
  version: number;
  gmt_create: number | string;
  gmt_modified: number | string;
  auto_execute: number | string | boolean;
};

type TrustRow = {
  id: number;
  scope_fingerprint: string;
  status: string;
};

function stableTargets(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))].sort();
}

function fingerprint(value: unknown): string {
  return createHash("sha256").update(JSON.stringify(value)).digest("hex");
}

export function ruleScopeFingerprint(rule: AutoRepairRuleSnapshot): string {
  return fingerprint({
    environment: rule.environment,
    sourceRuleId: rule.sourceRuleId,
    ruleVersion: rule.ruleVersion,
    actionType: rule.actionType,
    allowedTargets: stableTargets(rule.allowedTargets),
    risk: rule.risk,
  });
}

export function grantScopeFingerprint(
  ownerUserId: string,
  botId: string,
  rule: AutoRepairRuleSnapshot,
): string {
  return fingerprint({ ownerUserId, botId, rule: ruleScopeFingerprint(rule) });
}

function parseTargets(raw: string): string[] {
  try {
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? parsed.map(String).filter(Boolean) : [];
  } catch {
    return [];
  }
}

function grantView(row: GrantRow): AutoRepairGrantView {
  return {
    grantId: row.id,
    ownerUserId: row.owner_user_id,
    botId: row.bot_id,
    environment: row.environment,
    sourceRuleId: row.source_rule_id,
    ruleVersion: Number(row.rule_version),
    actionType: row.action_type,
    allowedTargets: parseTargets(row.allowed_targets_json),
    risk: row.risk,
    status: row.status.toUpperCase() === "REVOKED" ? "REVOKED" : "ACTIVE",
    sourceImprovementId: Number(row.source_improvement_id),
    grantedBy: row.granted_by,
    grantedAt: row.granted_at,
    revokedBy: row.revoked_by,
    revokedAt: row.revoked_at,
    version: Number(row.version),
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
    autoExecute: Number(row.auto_execute) === 1 || row.auto_execute === true,
  };
}

export async function upsertRuleTrust(
  db: IDatabase,
  rule: AutoRepairRuleSnapshot,
  trustedBy: string,
): Promise<void> {
  const now = db.dialect.now();
  const params = [
    ruleScopeFingerprint(rule),
    rule.environment,
    rule.sourceRuleId,
    rule.ruleVersion,
    rule.actionType,
    JSON.stringify(stableTargets(rule.allowedTargets)),
    rule.risk,
    trustedBy,
    now,
    now,
    now,
  ];
  if (db.dbType === "mysql" || db.dbType === "zdas") {
    await db.exec(
      `INSERT INTO insight_governance_rule_trust
       (scope_fingerprint, environment, source_rule_id, rule_version, action_type,
        allowed_targets_json, risk, status, trusted_by, trusted_at, version, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, 1, ?, ?)
       ON DUPLICATE KEY UPDATE status = 'ACTIVE', trusted_by = VALUES(trusted_by),
        trusted_at = VALUES(trusted_at), version = version + 1, gmt_modified = VALUES(gmt_modified)`,
      params,
    );
  } else {
    await db.exec(
      `INSERT INTO insight_governance_rule_trust
       (scope_fingerprint, environment, source_rule_id, rule_version, action_type,
        allowed_targets_json, risk, status, trusted_by, trusted_at, version, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, 1, ?, ?)
       ON CONFLICT(scope_fingerprint) DO UPDATE SET status = 'ACTIVE', trusted_by = excluded.trusted_by,
        trusted_at = excluded.trusted_at, version = insight_governance_rule_trust.version + 1,
        gmt_modified = excluded.gmt_modified`,
      params,
    );
  }
}

export class InsightAutoRepairRepository {
  constructor(private readonly db: IDatabase) {}

  async isRuleTrusted(rule: AutoRepairRuleSnapshot): Promise<boolean> {
    if (rule.adminPolicyMode === "TRUSTED") return true;
    const row = (await this.db.query<TrustRow>(
      `SELECT id, scope_fingerprint, status FROM insight_governance_rule_trust
       WHERE scope_fingerprint = ? AND status = 'ACTIVE' LIMIT 1`,
      [ruleScopeFingerprint(rule)],
    ))[0];
    return Boolean(row);
  }

  async grant(input: {
    ownerUserId: string;
    botId: string;
    rule: AutoRepairRuleSnapshot;
    sourceImprovementId: number;
    grantedBy: string;
    autoExecute?: boolean;
  }): Promise<AutoRepairGrantView> {
    const now = this.db.dialect.now();
    const scopeFingerprint = grantScopeFingerprint(input.ownerUserId, input.botId, input.rule);
    const params = [
      scopeFingerprint,
      input.ownerUserId,
      input.botId,
      input.rule.environment,
      input.rule.sourceRuleId,
      input.rule.ruleVersion,
      input.rule.actionType,
      JSON.stringify(stableTargets(input.rule.allowedTargets)),
      input.rule.risk,
      input.sourceImprovementId,
      input.grantedBy,
      now,
      now,
      now,
      input.autoExecute === true ? 1 : 0,
    ];
    if (this.db.dbType === "mysql" || this.db.dbType === "zdas") {
      await this.db.exec(
        `INSERT INTO insight_auto_repair_grant
         (scope_fingerprint, owner_user_id, bot_id, environment, source_rule_id, rule_version,
          action_type, allowed_targets_json, risk, status, source_improvement_id,
          granted_by, granted_at, revoked_by, revoked_at, version, gmt_create, gmt_modified, auto_execute)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, NULL, NULL, 1, ?, ?, ?)
         ON DUPLICATE KEY UPDATE status = 'ACTIVE', source_improvement_id = VALUES(source_improvement_id),
          granted_by = VALUES(granted_by), granted_at = VALUES(granted_at), revoked_by = NULL,
          revoked_at = NULL, auto_execute = VALUES(auto_execute), version = version + 1, gmt_modified = VALUES(gmt_modified)`,
        params,
      );
    } else {
      await this.db.exec(
        `INSERT INTO insight_auto_repair_grant
         (scope_fingerprint, owner_user_id, bot_id, environment, source_rule_id, rule_version,
          action_type, allowed_targets_json, risk, status, source_improvement_id,
          granted_by, granted_at, revoked_by, revoked_at, version, gmt_create, gmt_modified, auto_execute)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?, ?, NULL, NULL, 1, ?, ?, ?)
         ON CONFLICT(scope_fingerprint) DO UPDATE SET status = 'ACTIVE',
          source_improvement_id = excluded.source_improvement_id, granted_by = excluded.granted_by,
          granted_at = excluded.granted_at, revoked_by = NULL, revoked_at = NULL,
          auto_execute = excluded.auto_execute, version = insight_auto_repair_grant.version + 1, gmt_modified = excluded.gmt_modified`,
        params,
      );
    }
    const row = (await this.db.query<GrantRow>(
      "SELECT * FROM insight_auto_repair_grant WHERE scope_fingerprint = ? LIMIT 1",
      [scopeFingerprint],
    ))[0];
    if (!row) throw new Error("自动修复授权写入后不可见");
    return grantView(row);
  }

  async findActiveGrant(
    ownerUserId: string,
    botId: string,
    rule: AutoRepairRuleSnapshot,
  ): Promise<AutoRepairGrantView | null> {
    const row = (await this.db.query<GrantRow>(
      `SELECT * FROM insight_auto_repair_grant
       WHERE scope_fingerprint = ? AND status = 'ACTIVE' LIMIT 1`,
      [grantScopeFingerprint(ownerUserId, botId, rule)],
    ))[0];
    return row ? grantView(row) : null;
  }

  async findLatestActiveGrantForRule(
    ownerUserId: string,
    rule: AutoRepairRuleSnapshot,
  ): Promise<AutoRepairGrantView | null> {
    const row = (await this.db.query<GrantRow>(
      `SELECT * FROM insight_auto_repair_grant
       WHERE owner_user_id = ? AND environment = ? AND source_rule_id = ?
         AND rule_version = ? AND action_type = ? AND allowed_targets_json = ?
         AND risk = ? AND status = 'ACTIVE'
       ORDER BY gmt_modified DESC, id DESC
       LIMIT 1`,
      [
        ownerUserId,
        rule.environment,
        rule.sourceRuleId,
        rule.ruleVersion,
        rule.actionType,
        JSON.stringify(stableTargets(rule.allowedTargets)),
        rule.risk,
      ],
    ))[0];
    return row ? grantView(row) : null;
  }

  async findById(grantId: number): Promise<AutoRepairGrantView | null> {
    const row = (await this.db.query<GrantRow>(
      "SELECT * FROM insight_auto_repair_grant WHERE id = ? LIMIT 1",
      [grantId],
    ))[0];
    return row ? grantView(row) : null;
  }

  async list(ownerUserId: string): Promise<AutoRepairGrantView[]> {
    const rows = await this.db.query<GrantRow>(
      `SELECT * FROM insight_auto_repair_grant
       WHERE owner_user_id = ? ORDER BY status, gmt_modified DESC, id DESC`,
      [ownerUserId],
    );
    return rows.map(grantView);
  }

  async revoke(input: {
    ownerUserId: string;
    grantId: number;
    expectedVersion: number;
    revokedBy: string;
  }): Promise<AutoRepairGrantView | null | "VERSION_CONFLICT" | "STATE_CONFLICT"> {
    const existing = await this.findById(input.grantId);
    if (!existing || existing.ownerUserId !== input.ownerUserId) return null;
    if (existing.version !== input.expectedVersion) return "VERSION_CONFLICT";
    if (existing.status !== "ACTIVE") return "STATE_CONFLICT";
    const now = this.db.dialect.now();
    const result = await this.db.exec(
      `UPDATE insight_auto_repair_grant
       SET status = 'REVOKED', revoked_by = ?, revoked_at = ?, version = version + 1, gmt_modified = ?
       WHERE id = ? AND owner_user_id = ? AND version = ? AND status = 'ACTIVE'`,
      [input.revokedBy, now, now, input.grantId, input.ownerUserId, input.expectedVersion],
    );
    if (result.affectedRows !== 1) return "VERSION_CONFLICT";
    return this.findById(input.grantId);
  }
}
