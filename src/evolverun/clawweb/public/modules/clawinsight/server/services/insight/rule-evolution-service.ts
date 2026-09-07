import type { ImprovementView } from "./contracts.js";
import { readAutoRepairRule } from "./auto-repair-policy.js";
import type { GovernanceRuleProvider } from "./governance-rule-provider.js";
import {
  ruleScopeFingerprint,
} from "../../repositories/insight-auto-repair-repository.js";
import type { InsightImprovementRepository } from "../../repositories/insight-improvement-repository.js";
import {
  InsightRuleEvolutionRepository,
  type RuleEvolutionProposalStatus,
  type RuleEvolutionProposalView,
} from "../../repositories/insight-rule-evolution-repository.js";
import { InsightConflictError, InsightNotFoundError, InsightValidationError } from "./insight-service.js";

const DEFAULT_TRUSTED_AFTER_APPROVALS = 3;

export class RuleEvolutionService {
  constructor(
    private readonly improvementRepo: InsightImprovementRepository,
    private readonly proposalRepo: InsightRuleEvolutionRepository,
    private readonly ruleProvider: GovernanceRuleProvider,
  ) {}

  async maybeCreateFromVerification(
    improvement: ImprovementView,
  ): Promise<RuleEvolutionProposalView | null> {
    if (
      improvement.status.toUpperCase() !== "RESOLVED"
      || improvement.verificationStatus !== "VERIFIED"
      || improvement.actionType !== "DIRECT_EVOLUTION"
      || !improvement.sourceRuleId
    ) {
      return null;
    }

    const rule = await readAutoRepairRule(this.ruleProvider, improvement.sourceRuleId, "DIRECT_EVOLUTION");
    if (!rule || rule.adminPolicyMode === "TRUSTED") return null;
    const threshold = Number(rule.trustedAfterApprovals ?? DEFAULT_TRUSTED_AFTER_APPROVALS);
    const stats = await this.improvementRepo.getRuleEvolutionStats(rule.sourceRuleId);
    if (stats.successCount < threshold) return null;

    const learnedFix = (improvement.suggestedAction ?? improvement.userGuidance ?? "").trim() || null;
    return this.proposalRepo.upsertCandidate({
      scopeFingerprint: ruleScopeFingerprint(rule),
      environment: rule.environment,
      sourceRuleId: rule.sourceRuleId,
      fromRuleVersion: rule.ruleVersion,
      proposedRuleVersion: rule.ruleVersion + 1,
      proposalType: "PROMOTE_TRUSTED",
      actionType: rule.actionType,
      allowedTargets: rule.allowedTargets,
      risk: rule.risk,
      successCount: stats.successCount,
      ownerCount: stats.ownerCount,
      botCount: stats.botCount,
      lastVerifiedAt: stats.lastVerifiedAt,
      rationale: `规则 ${rule.sourceRuleId} 在 ${stats.ownerCount} 个 Owner、${stats.botCount} 个 Bot 上完成 ${stats.successCount} 次成功验收，达到可信规则门槛 ${threshold} 次；建议仅将 Admin Policy 提升为 TRUSTED，不扩大修改目标。`,
      learnedFix,
    });
  }

  async list(status?: RuleEvolutionProposalStatus): Promise<RuleEvolutionProposalView[]> {
    return this.proposalRepo.list(status);
  }

  async review(
    actorUserId: string,
    proposalId: number,
    body: Record<string, unknown>,
  ): Promise<RuleEvolutionProposalView> {
    const allowedFields = new Set(["decision", "comment", "version"]);
    const unknownFields = Object.keys(body).filter((field) => !allowedFields.has(field));
    if (unknownFields.length) {
      throw new InsightValidationError(`规则进化审核包含未知字段: ${unknownFields.join(", ")}`);
    }
    const decision = String(body.decision ?? "").trim().toUpperCase();
    const comment = body.comment == null ? null : String(body.comment).trim() || null;
    const version = Number(body.version);
    if (decision !== "APPROVE" && decision !== "REJECT") {
      throw new InsightValidationError("decision 只能是 APPROVE 或 REJECT");
    }
    if (!Number.isInteger(version) || version < 1) {
      throw new InsightValidationError("version 必须是正整数");
    }
    if (decision === "REJECT" && !comment) {
      throw new InsightValidationError("驳回规则进化建议时必须填写理由");
    }
    if ((comment?.length ?? 0) > 1000) {
      throw new InsightValidationError("comment 不能超过 1000 个字符");
    }

    const proposal = await this.proposalRepo.findById(proposalId);
    if (!proposal) throw new InsightNotFoundError("规则进化建议不存在");
    if (proposal.version !== version) throw new InsightConflictError("规则进化建议已被更新，请刷新后重试");
    if (proposal.status !== "PENDING") throw new InsightConflictError("规则进化建议已经处理");

    if (decision === "APPROVE") {
      const currentRule = await readAutoRepairRule(this.ruleProvider, proposal.sourceRuleId, "DIRECT_EVOLUTION");
      if (!currentRule || currentRule.ruleVersion !== proposal.fromRuleVersion) {
        throw new InsightConflictError("治理规则已变化，请重新生成规则进化建议");
      }
      if (currentRule.adminPolicyMode === "TRUSTED") {
        throw new InsightConflictError("治理规则已经是 TRUSTED");
      }
      try {
        await this.ruleProvider.promoteRuleToTrusted(
          proposal.sourceRuleId,
          proposal.fromRuleVersion,
          proposal.learnedFix ? {
            summary: proposal.learnedFix,
            verifiedAt: new Date().toISOString(),
          } : null,
        );
      } catch (error) {
        throw new InsightConflictError(
          `规则发布失败: ${error instanceof Error ? error.message : String(error)}`,
          "RULE_PUBLISH_FAILED",
        );
      }
    }

    const updated = await this.proposalRepo.review({
      proposalId,
      expectedVersion: version,
      decision: decision as "APPROVE" | "REJECT",
      reviewedBy: actorUserId,
      comment,
    });
    if (!updated) throw new InsightNotFoundError("规则进化建议不存在");
    if (updated === "VERSION_CONFLICT") throw new InsightConflictError("规则进化建议已被更新，请刷新后重试");
    if (updated === "STATE_CONFLICT") throw new InsightConflictError("规则进化建议已经处理");
    return updated;
  }
}
