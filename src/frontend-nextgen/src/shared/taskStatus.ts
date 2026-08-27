/** 任务状态归一化：兼容后端产品层状态和旧运行时状态。 */
export type ProductTaskStatus = 'DRAFTING' | 'DEFINED' | 'EXECUTING' | 'REVIEWING' | 'DONE' | 'FAILED' | 'CANCELLED';

/**
 * 旧运行时映射：
 * HUNG→REVIEWING、DONE→DONE、FAILED→FAILED、CANCELLED→CANCELLED、
 * PENDING→DEFINED、PLANNING/RUNNING→EXECUTING，其它未知值→EXECUTING。
 */
export function normalizeTaskStatus(status: unknown): ProductTaskStatus {
  const value = String(status ?? '')
    .trim()
    .toUpperCase();
  switch (value) {
    case 'DRAFTING':
    case 'DEFINED':
    case 'EXECUTING':
    case 'REVIEWING':
    case 'DONE':
    case 'FAILED':
    case 'CANCELLED':
      return value;
    case 'HUNG':
      return 'REVIEWING';
    case 'PENDING':
      return 'DEFINED';
    case 'PLANNING':
    case 'RUNNING':
    default:
      return 'EXECUTING';
  }
}
