import { RepairBatchError, type RepairRevision } from '../contracts/repair-batch.js';
import type { RepairTaskDetail } from '../contracts/repair-workbench.js';
const HISTORY_BYTES = 6 * 1024 * 1024;
export const MAX_TASK_HTTP_BYTES = 8 * 1024 * 1024;
/** Internal task reads retain the entire chain. Only transport history is paginated. */
export function projectTaskHistory(task: RepairTaskDetail, page = 1, pageSize = 20): RepairTaskDetail {
  if (!Number.isSafeInteger(page) || page < 1 || !Number.isSafeInteger(pageSize) || pageSize < 1 || pageSize > 20) throw new RepairBatchError('INVALID_INPUT', 'Invalid task history page');
  const overhead = Buffer.byteLength(JSON.stringify({ ...task, revisions: [], executions: undefined }), 'utf8') + 1024;
  const pages: RepairRevision[][] = [[]]; let used = overhead;
  for (const revision of task.revisions) {
    const bytes = Buffer.byteLength(JSON.stringify([revision,task.executions?.[revision.revision]??null]), 'utf8') + 1;
    if (pages.at(-1)!.length && (pages.at(-1)!.length >= pageSize || used + bytes > HISTORY_BYTES)) { pages.push([]); used = overhead; }
    pages.at(-1)!.push(revision); used += bytes;
  }
  const selectedPage = Math.min(page, pages.length);
  const projection = { ...task, revisions: pages[selectedPage - 1], revisionPage: { page: selectedPage, pageSize, total: task.revisions.length, totalPages: pages.length } };
  if (task.executions) {
    const visible = new Set([task.latestAttempt.revision, task.latestSuccessful?.revision, ...projection.revisions.map(row => row.revision)]);
    projection.executions = Object.fromEntries(Object.entries(task.executions).filter(([revision]) => visible.has(Number(revision))));
  }
  // This is an HTTP view, not a signed write identity: bound the actual JSON bytes
  // without recursively canonicalizing the entire multi-megabyte body again.
  const json = JSON.stringify(projection), actualBytes = Buffer.byteLength(json, 'utf8');
  if (actualBytes > MAX_TASK_HTTP_BYTES) throw new RepairBatchError('PAYLOAD_TOO_LARGE', 'Repair payload exceeds its byte limit', { limit: MAX_TASK_HTTP_BYTES, actualBytes });
  return JSON.parse(json) as RepairTaskDetail;
}
