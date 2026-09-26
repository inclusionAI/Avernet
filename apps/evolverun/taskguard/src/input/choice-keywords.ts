import type { HumanInputFieldSpec } from "../types.js";

/** Common reject intent keywords (lowercased for matching). */
export const REJECT_KEYWORDS: string[] = [
  "拒绝", "不行", "算了", "取消", "不要", "否", "驳回", "退回",
];

/** Common confirm intent keywords (lowercased for matching). */
export const CONFIRM_KEYWORDS: string[] = [
  "同意", "确认", "批准", "通过", "好", "好的", "是", "ok", "可以",
];

export type ChoiceKeywordMapping = {
  choice: string;
  /** Lowercased keywords that map to this choice value. */
  keywords: string[];
};

/**
 * Built-in Chinese synonym mappings for common enum values.
 * When a field has no explicit `keywordAliases`, these auto-mappings
 * allow L1 to match common Chinese expressions to enum values.
 *
 * Used by both `buildKeywordMappings` (when no keywordAliases provided)
 * and the API-fallback L1 guard in index.ts (direct keyword matching
 * when full FlowState is unavailable).
 */
export const ENUM_SYNONYM_MAP: Record<string, string[]> = {
  // approve / 批准
  approve: ["批准", "同意", "通过", "确认", "允许", "认可", "approve", "通过审批"],
  accepted: ["接受", "同意", "确认", "accepted"],
  yes: ["是", "好", "好的", "可以", "yes", "同意"],
  pass: ["通过", "合格", "及格", "pass"],

  // reject / 驳回
  reject: ["驳回", "拒绝", "否决", "不通过", "退回", "打回", "reject"],
  rejected: ["已驳回", "已拒绝", "rejected"],
  denied: ["拒绝", "否定", "denied"],
  no: ["否", "不是", "不行", "不要", "no"],
  fail: ["不通过", "失败", "不合格", "fail"],
  failed: ["失败", "不通过", "failed"],

  // revise / 修改
  revise: ["修改", "调整", "返工", "修订", "更改", "revise", "修改后重新提交"],
  revision: ["修改", "调整", "返工", "revision"],
  pending: ["待定", "挂起", "暂缓", "pending"],
  defer: ["延期", "推迟", "暂缓", "defer"],

  // fast / quick
  fast: ["快速", "快速处理", "迅速", "加急", "fast", "快"],
  quick: ["快速", "迅速", "加急", "quick"],
  express: ["加急", "特快", "express"],

  // thorough / deep
  thorough: ["深入", "详细", "彻底", "全面", "仔细", "thorough", "深入分析"],
  deep: ["深入", "深度", "详细", "deep"],
  comprehensive: ["全面", "综合", "完整", "comprehensive"],
  detailed: ["详细", "细致", "detailed"],

  // Other common values
  skip: ["跳过", "略过", "跳过此步", "skip"],
  cancel: ["取消", "作废", "cancel"],
  retry: ["重试", "重新执行", "retry"],
  pause: ["暂停", "挂起", "pause"],
  resume: ["继续", "恢复", "resume"],
};

/**
 * Build keyword→choice mappings from a HumanInputFieldSpec.
 *
 * 1. If `keywordAliases` is provided, use it directly (values lowercased).
 * 2. Otherwise, use built-in synonym map (ENUM_SYNONYM_MAP) for known values,
 *    plus the enum value itself as fallback.
 * 3. If no enum is defined, return empty.
 */
export function buildKeywordMappings(
  spec: HumanInputFieldSpec | undefined,
): ChoiceKeywordMapping[] {
  if (!spec) return [];

  // Only handle string enums
  if (spec.type && spec.type !== "string") return [];
  if (!spec.enum || spec.enum.length === 0) return [];

  const enumValues = spec.enum.filter((v): v is string => typeof v === "string");

  if (spec.keywordAliases && Object.keys(spec.keywordAliases).length > 0) {
    // Use explicit aliases
    return enumValues.map((value) => ({
      choice: value,
      keywords: (spec.keywordAliases![value] ?? []).map((k) => k.toLowerCase()),
    }));
  }

  // No explicit keywordAliases — use built-in synonym map + enum value itself
  return enumValues.map((value) => {
    const lowerValue = value.toLowerCase();
    const synonyms = ENUM_SYNONYM_MAP[lowerValue] ?? ENUM_SYNONYM_MAP[value] ?? [];
    // Deduplicate: enum value itself + synonyms, all lowercased
    const allKeywords = [lowerValue, ...synonyms.map((s) => s.toLowerCase())];
    const unique = [...new Set(allKeywords)];
    return { choice: value, keywords: unique };
  });
}