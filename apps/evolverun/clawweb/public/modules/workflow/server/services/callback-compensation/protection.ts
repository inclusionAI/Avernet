import { createCipheriv, createDecipheriv, createHash, randomBytes } from 'node:crypto';
import { CompensationError } from './types.js';

/** Stable canonicalization also supports MySQL Date objects without object-key ambiguity. */
export function canonical(value: unknown): string {
  if (value instanceof Date) return JSON.stringify(value.toISOString());
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value !== null && typeof value === 'object') return `{${Object.keys(value).sort()
    .map(k => `${JSON.stringify(k)}:${canonical((value as Record<string, unknown>)[k])}`).join(',')}}`;
  return JSON.stringify(value) ?? 'null';
}
export function hash(value: unknown): string { return createHash('sha256').update(canonical(value)).digest('hex'); }
export function bodyHash(rawBody: string): string { return createHash('sha256').update(rawBody).digest('hex'); }
export function protect(value: unknown, key: Buffer, aad: string): string {
  const iv = randomBytes(12); const cipher = createCipheriv('aes-256-gcm', key, iv);
  cipher.setAAD(Buffer.from(aad));
  const bytes = Buffer.concat([cipher.update(JSON.stringify(value), 'utf8'), cipher.final()]);
  return ['v1', iv.toString('base64'), cipher.getAuthTag().toString('base64'), bytes.toString('base64')].join('.');
}
export function unprotect<T>(value: string, key: Buffer, aad: string): T {
  try {
    const [version, iv, tag, bytes, extra] = value.split('.');
    if (version !== 'v1' || extra !== undefined) throw new Error('Invalid envelope');
    const decipher = createDecipheriv('aes-256-gcm', key, Buffer.from(iv, 'base64'), { authTagLength: 16 });
    decipher.setAAD(Buffer.from(aad)); decipher.setAuthTag(Buffer.from(tag, 'base64'));
    return JSON.parse(Buffer.concat([decipher.update(Buffer.from(bytes, 'base64')), decipher.final()]).toString('utf8'));
  } catch { throw new CompensationError('PROTECTED_SNAPSHOT_INVALID', 409); }
}
