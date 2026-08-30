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
