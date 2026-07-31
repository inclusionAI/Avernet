/**
 * Task workflow panel — API client (UMD context).
 *
 * Uses raw fetch (no @alipay/bigfish) so the bundle is self-contained. Paths
 * are relative ("/api/tasks/...") — the frontend dev server / runtime proxy
 * routes /api/* to the backend, same-origin, no CORS. (Mirrors bcsPanel's use
 * of a proxied base URL.)
 *
 * Endpoints (backend router.py):
 *   GET /api/tasks/{task_id}/graph                   → TaskGraphView
 *   GET /api/tasks/{task_id}/nodes/{node_id}         → TaskNodeDetailView
 *   GET /api/tasks/{task_id}/nodes/{node_id}/sub-dag → TaskGraphView (v1.5)
 */
import type { TaskGraphView, TaskNodeDetailView } from './types';

export interface FetchOptions {
  signal?: AbortSignal;
}

function taskPath(taskId: string, suffix: string): string {
  return `/api/tasks/${encodeURIComponent(taskId)}${suffix}`;
}

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const err = new Error(
      `task panel request failed: ${res.status} ${res.statusText}`,
    ) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return (await res.json()) as T;
}

export async function fetchTaskGraph(
  taskId: string,
  opts: FetchOptions = {},
): Promise<TaskGraphView> {
  const res = await fetch(taskPath(taskId, '/graph'), { signal: opts.signal });
  return parseJson<TaskGraphView>(res);
}

export async function fetchNodeDetail(
  taskId: string,
  nodeId: string,
  opts: FetchOptions = {},
): Promise<TaskNodeDetailView> {
  const res = await fetch(taskPath(taskId, `/nodes/${encodeURIComponent(nodeId)}`), {
    signal: opts.signal,
  });
  return parseJson<TaskNodeDetailView>(res);
}

// v1.5: cooperative-group sub-DAG drill-down (kept ready, not wired in v1).
export async function fetchSubDag(
  taskId: string,
  nodeId: string,
  opts: FetchOptions = {},
): Promise<TaskGraphView> {
  const res = await fetch(
    taskPath(taskId, `/nodes/${encodeURIComponent(nodeId)}/sub-dag`),
    { signal: opts.signal },
  );
  return parseJson<TaskGraphView>(res);
}

/** Resolve taskId from the UmdPanel-injected props (skill emits params='{"taskId":"..."}'). */
export function resolveTaskId(props: {
  taskId?: string;
  payload?: Record<string, unknown>;
  params?: Record<string, unknown>;
  data?: Record<string, unknown>;
}): string | undefined {
  if (props.taskId) return props.taskId;
  const candidates = [props.payload, props.params, props.data];
  for (const c of candidates) {
    if (c && typeof c.taskId === 'string') return c.taskId;
    if (c && typeof c.params === 'object' && c.params) {
      const t = (c.params as Record<string, unknown>).taskId;
      if (typeof t === 'string') return t;
    }
  }
  return undefined;
}

export function isRetryableStatus(status: number | undefined): boolean {
  return status === undefined || status >= 500;
}
