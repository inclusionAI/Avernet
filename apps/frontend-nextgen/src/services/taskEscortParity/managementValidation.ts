/** Distinguish locally validated protocol failures from unsafe upstream error bodies. */
export class ManagementProtocolError extends Error {}
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new ManagementProtocolError('响应对象协议不完整');
  return value as Record<string, unknown>;
}
export function array(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new ManagementProtocolError('响应列表协议不完整');
  return value;
}
export function text(value: unknown): string {
  if (typeof value !== 'string') throw new ManagementProtocolError('响应文本协议不完整');
  return value;
}
export const nullableText = (value: unknown): string | null =>
  value === null || value === undefined ? null : text(value);
export function bool(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new ManagementProtocolError('响应权限或开关协议不完整');
  return value;
}
export function count(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0)
    throw new ManagementProtocolError('响应数值协议不完整');
  return value;
}
export const nullableCount = (value: unknown): number | null =>
  value === null || value === undefined ? null : count(value);
export function seconds(value: unknown): number {
  const n = count(value);
  if (n >= 1e12) throw new ManagementProtocolError('响应时间协议不是 Unix 秒');
  return n * 1000;
}
export function milliseconds(value: unknown): number {
  const n = count(value);
  if (n > 8640000000000000) throw new ManagementProtocolError('响应时间协议不完整');
  return n;
}
export const strings = (value: unknown): string[] => array(value).map(text);
export function identity(value: unknown, expected: string): void {
  if (value !== expected) throw new ManagementProtocolError('响应身份协议不匹配');
}
export function enumeration<T extends string>(value: unknown, allowed: readonly T[]): T {
  if (!allowed.includes(value as T)) throw new ManagementProtocolError('响应枚举协议不完整');
  return value as T;
}
const sensitive = /secret|token|password|authorization|cookie|credential|api[_-]?key|private[_-]?key/i;
export function maskedUrl(raw: string): string {
  try {
    const url = new URL(raw);
    if (!['http:', 'https:'].includes(url.protocol)) return '[非 HTTP 地址已隐藏]';
    if (url.username) url.username = 'REDACTED';
    if (url.password) url.password = 'REDACTED';
    for (const key of Array.from(url.searchParams.keys()))
      if (sensitive.test(key)) url.searchParams.set(key, '[已隐藏]');
    url.hash = '';
    return url.toString();
  } catch {
    return '[地址格式无效]';
  }
}
/** A snapshot can contain arbitrary node config: redact before it reaches React state. */
export function safeSpecJson(value: unknown): string {
  try {
    const parsed = object(JSON.parse(text(value)));
    const clean = (v: unknown, depth: number): unknown => {
      if (depth > 40) return '[内容层级过深]';
      if (Array.isArray(v)) return v.map((item) => clean(item, depth + 1));
      if (v && typeof v === 'object')
        return Object.fromEntries(
          Object.entries(v).map(([k, item]) => [k, sensitive.test(k) ? '[已隐藏]' : clean(item, depth + 1)]),
        );
      if (typeof v === 'string' && /^https?:\/\//i.test(v)) return maskedUrl(v);
      // Legacy snapshots may wrap YAML/encoded config inside an opaque content string.
      if (typeof v === 'string' && sensitive.test(v)) return '[包含敏感配置的文本已隐藏]';
      return v;
    };
    return JSON.stringify(clean(parsed, 0), null, 2);
  } catch {
    throw new ManagementProtocolError('版本快照 JSON 协议不完整');
  }
}
