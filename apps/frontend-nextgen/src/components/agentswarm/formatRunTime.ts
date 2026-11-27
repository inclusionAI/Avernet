/**
 * Formatting shared by the Agent Compute list and detail views.
 */

/**
 * `yyyy-MM-dd HH:mm:ss`, the Java SimpleDateFormat pattern used elsewhere in
 * this product. Seconds are included because a run log has events seconds
 * apart (guard trips, retries) that are indistinguishable at minute precision.
 *
 * Built from parts on purpose: handing the locale the job lets it pick its own
 * separators (zh-CN gives `2026/11/11 10:56`), and `hourCycle: 'h23'` alone
 * does not guarantee a 24-hour clock across engines.
 */
const TIME_FORMAT = new Intl.DateTimeFormat('en-CA', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
});

/**
 * The gateway reports epoch **milliseconds**, but tolerate seconds so an older
 * payload cannot render as an invalid date.
 */
export function formatRunTime(value: number | null | undefined): string | null {
  if (!value) return null;
  const millis = value > 10_000_000_000 ? value : value * 1000;
  const date = new Date(millis);
  if (Number.isNaN(date.getTime())) return null;
  const parts = TIME_FORMAT.formatToParts(date);
  const get = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? '';
  return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}:${get('second')}`;
}

/** Same contract, with an em dash for "no value" instead of null. */
export function formatRunTimeOrDash(value: number | null | undefined): string {
  return formatRunTime(value) ?? '—';
}