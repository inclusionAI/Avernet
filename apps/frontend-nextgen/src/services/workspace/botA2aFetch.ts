/** Browser-direct A2A proxy credentials. IAM and the RPC body stay SDK-owned. */
export interface A2aProxyConnection {
  a2aBaseUrl: string | null;
  a2aWithheld: boolean;
  a2aHeaders?: Record<string, string>;
  expiresAt: string;
}

const TOKEN_HEADER = 'X-PROXYPASS-TOKEN';
const REFRESH_MARGIN_MS = 15_000;

function tokenOf(connection: A2aProxyConnection): string {
  const entries = Object.entries(connection.a2aHeaders ?? {});
  if (entries.length !== 1 || entries[0][0].toLowerCase() !== TOKEN_HEADER.toLowerCase()
    || typeof entries[0][1] !== 'string' || !/^[\x21-\x7e]+$/.test(entries[0][1])) {
    throw new Error('Invalid A2A proxy credential');
  }
  return entries[0][1];
}

function waitForRefresh<T>(work: Promise<T>, signal?: AbortSignal | null): Promise<T> {
  if (!signal) return work;
  return new Promise((resolve, reject) => {
    const abort = () => reject(new DOMException('Aborted', 'AbortError'));
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
    void work.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort));
  });
}

export function createBotA2aFetch(
  initial: A2aProxyConnection,
  refresh: () => Promise<A2aProxyConnection>,
  fetchImpl: typeof fetch = (...args) => fetch(...args),
): typeof fetch | undefined {
  // Legacy/direct A2A retains its existing fetch and selection behavior.
  if (!initial.a2aHeaders || Object.keys(initial.a2aHeaders).length === 0) return undefined;
  let current = initial;
  let refreshing: Promise<void> | undefined;
  const base = initial.a2aBaseUrl?.replace(/\/$/, '') ?? '';

  return async (input, init) => {
    const request = typeof Request !== 'undefined' && input instanceof Request ? input : undefined;
    const rawUrl = request ? request.url : String(input);
    const signal = init?.signal ?? request?.signal;
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    const parsedBase = new URL(base);
    if (parsedBase.protocol !== 'https:' || parsedBase.username || parsedBase.password
      || parsedBase.search || parsedBase.hash
      || ![`${base}/.well-known/agent-card.json`, `${base}/rpc`].includes(rawUrl)) {
      throw new Error('A2A request is outside the issued endpoint');
    }

    if (!(Date.parse(current.expiresAt) > Date.now() + REFRESH_MARGIN_MS)) {
      if (!refreshing) {
        refreshing = refresh().then((next) => {
          // A restart may select another container. Re-bootstrap explicitly;
          // do not silently move a running context or a cancellation to it.
          if (next.a2aWithheld || next.a2aBaseUrl?.replace(/\/$/, '') !== base
            || !(Date.parse(next.expiresAt) > Date.now() + REFRESH_MARGIN_MS)) {
            throw new Error('A2A connection changed or expired; reconnect required');
          }
          tokenOf(next);
          current = next;
        }).finally(() => { refreshing = undefined; });
      }
      await waitForRefresh(refreshing, signal);
    }
    if (current.a2aWithheld) throw new Error('A2A connection withheld');
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    const headers = new Headers(request?.headers);
    new Headers(init?.headers).forEach((value, key) => headers.set(key, value));
    headers.set(TOKEN_HEADER, tokenOf(current));
    // No redirects, cookies, buffering or automatic POST retries. SSE and
    // AbortSignal remain fetch-native; uncertain sends must never be replayed.
    return fetchImpl(input, { ...init, headers, credentials: 'omit', redirect: 'error' });
  };
}
