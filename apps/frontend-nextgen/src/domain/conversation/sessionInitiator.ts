import { normalizeHumanUserId } from '@/domain/userIdentity';

export interface SessionInitiator {
  userId: string;
  displayName?: string;
  avatarUrl?: string;
}

/** Only explicit human creator segments are evidence; never infer the Bot owner. */
export function sessionInitiatorId(sessionId: string): string | null {
  if (sessionId.startsWith('bcs-cli:') || /:agent:member-task$/.test(sessionId)) return null;
  const match = sessionId.match(/^user:([^:]+):/) ?? sessionId.match(/(?:^|:)(?:user|direct):([^:]+)$/);
  return match ? normalizeHumanUserId(match[1]) || null : null;
}
