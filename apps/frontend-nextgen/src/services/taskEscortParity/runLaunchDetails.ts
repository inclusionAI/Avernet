import type { OverviewLaunchField } from '@/domain/taskEscortParity/overviewTypes';
import { maskedUrl } from './managementValidation';

// Same known-key/text masking policy as management snapshots, extended for runtime state.
// This is display redaction, not a claim that arbitrary user text is secret-free.
const sensitive =
  /secret|token|password|authorization|cookie|credential|api[_-]?key|access[_-]?key|bearer|private[_-]?key|system[_-]?context|state[_-]?json/i;
const tokenMetric = /^(total|input|output|cached|cacheRead|cacheWrite|prompt|completion)[_-]?tokens?$/i;
export const nonblank = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value : null);

function displayUrl(value: string): string {
  const masked = maskedUrl(value);
  try {
    const url = new URL(masked);
    for (const key of Array.from(url.searchParams.keys()))
      if (sensitive.test(key)) url.searchParams.set(key, '[已隐藏]');
    return url.toString();
  } catch {
    return masked;
  }
}

/** Also used at the archive display boundary: never stringify raw runtime envelopes into the UI. */
function redactRunValue(value: unknown, depth = 0): unknown {
  if (depth > 40) return '[内容层级过深]';
  if (Array.isArray(value)) return value.map((item) => redactRunValue(item, depth + 1));
  if (value && typeof value === 'object')
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        key === '_clawmind_state' ||
        (sensitive.test(key) &&
          !(tokenMetric.test(key) && typeof item === 'number' && Number.isFinite(item) && item >= 0))
          ? '[已隐藏]'
          : redactRunValue(item, depth + 1),
      ]),
    );
  if (typeof value === 'string') {
    if (/^https?:\/\//i.test(value)) return displayUrl(value);
    // Opaque JSON nested in execution evidence still needs key masking. Preserve string types.
    if (/^\s*[[{]/.test(value)) {
      try {
        const parsed: unknown = JSON.parse(value);
        const safe = redactRunValue(parsed, depth + 1);
        return JSON.stringify(safe) === JSON.stringify(parsed) ? value : JSON.stringify(safe);
      } catch {
        /* Fixed display policy below; never surface parser errors. */
      }
    }
    if (sensitive.test(value) || value.includes('_clawmind_state')) return '[包含敏感配置的文本已隐藏]';
  }
  return value;
}
export function redactRunEvidence(value: unknown): unknown {
  if (typeof value === 'string' && /^[\s]*[[{]/.test(value)) {
    try {
      return redactRunValue(JSON.parse(value));
    } catch {
      /* Never include parser errors or a malformed sensitive body in display diagnostics. */
    }
  }
  return redactRunValue(value);
}
export function displayRunText(value: unknown): string | null {
  const raw = nonblank(value);
  if (raw === null) return null;
  const safe = redactRunValue(raw);
  return typeof safe === 'string' ? safe : JSON.stringify(safe);
}
function containsInternalState(value: unknown, depth = 0): boolean {
  if (depth > 40) return true;
  if (typeof value === 'string') {
    if (value.includes('_clawmind_state')) return true;
    if (/^\s*[[{]/.test(value)) {
      try {
        return containsInternalState(JSON.parse(value), depth + 1);
      } catch {
        return false;
      }
    }
    return false;
  }
  if (!value || typeof value !== 'object') return false;
  return Object.entries(value).some(
    ([key, child]) => key === '_clawmind_state' || containsInternalState(child, depth + 1),
  );
}
export function runLaunchField(raw: unknown, params: boolean): OverviewLaunchField {
  const field: OverviewLaunchField = {
    label: params ? '原始启动参数' : '输入文本',
    source: params ? 'flow_runs.params_json' : 'flow_runs.input_json',
    text: null,
    note: null,
  };
  if (!nonblank(raw)) return field;
  const stored = raw as string;
  if (params && stored.includes('_clawmind_state')) return { ...field, note: '仅有执行状态，未记录原始启动参数' };
  let value: unknown;
  try {
    value = JSON.parse(stored);
  } catch {
    return { ...field, note: params ? '启动参数记录格式异常，无法确认原始输入' : '输入记录格式异常，无法提取文本' };
  }
  if (params) {
    if (containsInternalState(value)) return { ...field, note: '仅有执行状态，未记录原始启动参数' };
    if (value === null || (typeof value === 'string' && !value.trim())) return field;
    const safe = redactRunValue(value);
    return { ...field, text: typeof safe === 'string' ? safe : JSON.stringify(safe, null, 2) };
  }
  if (typeof value === 'string') return { ...field, text: displayRunText(value), note: '字符串格式的输入记录' };
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const input = value as Record<string, unknown>;
    if (nonblank(input.message))
      return { ...field, source: field.source + ' · message', text: displayRunText(input.message) };
    if (nonblank(input.command))
      return {
        ...field,
        label: '已存储命令',
        source: field.source + ' · command',
        text: displayRunText(input.command),
      };
  }
  return field;
}
