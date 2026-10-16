import Ajv from 'ajv';
import { RepairBatchError, boundedRepairJson } from '../contracts/repair-batch.js';
const sensitiveField = /password|passwd|secret|token|credential|authorization|cookie|api.?key|private.?key/i;
/** History is always text, capped, and strips sensitive keys plus exact input values from output. */
export function redactTrial(value: unknown, sensitiveValues: string[] = []): unknown {
  if (typeof value === 'string') {
    let text = value.replace(/\b(Bearer\s+)\S+|\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]+/gi, '[REDACTED]')
      .replace(/((?:password|secret|token|api[_-]?key|authorization|cookie)\s*[=:]\s*)[^\s,;]+/gi, '$1[REDACTED]');
    for (const raw of sensitiveValues.sort((a, b) => b.length - a.length)) if (raw) text = text.split(raw).join('[REDACTED]');
    return text.slice(0, 8192);
  }
  if (Array.isArray(value)) return value.slice(0, 100).map(item => redactTrial(item, sensitiveValues));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).slice(0, 100).map(([key, item]) => [key, sensitiveField.test(key) ? '[REDACTED]' : redactTrial(item, sensitiveValues)]));
  return value;
}
export function trialSensitiveValues(value: unknown): string[] {
  if (!value || typeof value !== 'object') return [];
  return Object.entries(value).flatMap(([key, item]) => sensitiveField.test(key) && typeof item === 'string' ? [item] : trialSensitiveValues(item));
}
export function validateTrialInput(input: unknown, schema: Record<string, unknown> | null): asserts input is Record<string, unknown> {
  boundedRepairJson(input, 32 * 1024);
  if (!input || typeof input !== 'object' || Array.isArray(input)) throw new RepairBatchError('INVALID_INPUT', '参数需要 JSON 对象');
  if (!schema) return;
  // Full JSON Schema validation, without coercion, implicit defaults or remote refs.
  try {
    const validate = new Ajv({ strict: false, allErrors: true, ownProperties: true }).compile(schema);
    if (!validate(input)) throw new RepairBatchError('INVALID_INPUT', `参数校验失败：${validate.errors?.slice(0, 5).map(error => `${error.instancePath || '$'} ${error.message}`).join('；')}`);
  } catch (error) {
    if (error instanceof RepairBatchError) throw error;
    throw new RepairBatchError('INVALID_INPUT', '候选的参数定义无法校验，请检查 input.schema');
  }
}
