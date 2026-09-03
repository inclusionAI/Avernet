import { createHash } from 'node:crypto';
import { SessionId } from '@deepseek-ai/dsh-session';

export interface BcnSessionIdentityInput {
  sessionKey: string;
  groupId: string;
  bcsSessionId?: string;
}

/**
 * Resolve the BCN identity that owns one DSH conversation.
 *
 * V3 supplies the canonical session explicitly. V2 keeps a group-scoped
 * `session_key`, but carries the concrete non-legacy session in
 * `bcs_group_id`. Frames without either session discriminator retain the
 * historical `session_key` mapping.
 */
export function resolveBcnSessionIdentity(input: BcnSessionIdentityInput): string {
  const canonicalSessionId = input.bcsSessionId?.trim();
  if (canonicalSessionId) return canonicalSessionId;

  const wireGroupId = input.groupId.trim();
  const separator = wireGroupId.indexOf(':');
  if (separator > 0 && separator < wireGroupId.length - 1) return wireGroupId;

  const legacySessionKey = input.sessionKey.trim();
  if (!legacySessionKey) throw new Error('BCN session identity requires a non-empty session_key');
  return legacySessionKey;
}

/**
 * Convert the resolved BCN identity to a fixed-size DSH Session ID.
 * Keeping this translation isolated lets a later canonical V3-only adapter
 * change the wire selection without touching DSH persistence.
 */
export function dshSessionIdForV2(sessionIdentity: string): SessionId {
  // COSEC: hash the server-delivered identity before it reaches DSH's
  // filesystem-backed Session ID boundary; raw protocol text never becomes a path segment.
  const digest = createHash('sha256').update(sessionIdentity, 'utf8').digest('hex');
  return SessionId(`bcn-v2-${digest}`);
}
