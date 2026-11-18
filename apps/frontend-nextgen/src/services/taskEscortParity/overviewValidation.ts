import { isEnvelopeSuccessAnyDialect } from '@/services/backendApi/types';

export function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('接口返回了非预期的数据结构');
  return value as Record<string, unknown>;
}
export function payload(value: unknown): unknown {
  let current = value;
  for (let depth = 0; depth < 3; depth += 1) {
    if (!current || typeof current !== 'object' || Array.isArray(current)) return current;
    const row = record(current);
    if (('code' in row || 'success' in row) && !isEnvelopeSuccessAnyDialect(row))
      throw new Error(String(row.message || row.error || '接口读取失败'));
    if ('error' in row && !('data' in row)) throw new Error(String(row.message || row.error));
    if (!('data' in row)) return current;
    current = row.data;
  }
  return current;
}
export function requiredText(value: unknown, label: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${label} 字段缺失或无效`);
  return value;
}
export const text = (value: unknown): string | null => (typeof value === 'string' && value.length > 0 ? value : null);
export function metric(value: unknown, max = Infinity): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= max ? value : null;
}
export function count(value: unknown): number | null {
  const number = metric(value);
  return number !== null && Number.isSafeInteger(number) ? number : null;
}
export function list(value: unknown): unknown[] {
  if (!Array.isArray(value)) throw new Error('接口列表字段缺失或无效');
  return value;
}
export const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((entry): entry is string => typeof entry === 'string') : [];
