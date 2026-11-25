import { createPublicKey, verify, type KeyObject } from 'node:crypto';
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Request } from 'express';

export type VerifiedAIStudioService = { serviceId: string };
export type ResolveVerifiedAIStudioService = (request: Request) => Promise<VerifiedAIStudioService | null>;
const rawBodies = new WeakMap<IncomingMessage, Buffer>();
const prefix = '/api/internal/aistudio-train-watches';
/** Pass as express.json({ verify: captureAIStudioRawBody }) BEFORE any JSON parser.
 * Keep the original bytes; JSON.stringify(req.body) is not the signed message. */
export function captureAIStudioRawBody(request: IncomingMessage, _response: ServerResponse, bytes: Buffer): void {
  const path = request.url?.split('?')[0];
  if (path === prefix || path?.startsWith(`${prefix}/`) || path === '/api/internal/events/aistudio-train-manual-audit') rawBodies.set(request, Buffer.from(bytes));
}
export class AIStudioServiceAuthUnavailable extends Error {
  constructor() { super('AIStudio service authentication unavailable'); }
}
/** ClawMind ApiClient v1: Ed25519 SPKI DER key, timestamp in milliseconds,
 * message `${timestamp}.${rawJSONBody}`; GET has an empty body. v1 does NOT bind
 * path/query. The key represents a trusted service, not an owner or a Web user.
 * Routes must still verify every target against stored flow ownership. */
export function createAIStudioServiceAuthenticator(options: { publicKeyB64: string; now?: () => number }): ResolveVerifiedAIStudioService {
  let key: KeyObject | undefined;
  try {
    if (options.publicKeyB64 && /^[A-Za-z0-9+/]+={0,2}$/.test(options.publicKeyB64)) {
      key = createPublicKey({ key:Buffer.from(options.publicKeyB64,'base64'),format:'der',type:'spki' });
      if (key.asymmetricKeyType !== 'ed25519') key = undefined;
    }
  } catch { key = undefined; }
  return async request => {
    if (!key) throw new AIStudioServiceAuthUnavailable(); // no environment or dev unsigned-pass
    // express.json inflates encoded bodies before verify(); v1 signs raw JSON, not an inflated substitute.
    const encoding = request.headers['content-encoding'];
    if (encoding !== undefined && encoding !== 'identity') return null;
    const timestamp = request.headers['x-timestamp']; const signature = request.headers['x-signature'];
    if (typeof timestamp !== 'string' || !/^\d+$/.test(timestamp) || !Number.isSafeInteger(Number(timestamp))
      || Math.abs((options.now?.() ?? Date.now()) - Number(timestamp)) > 300000
      || typeof signature !== 'string' || !/^[A-Za-z0-9+/]{86}==$/.test(signature)) return null;
    const raw = rawBodies.get(request);
    const emptyMethod = request.method === 'GET' || request.method === 'HEAD';
    // A body without verified parser capture, or a GET carrying a body, is ambiguous.
    if ((!emptyMethod && !raw) || (emptyMethod && ((raw?.length ?? 0) > 0 || Number(request.headers['content-length'] ?? 0) > 0 || request.headers['transfer-encoding']))) return null;
    const body = emptyMethod ? Buffer.alloc(0) : raw!;
    try {
      const valid = verify(null,Buffer.concat([Buffer.from(`${timestamp}.`),body]),key,Buffer.from(signature,'base64'));
      return valid ? { serviceId:'clawmind' } : null;
    } catch { return null; }
  };
}
