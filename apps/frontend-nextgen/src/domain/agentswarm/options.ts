/**
 * Run-option editors and validation.
 *
 * Bounds come from the gateway's own wire caps, not a demo table: the server
 * validates and clamps these, so a form that allowed more would only produce a
 * 422 the user cannot act on.
 */
import type { RunOptionsDTO } from '@/services/agentswarm/types';

export interface OptionField {
  key: keyof RunOptionsDTO;
  label: string;
  hint: string;
  min: number;
  max: number;
  /** Whole numbers only (counts) versus fractional seconds. */
  integer: boolean;
}

/**
 * Gateway caps: `max_turns` 1..999, `max_total_agents` 1..256 (enums.py),
 * `max_width` 1..16, `max_attempts` 1..3, and the seconds fields are bounded by
 * each other's slice math with 3600 as the outer envelope. Where this form is
 * narrower than the gateway, the field says so.
 */
export const OPTION_FIELDS: readonly OptionField[] = [
  // Capped at 128 of the gateway's 256: each agent provisions a container, so
  // a run this wide is already minutes of provisioning before any work starts.
  { key: 'max_total_agents', label: 'Agent 总数上限', hint: '本次运行最多派生多少个 Agent', min: 1, max: 128, integer: true },
  // Capped at 8 rather than the gateway's 16: more simultaneous personas than
  // this contend for the same container budget without finishing sooner.
  { key: 'max_width', label: '最大并发数', hint: '同时执行的 Agent 数，超出排队', min: 1, max: 8, integer: true },
  // 128 is a deliberate ceiling: the gateway accepts up to 999 by clamping, but
  // a turn budget that large only makes sense for runs parked on human
  // authorization, which this surface does not have.
  { key: 'max_turns', label: '编排轮次上限', hint: '调度器的决策轮次预算', min: 1, max: 128, integer: true },
  { key: 'max_attempts', label: '单个 Agent 重试次数', hint: '全新上下文重试，最多 3 次', min: 1, max: 3, integer: true },
  { key: 'slice_max_seconds', label: '单切片时限（秒）', hint: '调度器一次切片的总时限', min: 1, max: 3600, integer: false },
  { key: 'per_attempt_timeout_seconds', label: '单次尝试时限（秒）', hint: '一次尝试任务的上限', min: 1, max: 1800, integer: false },
  { key: 'setup_timeout_seconds', label: '启动准备时限（秒）', hint: '派生 + 授权 + 就绪的阶段上限', min: 1, max: 1800, integer: false },
] as const;

export interface OptionIssue {
  key: keyof RunOptionsDTO;
  message: string;
}

const isBlank = (value: unknown) => value === undefined || value === null || value === '';

/**
 * Validates the options the user typed. An unset field means "use the
 * deployment default" and is always allowed — omitting is how the user declines
 * to override anything.
 */
export function validateOptions(options: RunOptionsDTO | null | undefined): OptionIssue[] {
  if (!options) return [];
  const issues: OptionIssue[] = [];
  for (const field of OPTION_FIELDS) {
    const raw = options[field.key];
    if (isBlank(raw)) continue;
    const numeric = Number(raw);
    if (!Number.isFinite(numeric)) {
      issues.push({ key: field.key, message: `${field.label}必须是数字` });
      continue;
    }
    if (field.integer && !Number.isInteger(numeric)) {
      issues.push({ key: field.key, message: `${field.label}必须是整数` });
      continue;
    }
    if (numeric < field.min || numeric > field.max) {
      issues.push({ key: field.key, message: `${field.label}需在 ${field.min}–${field.max} 之间` });
    }
  }
  // The orchestrator deadline has to contain one attempt, or a slice can expire
  // before the attempt it bounds ever could.
  const slice = Number(options.slice_max_seconds);
  const attempt = Number(options.per_attempt_timeout_seconds);
  if (Number.isFinite(slice) && Number.isFinite(attempt) && attempt > slice) {
    issues.push({ key: 'per_attempt_timeout_seconds', message: '单次尝试时限不能大于单切片时限' });
  }
  return issues;
}

/** Drops blank values so an untouched field is omitted rather than sent as null. */
export function normalizeOptions(options: RunOptionsDTO | null | undefined): RunOptionsDTO | undefined {
  if (!options) return undefined;
  const entries = Object.entries(options).filter(([, value]) => !isBlank(value));
  if (!entries.length) return undefined;
  return Object.fromEntries(entries) as RunOptionsDTO;
}

export function hasAnyOption(options: RunOptionsDTO | null | undefined): boolean {
  return Object.values(options ?? {}).some((value) => !isBlank(value));
}