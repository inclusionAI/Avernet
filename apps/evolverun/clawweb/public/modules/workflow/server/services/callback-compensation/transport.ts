import { createHmac } from 'node:crypto';
import type { CompensationTransport } from './types.js';

export function callbackHeaders(configId: string, secret: string, rawBody: string, now: number): Record<string, string> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Callback-Config-Id': configId };
  if (secret) {
    const timestamp = String(Math.trunc(now));
    headers['X-Callback-Timestamp'] = timestamp;
    headers['X-Callback-Signature-256'] = `sha256=${createHmac('sha256', secret).update(`${timestamp}.${rawBody}`).digest('hex')}`;
  }
  return headers;
}
export const fetchTransport: CompensationTransport = async request => {
  const response = await fetch(request.url, {
    method: 'POST', headers: request.headers, body: request.rawBody,
    redirect: 'error', signal: request.signal,
  });
  // Do not collect receiver content: it may echo credentials or business payloads.
  void response.body?.cancel().catch(() => undefined);
  return { status: response.status };
};
