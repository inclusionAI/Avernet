import { boundedRepairJson, RepairBatchError, type RepairRevision } from '../contracts/repair-batch.js';
import type { RepairTaskDetail } from '../contracts/repair-workbench.js';
const HISTORY_BYTES = 6 * 1024 * 1024;
export const MAX_TASK_HTTP_BYTES = 8 * 1024 * 1024;
/** Internal task reads retain the entire chain. Only transport history is paginated. */
export function projectTaskHistory(task: RepairTaskDetail, page = 1, pageSize = 20): RepairTaskDetail {
  if (!Number.isSafeInteger(page) || page < 1 || !Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 20) throw new RepairBatchError('INVALID_INPUT', 'Invalid task history page');
  const overhead = Buffer.byteLength(JSON.stringify({ ...task, revisions: [] }), 'utf8') + 1024;
  const pages: RepairRevision[][] = [[]]; let used = overhead;
  for (const revision of task.revisions) {
    const bytes = Buffer.byteLength(JSON.stringify(revision), 'utf8') + 1;
    if (pages.at(-1)!.length && (pages.at(-1)!.length >= pageSize || used + bytes > HISTORY_BYTES)) { pages.push([]); used = overhead; }
    pages.at(-1)!.push(revision); used += bytes;
  }
  const selectedPage = Math.min(page, pages.length);
  const projection = { ...task, revisions: pages[selectedPage - 1], revisionPage: { page: selectedPage, pageSize, total: task.revisions.length, totalPages: pages.length } };
  const response = JSON.parse(JSON.stringify(projection)) as RepairTaskDetail;
  boundedRepairJson(response, MAX_TASK_HTTP_BYTES);
  return response;
}
