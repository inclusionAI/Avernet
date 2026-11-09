import { createPublicKey, verify, type KeyObject } from 'node:crypto';
import { BlockList, isIP } from 'node:net';
import type { RequestHandler, Response } from 'express';
import type { ClawWebMachineEnvironment } from './environment.js';

const MAX_CLOCK_SKEW_MS = 5 * 60 * 1000;
const loopback = new BlockList();
loopback.addSubnet('127.0.0.0', 8, 'ipv4');
loopback.addAddress('::1', 'ipv6');

function isSocketLoopback(address: string | undefined): boolean {
  if (!address) return false;
  const version = isIP(address);
  return version !== 0 && loopback.check(address, version === 4 ? 'ipv4' : 'ipv6');
}
function base64(value: string): Buffer {
  const decoded = Buffer.from(value, 'base64');
  // Buffer's base64 decoder otherwise tolerates junk, whitespace and missing padding.
  if (!value || decoded.toString('base64') !== value) throw new Error('Invalid base64');
  return decoded;
}
function deny(res: Response, status: 401 | 403 | 503): void {
  const error = status === 503 ? 'Service Unavailable' : status === 403 ? 'Forbidden' : 'Unauthorized';
  const message = status === 503 ? 'Engine signature verification is not configured correctly'
    : status === 403 ? 'Unsigned engine requests require a dev loopback socket' : 'Valid engine signature required';
  res.status(status).json({ success: false, error, message });
}

/** Only for the explicitly mounted engine APIs, after express.json().
 * Wire contract: base64 SPKI-DER Ed25519 public key; X-Timestamp is epoch ms;
 * signature covers `${timestamp}.${JSON.stringify(body)}` (empty body for GET/DELETE).
 * The five-minute window is symmetric. This existing protocol does not bind the
 * method/path or provide one-time nonces; deployments still need a trusted transport.
 * No environment, headers, cookies or forwarded IP can override a configured key.
 */
export function createInternalEngineAuth(config: {
  environment: ClawWebMachineEnvironment;
  publicKeyB64: string;
}): RequestHandler {
  if (config.publicKeyB64 === '') {
    return (req, res, next) => {
      res.setHeader('Cache-Control', 'private, no-store');
      if (config.environment !== 'dev') { deny(res, 503); return; }
      if (!isSocketLoopback(req.socket.remoteAddress)) { deny(res, 403); return; }
      next();
    };
  }

  let publicKey: KeyObject;
  try {
    publicKey = createPublicKey({ key: base64(config.publicKeyB64), format: 'der', type: 'spki' });
    if (publicKey.asymmetricKeyType !== 'ed25519') throw new Error('Ed25519 required');
  } catch {
    // A malformed configured key must never activate the unsigned dev fallback.
    return (_req, res) => { res.setHeader('Cache-Control', 'private, no-store'); deny(res, 503); };
  }

  return (req, res, next) => {
    res.setHeader('Cache-Control', 'private, no-store');
    const timestamp = req.headers['x-timestamp'];
    const signature = req.headers['x-signature'];
    if (typeof timestamp !== 'string' || !/^[1-9]\d{0,15}$/.test(timestamp) || typeof signature !== 'string') {
      deny(res, 401); return;
    }
    const time = Number(timestamp);
    if (!Number.isSafeInteger(time) || Math.abs(Date.now() - time) > MAX_CLOCK_SKEW_MS) { deny(res, 401); return; }
    try {
      const bodyFree = req.method === 'GET' || req.method === 'DELETE' || req.method === 'HEAD';
      const hasPayload = req.headers['transfer-encoding'] !== undefined || Number(req.headers['content-length'] ?? 0) > 0;
      // Reject unsigned payloads rather than authenticate an empty string and pass a body on.
      if ((bodyFree && (req.body !== undefined || hasPayload)) || (hasPayload && req.body === undefined)) {
        deny(res, 401); return;
      }
      const bodyJSON = bodyFree || req.body === undefined ? '' : JSON.stringify(req.body);
      const bytes = base64(signature);
      if (bytes.length !== 64 || !verify(null, Buffer.from(`${timestamp}.${bodyJSON}`), publicKey, bytes)) {
        deny(res, 401); return;
      }
    } catch { deny(res, 401); return; }
    next();
  };
}
