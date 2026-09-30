/** OpenAPI user_id normalization shared by user-scoped public API callers. */
export function resolveOpenApiUserId(identityId: string): string {
  const colonIdx = identityId.indexOf(':');
  let userId = colonIdx >= 0 ? identityId.slice(colonIdx + 1) : identityId;
  const humanPrefix = 'human_';
  if (userId.startsWith(humanPrefix)) userId = userId.slice(humanPrefix.length);
  return userId;
}

export function isResolvableUserId(identityId?: string | null): identityId is string {
  return Boolean(identityId && resolveOpenApiUserId(identityId).trim());
}

/** `me` 仅是前端展示占位身份，绝不能作为真实 OpenAPI user_id。 */
export function normalizeOpenApiUserId(identityId?: string | null): string {
  if (!identityId) return '';
  const normalized = resolveOpenApiUserId(identityId).trim();
  return normalized.toLowerCase() === 'me' ? '' : normalized;
}

export type UserIdentityKind = 'human' | 'user' | 'bot' | 'system';

export interface DisplayNameCandidate {
  id?: string | null;
  kind?: UserIdentityKind;
  name?: string | null;
  fallbackId?: string | null;
}

export interface AuthenticatedUserName {
  userId: string;
  name?: string | null;
}

function looksLikeBotIdentityId(value: string): boolean {
  return /^(?:bot|agent)(?:_|:)/i.test(value.trim());
}

/**
 * Normalize an id only when it is safe to treat the value as a human identity.
 * Bot compound ids must not be reduced to their owner suffix for display matching.
 */
export function normalizeHumanUserId(
  identityId?: string | null,
  kind?: UserIdentityKind,
): string {
  const raw = identityId?.trim() ?? '';
  if (!raw || kind === 'bot' || kind === 'system' || looksLikeBotIdentityId(raw)) return '';

  if (kind === 'human' || kind === 'user') return normalizeOpenApiUserId(raw);

  // Unknown compound ids are unsafe. Accept only known human encodings or plain ids.
  if (/^(?:human_|user_id:)/i.test(raw) || !raw.includes(':')) {
    return normalizeOpenApiUserId(raw);
  }
  return '';
}

export function isSameHumanIdentity(
  candidateId?: string | null,
  authenticatedUserId?: string | null,
  candidateKind?: UserIdentityKind,
): boolean {
  const candidate = normalizeHumanUserId(candidateId, candidateKind);
  const authenticated = normalizeHumanUserId(authenticatedUserId, 'human');
  return Boolean(candidate && authenticated && candidate === authenticated);
}

/**
 * Resolve one display name without allowing another human participant to borrow
 * the authenticated user's name. The original id remains a display fallback.
 */
export function resolveAuthenticatedDisplayName(
  candidate: DisplayNameCandidate,
  authenticatedUser?: AuthenticatedUserName | null,
): string {
  const candidateId = candidate.id?.trim() || candidate.fallbackId?.trim() || '';
  const candidateName = candidate.name?.trim() || '';
  const authenticatedName = authenticatedUser?.name?.trim() || '';

  if (
    authenticatedUser &&
    isSameHumanIdentity(candidateId, authenticatedUser.userId, candidate.kind) &&
    authenticatedName
  ) {
    return authenticatedName;
  }

  return candidateName || candidateId;
}
