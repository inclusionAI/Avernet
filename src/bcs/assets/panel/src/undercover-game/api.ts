import type {
  PendingHumanNode,
  SessionMessage,
  StateMachineNodeDetailResponse,
  StateMachineRunGraph,
} from './types';

export function joinUrl(baseUrl: string, path: string): string {
  const base = baseUrl.replace(/\/+$/, '');
  return `${base}${path.startsWith('/') ? path : `/${path}`}`;
}

export function unwrapEnvelope<T>(body: unknown): T {
  if (
    body && typeof body === 'object' && !Array.isArray(body) &&
    ('code' in body || 'request_id' in body) && 'data' in body
  ) {
    return (body as { data: T }).data;
  }
  return body as T;
}

export class ApiRequestError extends Error {
  readonly status?: number;
  readonly retryable: boolean;
  readonly conflict: boolean;

  constructor(message: string, options: { status?: number; retryable?: boolean } = {}) {
    super(message);
    this.name = 'ApiRequestError';
    this.status = options.status;
    this.retryable = options.retryable ?? Boolean(!options.status || options.status >= 500);
    this.conflict = options.status === 404 || options.status === 408 || options.status === 409 || options.status === 410 || options.status === 422;
  }
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return undefined;
  try { return JSON.parse(text); } catch { return text; }
}

function errorMessage(body: unknown, status: number): string {
  if (typeof body === 'string' && body.trim()) return body.trim();
  if (body && typeof body === 'object') {
    const value = body as Record<string, unknown>;
    if (typeof value.message === 'string' && value.message.trim()) return value.message;
    if (value.data && typeof value.data === 'object' && typeof (value.data as Record<string, unknown>).message === 'string') {
      return String((value.data as Record<string, unknown>).message);
    }
  }
  return `BCS request failed (${status}).`;
}

export async function requestJson<T>(
  baseUrl: string,
  path: string,
  init: RequestInit = {},
  fetchImpl: typeof fetch = fetch,
): Promise<T> {
  let response: Response;
  try {
    response = await fetchImpl(joinUrl(baseUrl, path), { credentials: 'include', ...init });
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    throw new ApiRequestError(error instanceof Error ? error.message : 'Network request failed.');
  }
  const body = await parseBody(response);
  if (!response.ok) {
    throw new ApiRequestError(errorMessage(body, response.status), { status: response.status });
  }
  if (typeof body === 'string') {
    throw new ApiRequestError('游戏接口未返回 JSON，请检查副屏 API 地址及反向代理配置。', { status: response.status });
  }
  return unwrapEnvelope<T>(body);
}

export function fetchRunGraph(baseUrl: string, runId: string, signal?: AbortSignal, fetchImpl?: typeof fetch) {
  return requestJson<StateMachineRunGraph>(baseUrl, `/state-machine-runs/${encodeURIComponent(runId)}/graph`, { signal }, fetchImpl);
}

export function fetchPendingHumanNodes(baseUrl: string, runId: string, signal?: AbortSignal, fetchImpl?: typeof fetch) {
  return requestJson<PendingHumanNode[]>(baseUrl, `/state-machine-runs/${encodeURIComponent(runId)}/pending-human-nodes`, { signal }, fetchImpl);
}

export function fetchNodeDetail(baseUrl: string, runId: string, nodeId: string, signal?: AbortSignal, fetchImpl?: typeof fetch) {
  return requestJson<StateMachineNodeDetailResponse>(baseUrl, `/state-machine-runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}`, { signal }, fetchImpl);
}

export function fetchSessionMessages(baseUrl: string, sessionId: string, signal?: AbortSignal, fetchImpl?: typeof fetch) {
  return requestJson<SessionMessage[]>(baseUrl, `/sessions/${encodeURIComponent(sessionId)}/messages?include_pending=true`, { signal }, fetchImpl);
}

export function respondToHumanNode(
  baseUrl: string,
  runId: string,
  nodeId: string,
  content: string,
  signal?: AbortSignal,
  fetchImpl?: typeof fetch,
) {
  return requestJson<unknown>(baseUrl, `/state-machine-runs/${encodeURIComponent(runId)}/nodes/${encodeURIComponent(nodeId)}/respond`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ content }),
    signal,
  }, fetchImpl);
}
