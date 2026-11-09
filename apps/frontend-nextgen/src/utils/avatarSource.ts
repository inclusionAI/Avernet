export function normalizeAvatarUrl(value: unknown): string | undefined {
  return typeof value === 'string' ? value.trim() || undefined : undefined;
}
export function imageAvatarSource(value: unknown): string | undefined {
  const src = normalizeAvatarUrl(value);
  return src && /^(?:https?:\/\/|\/|data:image\/)/i.test(src) ? src : undefined;
}
