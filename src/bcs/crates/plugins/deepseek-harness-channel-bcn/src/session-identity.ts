import { createHash } from 'node:crypto';
import { SessionId } from '@deepseek-ai/dsh-session';

/**
 * V2 uses the BCN-provided session_key as its stable identity source. Keeping
 * this translation isolated lets a future V3 adapter switch to the canonical
 * BCN session id without changing the DSH delivery bridge.
 */
export function dshSessionIdForV2(sessionKey: string): SessionId {
  const digest = createHash('sha256').update(sessionKey, 'utf8').digest('hex');
  return SessionId(`bcn-v2-${digest}`);
}
