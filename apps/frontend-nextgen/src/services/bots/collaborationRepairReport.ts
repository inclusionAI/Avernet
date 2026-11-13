import type { CollaborationRepairReport, RepairItemStatus } from '@/services/backendApi/bots/botCollaborationRepairController';

// 报告契约（backend 收窄后）：固定三项 ownership / profile / managers；caller_config 不在修复范围。
const components = ['ownership', 'profile', 'managers'] as const;
const states: readonly RepairItemStatus[] = ['succeeded', 'failed', 'unknown', 'skipped_dependency'];

/**
 * 唯一的修复报告 parser：同时校验结构与整体状态一致性，不让「HTTP 200 + 假成功」通过。
 * 旧四项报告（含 caller_config）或 not_applicable 状态与当前契约不符，一律视为未能确认。
 * 未知 error_code 只透传原因码，不改变分项结果。
 */
export function parseCollaborationRepairReport(value: unknown): CollaborationRepairReport {
  const invalid = () => new Error('未能确认修复结果，请稍后重试');
  if (!value || typeof value !== 'object') throw invalid();
  const r = value as Record<string, unknown>;
  if (typeof r.bot_id !== 'string' || !r.bot_id.trim() || !Array.isArray(r.items) || r.items.length !== 3) {
    throw invalid();
  }
  const items = r.items.map((value: unknown, index: number) => {
    if (!value || typeof value !== 'object') throw invalid();
    const item = value as Record<string, unknown>;
    if (
      item.component !== components[index] ||
      !states.includes(item.status as RepairItemStatus) ||
      (item.error_code !== null && item.error_code !== undefined && typeof item.error_code !== 'string')
    ) {
      throw invalid();
    }
    return {
      component: components[index],
      status: item.status as RepairItemStatus,
      error_code: typeof item.error_code === 'string' ? item.error_code : null,
    };
  });
  const hasFailure = items.some((i) => ['failed', 'unknown', 'skipped_dependency'].includes(i.status));
  const status = hasFailure ? (items.some((i) => i.status === 'succeeded') ? 'partial_failed' : 'failed') : 'succeeded';
  if (r.status !== status) throw invalid();
  return { bot_id: r.bot_id, status, items };
}
