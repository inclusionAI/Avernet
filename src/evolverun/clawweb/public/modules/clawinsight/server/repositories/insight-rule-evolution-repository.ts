import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { nowForDb } from "@avernet/clawweb-shared/server/db";

export type RuleEvolutionProposalStatus = "PENDING" | "APPROVED" | "REJECTED";
export type RuleEvolutionProposalType = "PROMOTE_TRUSTED";

export type RuleEvolutionProposalView = {
  proposalId: number;
  scopeFingerprint: string;
  environment: string;
  sourceRuleId: string;
  fromRuleVersion: number;
  proposedRuleVersion: number;
  proposalType: RuleEvolutionProposalType;
  actionType: "DIRECT_EVOLUTION" | "ASSIGN_OWNER";
  allowedTargets: string[];
  risk: string;
  successCount: number;
  ownerCount: number;
  botCount: number;
  lastVerifiedAt: number | string | null;
  rationale: string;
  status: RuleEvolutionProposalStatus;
  reviewedBy: string | null;
  reviewedAt: number | string | null;
  reviewComment: string | null;
  learnedFix: string | null;
  version: number;
  gmtCreate: number | string;
  gmtModified: number | string;
};

type ProposalRow = {
  id: number;
  scope_fingerprint: string;
  environment: string;
  source_rule_id: string;
  from_rule_version: number;
  proposed_rule_version: number;
  proposal_type: string;
  action_type: string;
  allowed_targets_json: string;
  risk: string;
  success_count: number;
  owner_count: number;
  bot_count: number;
  last_verified_at: number | string | null;
  rationale: string;
  status: string;
  reviewed_by: string | null;
  reviewed_at: number | string | null;
  review_comment: string | null;
  learned_fix: string | null;
  version: number;
  gmt_create: number | string;
  gmt_modified: number | string;
};

function stableTargets(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))].sort();
}

function parseTargets(raw: string): string[] {
  try {
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? parsed.map(String).filter(Boolean) : [];
  } catch {
    return [];
  }
}

function proposalView(row: ProposalRow): RuleEvolutionProposalView {
  const status = row.status.toUpperCase();
  return {
    proposalId: Number(row.id),
    scopeFingerprint: row.scope_fingerprint,
    environment: row.environment,
    sourceRuleId: row.source_rule_id,
    fromRuleVersion: Number(row.from_rule_version),
    proposedRuleVersion: Number(row.proposed_rule_version),
    proposalType: "PROMOTE_TRUSTED",
    actionType: row.action_type.toUpperCase() === "ASSIGN_OWNER" ? "ASSIGN_OWNER" : "DIRECT_EVOLUTION",
    allowedTargets: parseTargets(row.allowed_targets_json),
    risk: row.risk,
    successCount: Number(row.success_count),
    ownerCount: Number(row.owner_count),
    botCount: Number(row.bot_count),
    lastVerifiedAt: row.last_verified_at,
    rationale: row.rationale,
    status: status === "APPROVED" || status === "REJECTED" ? status : "PENDING",
    reviewedBy: row.reviewed_by,
    reviewedAt: row.reviewed_at,
    reviewComment: row.review_comment,
    learnedFix: row.learned_fix ?? null,
    version: Number(row.version),
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
  };
}

export type UpsertRuleEvolutionProposalInput = {
  scopeFingerprint: string;
  environment: string;
  sourceRuleId: string;
  fromRuleVersion: number;
  proposedRuleVersion: number;
  proposalType?: RuleEvolutionProposalType;
  actionType: "DIRECT_EVOLUTION" | "ASSIGN_OWNER";
  allowedTargets: string[];
  risk: string;
  successCount: number;
  ownerCount: number;
  botCount: number;
  lastVerifiedAt: number | string | null;
  rationale: string;
  learnedFix?: string | null;
};

export class InsightRuleEvolutionRepository {
  constructor(private readonly db: IDatabase) {}

  private async findByUnique(input: Pick<UpsertRuleEvolutionProposalInput, "scopeFingerprint" | "fromRuleVersion">): Promise<RuleEvolutionProposalView | null> {
    const row = (await this.db.query<ProposalRow>(
      `SELECT * FROM insight_rule_evolution_proposal
       WHERE scope_fingerprint = ? AND proposal_type = 'PROMOTE_TRUSTED' AND from_rule_version = ?
       LIMIT 1`,
      [input.scopeFingerprint, input.fromRuleVersion],
    ))[0];
    return row ? proposalView(row) : null;
  }

  async upsertCandidate(input: UpsertRuleEvolutionProposalInput): Promise<RuleEvolutionProposalView> {
    const existing = await this.findByUnique(input);
    if (existing) {
      if (existing.status !== "PENDING") return existing;
      const now = nowForDb(this.db.dbType);
      await this.db.exec(
        `UPDATE insight_rule_evolution_proposal
            SET proposed_rule_version = ?, allowed_targets_json = ?, risk = ?, success_count = ?,
            owner_count = ?, bot_count = ?, last_verified_at = ?, rationale = ?, learned_fix = ?,
                version = version + 1, gmt_modified = ?
          WHERE id = ? AND status = 'PENDING'`,
        [
          input.proposedRuleVersion,
          JSON.stringify(stableTargets(input.allowedTargets)),
          input.risk,
          input.successCount,
          input.ownerCount,
          input.botCount,
          input.lastVerifiedAt,
          input.rationale,
          input.learnedFix ?? null,
          now,
          existing.proposalId,
        ],
      );
      return (await this.findById(existing.proposalId))!;
    }

    const now = nowForDb(this.db.dbType);
    try {
      const result = await this.db.exec(
        `INSERT INTO insight_rule_evolution_proposal
         (scope_fingerprint, environment, source_rule_id, from_rule_version, proposed_rule_version,
          proposal_type, action_type, allowed_targets_json, risk, success_count, owner_count,
          bot_count, last_verified_at, rationale, learned_fix, status, version, gmt_create, gmt_modified)
         VALUES (?, ?, ?, ?, ?, 'PROMOTE_TRUSTED', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', 1, ?, ?)`,
        [
          input.scopeFingerprint,
          input.environment,
          input.sourceRuleId,
          input.fromRuleVersion,
          input.proposedRuleVersion,
          input.actionType,
          JSON.stringify(stableTargets(input.allowedTargets)),
          input.risk,
          input.successCount,
          input.ownerCount,
          input.botCount,
          input.lastVerifiedAt,
          input.rationale,
          input.learnedFix ?? null,
          now,
          now,
        ],
      );
      const created = result.insertId ? await this.findById(result.insertId) : await this.findByUnique(input);
      if (created) return created;
    } catch (error) {
      const raced = await this.findByUnique(input);
      if (raced) return raced;
      throw error;
    }
    throw new Error("规则进化候选写入后不可见");
  }

  async findById(proposalId: number): Promise<RuleEvolutionProposalView | null> {
    const row = (await this.db.query<ProposalRow>(
      "SELECT * FROM insight_rule_evolution_proposal WHERE id = ? LIMIT 1",
      [proposalId],
    ))[0];
    return row ? proposalView(row) : null;
  }

  async list(status?: RuleEvolutionProposalStatus): Promise<RuleEvolutionProposalView[]> {
    const params: unknown[] = [];
    const condition = status ? "WHERE status = ?" : "";
    if (status) params.push(status);
    const rows = await this.db.query<ProposalRow>(
      `SELECT * FROM insight_rule_evolution_proposal
       ${condition}
       ORDER BY CASE status WHEN 'PENDING' THEN 0 ELSE 1 END, gmt_modified DESC, id DESC`,
      params,
    );
    return rows.map(proposalView);
  }

  async review(input: {
    proposalId: number;
    expectedVersion: number;
    decision: "APPROVE" | "REJECT";
    reviewedBy: string;
    comment?: string | null;
  }): Promise<RuleEvolutionProposalView | null | "VERSION_CONFLICT" | "STATE_CONFLICT"> {
    const existing = await this.findById(input.proposalId);
    if (!existing) return null;
    if (existing.version !== input.expectedVersion) return "VERSION_CONFLICT";
    if (existing.status !== "PENDING") return "STATE_CONFLICT";
    const now = nowForDb(this.db.dbType);
    const result = await this.db.exec(
      `UPDATE insight_rule_evolution_proposal
          SET status = ?, reviewed_by = ?, reviewed_at = ?, review_comment = ?,
              version = version + 1, gmt_modified = ?
        WHERE id = ? AND version = ? AND status = 'PENDING'`,
      [
        input.decision === "APPROVE" ? "APPROVED" : "REJECTED",
        input.reviewedBy,
        now,
        input.comment ?? null,
        now,
        input.proposalId,
        input.expectedVersion,
      ],
    );
    if (result.affectedRows !== 1) return "VERSION_CONFLICT";
    return this.findById(input.proposalId);
  }
}
