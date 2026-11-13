/**
 * Transport for the agentswarm gateway.
 *
 * Composes on the app-wide `backendRequest` so auth, error-envelope decoding and
 * the error-notify protocol stay identical to every other backend call, rather
 * than introducing a second HTTP stack that would drift.
 */
import { backendRequest, BackendRequestError } from '@/services/backendApi/httpClient';
import type { BackendApiEnvelope } from '@/services/backendApi/types';

export const AGENTSWARM_BASE = '/openapi/v1/agentswarm';

export type AgentswarmErrorKind =
  /** The caller sent no identity source for a bot-kind run (gateway 400). */
  | 'identity_source_missing'
  /** The named identity source is not usable (gateway 422). */
  | 'identity_source_invalid'
  /** The outcome of the write is unknown; the caller must verify before retrying. */
  | 'unconfirmed'
  /** The run does not exist for this tenant. */
  | 'not_found'
  /** The request was rejected for a reason this layer does not classify. */
  | 'refused'
  | 'unknown';

export class AgentswarmRequestError extends Error {
  constructor(
    message: string,
    public kind: AgentswarmErrorKind,
    public status?: number,
    public requestId?: string,
  ) {
    super(message);
    this.name = 'AgentswarmRequestError';
  }
}

function classify(status: number | undefined): AgentswarmErrorKind {
  if (status === 400) return 'identity_source_missing';
  if (status === 422) return 'identity_source_invalid';
  if (status === 404) return 'not_found';
  if (status === 502 || status === 504) return 'unconfirmed';
  if (typeof status === 'number' && status >= 400) return 'refused';
  return 'unknown';
}

function errorText(data: unknown): string | undefined {
  if (!data || typeof data !== 'object') return undefined;
  const record = data as Record<string, unknown>;
  for (const key of ['error', 'detail', 'message']) {
    const value = record[key];
    if (typeof value === 'string' && value.trim()) return value.trim();
    // FastAPI validation errors arrive as a list of issue objects.
    if (Array.isArray(value) && value.length) {
      const first = value[0] as Record<string, unknown> | undefined;
      const msg = first?.msg;
      if (typeof msg === 'string' && msg.trim()) return msg.trim();
    }
  }
  return undefined;
}

/**
 * Reads the gateway's own explanation when it gave one, so a refusal is
 * reported in the backend's words rather than a generic wrapper.
 */
function describe(status: number | undefined, data: unknown, fallback: string): string {
  return errorText(data) ?? `${fallback}${typeof status === 'number' ? `（${status}）` : ''}`;
}

export interface AgentswarmRequestOptions {
  method?: 'GET' | 'POST';
  params?: Record<string, unknown>;
  data?: unknown;
}

/**
 * This surface answers **bare JSON**, not the `{code, data}` envelope most of the
 * backend uses: `POST /runs` returns `{"run_id": "..."}` directly. Only failures
 * are wrapped. So a successful response is the payload itself.
 */
function unwrap<T>(payload: T | BackendApiEnvelope<T>): T {
  if (payload && typeof payload === 'object' && 'code' in (payload as Record<string, unknown>)) {
    const envelope = payload as BackendApiEnvelope<T>;
    const code = Number(envelope.code);
    const status = Number.isFinite(code) ? Math.floor(code / 1000) : undefined;
    if (status !== undefined && (status < 200 || status >= 300)) {
      throw new AgentswarmRequestError(
        describe(status, envelope, '请求被接口拒绝'),
        classify(status),
        status,
        envelope.request_id,
      );
    }
    if (envelope.data === null || envelope.data === undefined) {
      throw new AgentswarmRequestError('接口未返回有效结果', 'unknown', status, envelope.request_id);
    }
    return envelope.data;
  }
  return payload as T;
}

export async function agentswarmRequest<T>(
  path: string,
  { method = 'GET', params = {}, data }: AgentswarmRequestOptions = {},
): Promise<T> {
  try {
    const response = await backendRequest<T | BackendApiEnvelope<T>>(`${AGENTSWARM_BASE}${path}`, {
      method,
      params,
      data,
      // A poll must not multiply into a burst of retries; callers own cadence.
      retryOnTransient: false,
    });
    if (response === null || response === undefined) {
      throw new AgentswarmRequestError('接口未返回有效结果', 'unknown');
    }
    return unwrap(response);
  } catch (reason) {
    if (reason instanceof AgentswarmRequestError) throw reason;
    if (reason instanceof BackendRequestError) {
      const envelope = reason.data as BackendApiEnvelope<unknown> | undefined;
      throw new AgentswarmRequestError(
        describe(reason.status, envelope ?? reason.data, reason.message || '请求失败'),
        classify(reason.status),
        reason.status,
        envelope?.request_id,
      );
    }
    throw reason;
  }
}