import type { GovernanceRuleProvider, GovernanceRuleSnapshot } from "./governance-rule-provider.js";
import type { GovernanceActionType } from "./governance-item.js";

export type AutoRepairRuleSnapshot = {
  environment: string;
  sourceRuleId: string;
  ruleVersion: number;
  actionType: GovernanceActionType;
  allowedTargets: string[];
  risk: "low" | "medium" | "high";
  adminPolicyMode: "REVIEW" | "TRUSTED";
  trustedAfterApprovals?: number;
};

function stableTargets(values: string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))].sort();
}

export function findAutoRepairRule(
  snapshot: GovernanceRuleSnapshot,
  sourceRuleId: string,
  expectedActionType?: GovernanceActionType | null,
): AutoRepairRuleSnapshot | null {
  const rule = snapshot.document.rules.find((item) => item.ruleId === sourceRuleId && item.enabled);
  if (!rule) return null;
  if (expectedActionType && rule.actionType !== expectedActionType) return null;
  return {
    environment: snapshot.document.environment,
    sourceRuleId: rule.ruleId,
    ruleVersion: rule.version,
    actionType: rule.actionType,
    allowedTargets: stableTargets(rule.allowedTargets),
    risk: rule.risk,
    adminPolicyMode: rule.adminPolicy.mode,
    ...(rule.adminPolicy.trustedAfterApprovals !== undefined
      ? { trustedAfterApprovals: rule.adminPolicy.trustedAfterApprovals }
      : {}),
  };
}

export async function readAutoRepairRule(
  provider: GovernanceRuleProvider,
  sourceRuleId: string,
  expectedActionType?: GovernanceActionType | null,
): Promise<AutoRepairRuleSnapshot | null> {
  return findAutoRepairRule(await provider.read(), sourceRuleId, expectedActionType);
}
