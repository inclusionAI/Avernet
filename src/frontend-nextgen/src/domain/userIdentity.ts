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
