/** Display metadata only: never use these labels as run identity, purpose or routing authority.
 * Contract and rollout: clawweb/docs/flow-run-markers.md. */
export type FlowRunMarkers = { env: string | null; tag: string | null };

export class FlowRunMarkerError extends Error {}

function marker(value: unknown, field: 'env' | 'tag', fallback: string, maxLength: number): string | null {
  if (value === undefined) return fallback;
  if (value === null) return null;
  if (typeof value !== 'string' || Array.from(value).length > maxLength || field === 'env' && !value.trim()) {
    throw new FlowRunMarkerError(`${field} must be null or a ${field === 'env' ? 'nonblank ' : ''}string of at most ${maxLength} characters`);
  }
  return value;
}

export function normalizeFlowRunMarkers(input: { env?: unknown; tag?: unknown }): FlowRunMarkers {
  return { env: marker(input.env, 'env', 'prod', 32), tag: marker(input.tag, 'tag', '', 255) };
}
