export type GovernanceActionType = "DIRECT_EVOLUTION" | "ASSIGN_OWNER";

export type ImprovementSourceType =
  | "USER_SELECTED"
  | "ADMIN_SELECTED"
  | "ADMIN_RULE_DIRECT_EVOLUTION"
  | "ADMIN_RULE_ASSIGN_OWNER"
  | "TRUSTED_RULE_DIRECT_EVOLUTION"
  | "TRUSTED_RULE_ASSIGN_OWNER"
  | "REJECTED_RULE_DIRECT_EVOLUTION"
  | "REJECTED_RULE_ASSIGN_OWNER";

export const GOVERNANCE_SOURCE_TYPES = [
  "ADMIN_RULE_DIRECT_EVOLUTION",
  "ADMIN_RULE_ASSIGN_OWNER",
  "TRUSTED_RULE_DIRECT_EVOLUTION",
  "TRUSTED_RULE_ASSIGN_OWNER",
  "REJECTED_RULE_DIRECT_EVOLUTION",
  "REJECTED_RULE_ASSIGN_OWNER",
] as const;

export const OWNER_VISIBLE_SOURCE_TYPES = [
  "USER_SELECTED",
  "ADMIN_SELECTED",
  "ADMIN_RULE_DIRECT_EVOLUTION",
  "ADMIN_RULE_ASSIGN_OWNER",
  "TRUSTED_RULE_DIRECT_EVOLUTION",
  "TRUSTED_RULE_ASSIGN_OWNER",
] as const;

export function governanceSourceType(
  actionType: GovernanceActionType,
  trusted = false,
): ImprovementSourceType {
  if (actionType === "DIRECT_EVOLUTION") {
    return trusted ? "TRUSTED_RULE_DIRECT_EVOLUTION" : "ADMIN_RULE_DIRECT_EVOLUTION";
  }
  return trusted ? "TRUSTED_RULE_ASSIGN_OWNER" : "ADMIN_RULE_ASSIGN_OWNER";
}

export function actionTypeFromSourceType(sourceType: string): GovernanceActionType | null {
  if (sourceType.endsWith("DIRECT_EVOLUTION")) return "DIRECT_EVOLUTION";
  if (sourceType.endsWith("ASSIGN_OWNER")) return "ASSIGN_OWNER";
  return null;
}

export function isGovernanceSourceType(sourceType: string): boolean {
  return (GOVERNANCE_SOURCE_TYPES as readonly string[]).includes(sourceType);
}

export function isTrustedGovernanceSourceType(sourceType: string): boolean {
  return sourceType.startsWith("TRUSTED_RULE_");
}

export function isRejectedGovernanceSourceType(sourceType: string): boolean {
  return sourceType.startsWith("REJECTED_RULE_");
}

export function trustedGovernanceSourceType(sourceType: string): ImprovementSourceType | null {
  const actionType = actionTypeFromSourceType(sourceType);
  return actionType ? governanceSourceType(actionType, true) : null;
}

export function assignOwnerGovernanceSourceType(sourceType: string): ImprovementSourceType | null {
  if (sourceType === "ADMIN_RULE_DIRECT_EVOLUTION") return "ADMIN_RULE_ASSIGN_OWNER";
  if (sourceType === "TRUSTED_RULE_DIRECT_EVOLUTION") return "TRUSTED_RULE_ASSIGN_OWNER";
  return null;
}

export function rejectedGovernanceSourceType(sourceType: string): ImprovementSourceType | null {
  const actionType = actionTypeFromSourceType(sourceType);
  if (actionType === "DIRECT_EVOLUTION") return "REJECTED_RULE_DIRECT_EVOLUTION";
  if (actionType === "ASSIGN_OWNER") return "REJECTED_RULE_ASSIGN_OWNER";
  return null;
}

export function restoredGovernanceSourceType(sourceType: string): ImprovementSourceType | null {
  if (sourceType === "REJECTED_RULE_DIRECT_EVOLUTION") return "ADMIN_RULE_DIRECT_EVOLUTION";
  if (sourceType === "REJECTED_RULE_ASSIGN_OWNER") return "ADMIN_RULE_ASSIGN_OWNER";
  return null;
}

function singleLine(value: string | null | undefined): string | null {
  const normalized = value?.replace(/\s+/g, " ").trim();
  return normalized || null;
}

export function buildGovernanceGuidance(input: {
  userGuidance?: string | null;
  assignmentReason?: string | null;
  rootCauseSummary?: string | null;
  suggestedAction?: string | null;
}): string | null {
  const lines = [
    singleLine(input.rootCauseSummary) ? `根因：${singleLine(input.rootCauseSummary)}` : null,
    singleLine(input.assignmentReason) ? `指派原因：${singleLine(input.assignmentReason)}` : null,
    singleLine(input.suggestedAction) ? `建议动作：${singleLine(input.suggestedAction)}` : null,
    singleLine(input.userGuidance) ? `补充说明：${singleLine(input.userGuidance)}` : null,
  ].filter((line): line is string => Boolean(line));
  return lines.length ? lines.join("\n") : null;
}

export function appendGovernanceEvent(
  existing: string | null,
  event: {
    title: string;
    values?: Array<[label: string, value: string | number | null | undefined]>;
  },
): string {
  const lines = [`[${event.title}]`];
  for (const [label, value] of event.values ?? []) {
    if (value === null || value === undefined || String(value).trim() === "") continue;
    lines.push(`${label}：${singleLine(String(value))}`);
  }
  return [existing?.trim(), lines.join("\n")].filter(Boolean).join("\n\n");
}

function labeledValue(text: string | null, label: string): string | null {
  if (!text) return null;
  const match = text.match(new RegExp(`^${label}：(.+)$`, "m"));
  return match?.[1]?.trim() || null;
}

function lastEvent(text: string | null, title: string): string | null {
  if (!text) return null;
  const marker = `[${title}]`;
  const index = text.lastIndexOf(marker);
  if (index < 0) return null;
  const remainder = text.slice(index + marker.length).trim();
  const nextEvent = remainder.indexOf("\n\n[");
  return nextEvent >= 0 ? remainder.slice(0, nextEvent).trim() : remainder;
}

function eventValue(event: string | null, label: string): string | null {
  return labeledValue(event, label);
}

export function parseGovernanceGuidance(text: string | null) {
  const adminEvent = lastEvent(text, "Admin审核");
  const adminRejectEvent = lastEvent(text, "Admin驳回");
  const ownerRejectEvent = lastEvent(text, "用户驳回");
  const rejectionEvent = ownerRejectEvent ?? adminRejectEvent;
  const verificationEvents = ["自动验证", "强制验收"]
    .map((title) => ({ title, index: text?.lastIndexOf(`[${title}]`) ?? -1, event: lastEvent(text, title) }))
    .filter((item): item is { title: string; index: number; event: string } => item.index >= 0 && item.event !== null)
    .sort((left, right) => right.index - left.index);
  const verificationEvent = verificationEvents[0]?.event ?? null;
  const handledEvents = ["用户已处理", "管理员已处理"]
    .map((title) => ({ title, index: text?.lastIndexOf(`[${title}]`) ?? -1, event: lastEvent(text, title) }))
    .filter((item): item is { title: string; index: number; event: string } => item.index >= 0 && item.event !== null)
    .sort((left, right) => right.index - left.index);
  const handledEvent = handledEvents[0]?.event ?? null;
  return {
    assignmentReason: labeledValue(text, "指派原因"),
    rootCauseSummary: labeledValue(text, "根因"),
    suggestedAction: labeledValue(text, "建议动作"),
    adminDecision: eventValue(adminEvent, "决定"),
    adminReviewedBy: eventValue(adminEvent, "审核人"),
    adminReviewedAt: eventValue(adminEvent, "审核时间"),
    adminReviewComment: eventValue(adminEvent, "说明"),
    rejectedBy: ownerRejectEvent ? "OWNER" : adminRejectEvent ? "ADMIN" : null,
    rejectReasonCode: eventValue(rejectionEvent, "原因"),
    rejectComment: eventValue(rejectionEvent, "说明"),
    rejectedAt: eventValue(rejectionEvent, "时间"),
    handledAt: eventValue(handledEvent, "时间"),
    verificationStatus: eventValue(verificationEvent, "状态"),
    verificationLastCheckedAt: eventValue(verificationEvent, "检查时间"),
    verificationNewSessionCount: Number(eventValue(verificationEvent, "新Session数") ?? 0) || 0,
    verificationLastRecurrenceAt: eventValue(verificationEvent, "最后再现"),
    resolvedSource: eventValue(verificationEvent, "关闭来源"),
  };
}
