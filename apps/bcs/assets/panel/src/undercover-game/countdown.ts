/**
 * Real countdown helpers. A countdown is only rendered from an actual
 * server-provided deadline (pending HumanInput timeout, current action, or
 * panel params); the view never invents a duration.
 */

export function remainingMs(deadlineAt: number | undefined, now: number): number | undefined {
  if (typeof deadlineAt !== 'number' || !Number.isFinite(deadlineAt)) return undefined;
  return Math.max(0, deadlineAt - now);
}

export function formatCountdown(remaining: number): string {
  const totalSeconds = Math.max(0, Math.ceil(remaining / 1000));
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
}

export function countdownText(deadlineAt: number | undefined, now: number): string | undefined {
  const remaining = remainingMs(deadlineAt, now);
  return remaining === undefined ? undefined : formatCountdown(remaining);
}
