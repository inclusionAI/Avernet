/**
 * card 探测与分场景能力校验（spec stage2 §2 传输选择 / §5 能力校验）。
 * 探测请求不携带任何 token（card 无鉴权）。
 */
export const EXT_BASE = 'https://avernet.local/a2a/ext';
const REPLAY_URI = `${EXT_BASE}/replay/v1`;
const RESOLVE_URI = `${EXT_BASE}/resolve/v1`;
const DEFAULT_CARD_TIMEOUT_MS = 5000;

export enum Scenario { STREAM = 'stream', RECOVERY = 'recovery', HITL = 'hitl' }

export interface ProbeResult { card: any | null; reason?: string }
export interface ScenarioResult { scenario: Scenario; ok: boolean; reason?: string }

const INTERFACE_FAIL = 'no_jsonrpc_interface';

/** card 探测：无 token、带超时上限——探测挂死不允许拖死传输选择流程。 */
export async function fetchCard(
  cardUrl: string, fetchImpl: typeof fetch = fetch,
  opts: { signal?: AbortSignal; timeoutMs?: number } = {},
): Promise<ProbeResult> {
  const controller = new AbortController();
  let timedOut = false;                 // 区分"我们的超时"与"调用方取消"
  const onAbort = () => controller.abort();
  if (opts.signal) {
    if (opts.signal.aborted) controller.abort();
    else opts.signal.addEventListener('abort', onAbort);
  }
  const timer = setTimeout(() => { timedOut = true; controller.abort(); },
    opts.timeoutMs ?? DEFAULT_CARD_TIMEOUT_MS);
  let resp: Response;
  try {
    resp = await fetchImpl(cardUrl, {
      headers: { accept: 'application/json' }, signal: controller.signal,
    });
  } catch {
    if (opts.signal?.aborted) return { card: null, reason: 'aborted' };
    if (timedOut) return { card: null, reason: 'timeout' };
    return { card: null, reason: 'network' };
  } finally {
    clearTimeout(timer);
    opts.signal?.removeEventListener('abort', onAbort);
  }
  if (!resp.ok) return { card: null, reason: `http_${resp.status}` };
  try {
    const raw = await resp.json();
    return { card: (raw && typeof raw === 'object') ? raw : null,
             reason: raw && typeof raw === 'object' ? undefined : 'bad_json' };
  } catch { return { card: null, reason: 'bad_json' }; }
}

export function validateScenarios(
  rawCard: any, scenarios: Scenario[],
): ScenarioResult[] {
  const exts = Array.isArray(rawCard?.capabilities?.extensions) ? rawCard.capabilities.extensions : [];
  const urls = new Set<string>(exts.map((e: any) => e?.url).filter(Boolean));
  return scenarios.map((s) => {
    const fail = (reason: string): ScenarioResult =>
      ({ scenario: s, ok: false, reason });
    const ok = (): ScenarioResult => ({ scenario: s, ok: true });
    if (!Array.isArray(rawCard?.supportedInterfaces)
        || !rawCard.supportedInterfaces.some((i: any) =>
          i?.protocolBinding === 'JSONRPC' && i?.protocolVersion === '1.0')) {
      return fail(INTERFACE_FAIL);
    }
    if (s === Scenario.STREAM) {
      return rawCard.capabilities?.streaming === true ? ok() : fail('not_streaming');
    }
    if (s === Scenario.RECOVERY) {
      return urls.has(REPLAY_URI) ? ok() : fail('missing_replay_extension');
    }
    return urls.has(RESOLVE_URI) ? ok() : fail('missing_resolve_extension');
  });
}