import Ajv from 'ajv';
import { containsSecret } from '@avernet/clawweb-shared/server/services/redaction';
import { RepairBatchError, boundedRepairJson } from '../contracts/repair-batch.js';
const sensitiveField = /password|passwd|secret|token|credential|authorization|cookie|api.?key|private.?key/i;
const privateKey = (key: string) => sensitiveField.test(key) || containsSecret({ [key]: 'redaction-probe' });
function redactText(value: string): string {
  return value.replace(/\b(Bearer\s+)\S+|\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]+/gi, '[REDACTED]')
    .replace(/((?:password|secret|token|api[_-]?key|authorization|cookie)\s*[=:]\s*)[^\s,;]+/gi, '$1[REDACTED]');
}
/** History is always text, capped, and strips sensitive keys plus exact input values from output. */
export function redactTrial(value: unknown, sensitiveValues: string[] = []): unknown {
  if (typeof value === 'string') {
    let text = redactText(value);
    const variants = [...new Set(sensitiveValues.flatMap(raw => [raw, JSON.stringify(raw).slice(1, -1)]))].sort((a, b) => b.length - a.length);
    for (const raw of variants) if (raw) text = text.split(raw).join('[REDACTED]');
    return text.slice(0, 8192);
  }
  if (Array.isArray(value)) return value.slice(0, 100).map(item => redactTrial(item, sensitiveValues));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).slice(0, 100).map(([key, item]) => [key, privateKey(key) ? '[REDACTED]' : redactTrial(item, sensitiveValues)]));
  if (['number', 'boolean'].includes(typeof value) && sensitiveValues.includes(String(value))) return '[REDACTED]';
  return value;
}
function schemaRecord(schema: unknown): Record<string, unknown> {
  return schema && typeof schema === 'object' && !Array.isArray(schema) ? schema as Record<string, unknown> : {};
}
function privateSchema(schema: unknown): boolean {
  const node = schemaRecord(schema);
  return node.sensitive === true || node.writeOnly === true || node.format === 'password';
}
function allStrings(value: unknown): string[] {
  const strings: string[] = [], pending = [value], seen = new Set<object>();
  while (pending.length) {
    const item = pending.pop();
    if (['string', 'number', 'boolean'].includes(typeof item)) strings.push(String(item));
    else if (item && typeof item === 'object' && !seen.has(item)) { seen.add(item); pending.push(...Object.values(item)); }
  }
  return strings;
}
const knownSchemaKeys = new Set([
  '$schema', '$id', '$ref', '$comment', '$defs', 'definitions', 'title', 'description', 'default', 'examples',
  'readOnly', 'writeOnly', 'sensitive', 'deprecated', 'format', 'type', 'enum', 'const',
  'multipleOf', 'minimum', 'maximum', 'exclusiveMinimum', 'exclusiveMaximum', 'minLength', 'maxLength', 'pattern',
  'minItems', 'maxItems', 'uniqueItems', 'items', 'additionalItems', 'contains',
  'properties', 'patternProperties', 'additionalProperties', 'required', 'minProperties', 'maxProperties', 'dependencies',
  'allOf', 'anyOf', 'oneOf', 'not', 'if', 'then', 'else',
]);
function objectSchema(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
/** Fragment-only JSON Pointers stay in this schema document. Never load URLs,
 * named anchors, prototype properties or a nested resource with different $id. */
function localSchema(root: unknown, reference: unknown): unknown {
  if (typeof reference !== 'string' || !reference.startsWith('#')) throw Error('unsupported reference');
  const pointer = decodeURIComponent(reference.slice(1));
  if (!pointer) return root;
  if (!pointer.startsWith('/')) throw Error('unsupported anchor');
  let target = root;
  for (const token of pointer.slice(1).split('/')) {
    if (/~(?![01])/.test(token)) throw Error('invalid pointer');
    const key = token.replace(/~1/g, '/').replace(/~0/g, '~');
    if (!target || typeof target !== 'object' || !Object.hasOwn(target, key)) throw Error('missing reference');
    target = (target as Record<string, unknown>)[key];
    if (objectSchema(target) && target !== root && target.$id !== undefined) throw Error('nested resource');
  }
  return target;
}
/** One conservative traversal owns both history masking and output-value removal.
 * Every union/conditional branch contributes sensitivity; unknown schema
 * semantics mask the affected subtree rather than guessing a public branch. */
function analyzeTrialInput(value: unknown, schema?: unknown): { input: unknown; values: string[] } {
  const root = schema ?? true, values = new Set<string>();
  let remaining = 50_000;
  function expand(current: unknown, ancestors = new Set<object>()): Record<string, unknown>[] {
    if (--remaining < 0 || ancestors.size > 64) throw Error('schema limit');
    if (current === undefined || current === true) return [];
    if (!objectSchema(current) || ancestors.has(current)) throw Error('unknown or cyclic schema');
    if (privateSchema(current)) return [current];
    if (Object.keys(current).some(key => !knownSchemaKeys.has(key)) || current !== root && current.$id !== undefined) throw Error('unsupported schema');
    if (['sensitive', 'writeOnly'].some(key => current[key] !== undefined && typeof current[key] !== 'boolean')) throw Error('invalid annotation');
    const next = new Set(ancestors).add(current), nodes = [current];
    if (current.$ref !== undefined) nodes.push(...expand(localSchema(root, current.$ref), next));
    for (const keyword of ['allOf', 'anyOf', 'oneOf']) if (current[keyword] !== undefined) {
      const branches = current[keyword];
      if (!Array.isArray(branches) || !branches.length) throw Error('invalid composition');
      for (const branch of branches) nodes.push(...expand(branch, next));
    }
    for (const keyword of ['not', 'if', 'then', 'else']) if (current[keyword] !== undefined) nodes.push(...expand(current[keyword], next));
    if (current.dependencies !== undefined) {
      if (!objectSchema(current.dependencies)) throw Error('invalid dependencies');
      for (const dependency of Object.values(current.dependencies)) {
        if (!Array.isArray(dependency)) nodes.push(...expand(dependency, next));
        else if (dependency.some(key => typeof key !== 'string')) throw Error('invalid property dependency');
      }
    }
    for (const keyword of ['properties', 'patternProperties']) if (current[keyword] !== undefined && !objectSchema(current[keyword])) throw Error('invalid property schemas');
    for (const pattern of Object.keys(schemaRecord(current.patternProperties))) new RegExp(pattern, 'u');
    return nodes;
  }
  function hide(current: unknown): string {
    for (const value of allStrings(current)) values.add(value);
    return '[REDACTED]';
  }
  function walk(current: unknown, schemas: unknown[], depth: number): unknown {
    let nodes: Record<string, unknown>[];
    try {
      if (depth > 64 || --remaining < 0) throw Error('input limit');
      nodes = schemas.flatMap(node => expand(node));
      if (nodes.some(privateSchema)) return hide(current);
    } catch { return hide(current); }
    if (Array.isArray(current)) return current.map((item, index) => walk(item, nodes.flatMap(node => {
      const child = Array.isArray(node.items) ? node.items[index] ?? node.additionalItems : node.items;
      return [child, node.contains].filter(schema => schema !== undefined);
    }), depth + 1));
    if (current && typeof current === 'object') return Object.fromEntries(Object.entries(current).map(([key, item]) => {
      if (privateKey(key)) return [key, hide(item)];
      const children: unknown[] = [];
      for (const node of nodes) {
        const properties = schemaRecord(node.properties), patterns = schemaRecord(node.patternProperties);
        let matched = Object.hasOwn(properties, key);
        if (matched) children.push(properties[key]);
        for (const [pattern, child] of Object.entries(patterns)) if (new RegExp(pattern, 'u').test(key)) { matched = true; children.push(child); }
        if (!matched && node.additionalProperties !== undefined) children.push(node.additionalProperties);
      }
      return [key, walk(item, children, depth + 1)];
    }));
    return current;
  }
  return { input: walk(value, [root], 0), values: [...values] };
}
export function trialSensitiveValues(value: unknown, schema?: unknown): string[] {
  return analyzeTrialInput(value, schema).values;
}
/** Preview limits apply after scanning all input so late fields cannot leak in output. */
export function redactTrialInput(value: unknown, schema?: unknown): unknown {
  return redactTrial(analyzeTrialInput(value, schema).input);
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
