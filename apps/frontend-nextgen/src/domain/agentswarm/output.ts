/**
 * Whether an agent's output carries nothing, despite the agent reporting
 * success.
 *
 * An agent can complete with `{"text": ""}` — observed in a run whose roster
 * asked three writers for a word each and got `hello`, ``, `world`. The run
 * then looked green everywhere while one stage had produced nothing, and the
 * orchestrator had to spend a whole extra round re-dispatching to recover.
 *
 * This is a reading of the payload, not a verdict on the agent: the gateway
 * marks it COMPLETED and this module does not dispute that. It exists so the
 * UI can say "completed, but empty" instead of showing a blank area that looks
 * like a rendering fault.
 */
const PROSE_KEYS = ['text', 'result', 'content', 'answer'] as const;

export function isEmptyOutput(value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (typeof value === 'string') return value.trim() === '';
  if (typeof value === 'number' || typeof value === 'boolean') return false;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const keys = Object.keys(record);
    if (keys.length === 0) return true;
    // A payload that is only the prose field is empty when that field is.
    // A richer object is judged on its own fields, not on one of them.
    if (keys.length === 1 && (PROSE_KEYS as readonly string[]).includes(keys[0])) {
      const inner = record[keys[0]];
      return typeof inner === 'string' ? inner.trim() === '' : isEmptyOutput(inner);
    }
    return Object.values(record).every((item) => isEmptyOutput(item));
  }
  return false;
}
