/**
 * Versioned, lossless JSON-document projection for a workflow snapshot.
 * This module does not save, publish, authorize or establish Git provenance.
 * V1 compares exact JSON values (object key order ignored; array order retained),
 * not defaults or other transformations from the workflow validator.
 * At most 512 total nodes (workflow, loop bodies and node templates) and eight
 * nested loop-body levels are accepted before invoking the existing validator.
 * dependsOn and triggerRule are checked against original JSON values in those
 * structures. This is not a full runtime schema or graph validation.
 */
import { createHash } from 'node:crypto';
import { isAlias, isMap, isScalar, parseDocument, stringify, visit } from 'yaml';
import { validateSpec } from '../validators/workflow-validator.js';
import { validateWorkflowSemantics } from '../workflow.js';

export const WORKFLOW_SNAPSHOT_PROJECTION_VERSION = 1 as const;
export const WORKFLOW_SNAPSHOT_MAX_BYTES = 1024 * 1024;
export const WORKFLOW_SNAPSHOT_MAX_NODES = 512;
export const WORKFLOW_SNAPSHOT_MAX_LOOP_DEPTH = 8;
const MAX_DEPTH = 100;
const MAX_VALUES = 100_000;
const DIGEST_DOMAIN = 'workflow-snapshot-projection:v1\0';

export type WorkflowSnapshotProjection = Readonly<{
  version: typeof WORKFLOW_SNAPSHOT_PROJECTION_VERSION;
  /** SHA-256 of the domain separator and canonical JSON, in lowercase hex. */
  digest: string;
  /** Canonical JSON for DB persistence. */
  specJson: string;
  /** Deterministic YAML generated from the same JSON values. */
  yaml: string;
}>;

function invalid(): never { throw new Error('Invalid workflow snapshot document'); }
function wellFormed(value: string): boolean {
  return !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value);
}

/** Compare numeric decimal values before and after JS conversion, without rounding. */
function decimalIdentity(source: string): string {
  const match = /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/.exec(source);
  if (!match) return invalid();
  const fraction = match[3] ?? match[4] ?? '';
  let digits = ((match[2] ?? '') + fraction).replace(/^0+/, '');
  if (!digits) return '0';
  // The full source is byte-bounded. Discard leading exponent zeros before
  // bounding its magnitude; zero mantissas have already returned above.
  const exponentText = match[5] ?? '0';
  const exponentDigits = exponentText.replace(/^[+-]?0*/, '') || '0';
  // A 1 MiB document cannot cancel an exponent with more than seven digits.
  if (exponentDigits.length > 7) return invalid();
  const sign = exponentText.startsWith('-') ? -1 : 1;
  let exponent = sign * Number(exponentDigits) - fraction.length;
  let trailing = 0;
  while (trailing < digits.length && digits.charCodeAt(digits.length - 1 - trailing) === 48) trailing++;
  if (trailing) { digits = digits.slice(0, -trailing); exponent += trailing; }
  return `${match[1] === '-' ? '-' : ''}${digits}e${exponent}`;
}

/** YAML 1.2 core hex/octal are non-negative integers. Bound before BigInt parsing. */
function safeRadixInteger(source: string, value: number): boolean {
  const match = /^0([xo])([0-9a-fA-F]+)$/.exec(source);
  if (!match || (match[1] === 'o' && !/^[0-7]+$/.test(match[2]))) return false;
  const digits = match[2].replace(/^0+/, '') || '0';
  if (digits.length > (match[1] === 'x' ? 14 : 18)) return false;
  const exact = BigInt(`0${match[1]}${digits}`);
  return exact <= BigInt(Number.MAX_SAFE_INTEGER) && Number(exact) === value;
}

/** Validate without JSON.stringify's silent omission/coercion or invoking getters. */
function canonicalJson(input: unknown): string {
  const ancestors = new Set<object>();
  let bytes = 0;
  let values = 0;
  const add = (part: string): string => {
    bytes += Buffer.byteLength(part);
    if (bytes > WORKFLOW_SNAPSHOT_MAX_BYTES) invalid();
    return part;
  };
  const string = (value: string): string => {
    if (!wellFormed(value) || Buffer.byteLength(value) > WORKFLOW_SNAPSHOT_MAX_BYTES) invalid();
    return add(JSON.stringify(value));
  };
  function encode(value: unknown, depth: number): string {
    if (++values > MAX_VALUES || depth > MAX_DEPTH) invalid();
    if (value === null) return add('null');
    if (typeof value === 'string') return string(value);
    if (typeof value === 'boolean') return add(value ? 'true' : 'false');
    if (typeof value === 'number') return Number.isFinite(value) && !Object.is(value, -0)
      && (!Number.isInteger(value) || Number.isSafeInteger(value)) ? add(JSON.stringify(value)) : invalid();
    if (typeof value !== 'object') return invalid();
    const proto = Object.getPrototypeOf(value);
    const array = Array.isArray(value);
    if (array ? proto !== Array.prototype : proto !== Object.prototype && proto !== null) invalid();
    if (ancestors.has(value)) invalid();
    ancestors.add(value);
    try {
      const keys = Reflect.ownKeys(value);
      if (keys.some(key => typeof key !== 'string')) invalid();
      if (array) {
        const items = value as unknown[];
        // Reject holes, non-index properties and hidden/accessor elements.
        if (items.length > MAX_VALUES || keys.length !== items.length + 1) invalid();
        add('[');
        const parts: string[] = [];
        for (let i = 0; i < items.length; i++) {
          if (i) add(',');
          const descriptor = Object.getOwnPropertyDescriptor(items, String(i));
          if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) invalid();
          parts.push(encode(descriptor.value, depth + 1));
        }
        add(']');
        return `[${parts.join(',')}]`;
      }
      const names = (keys as string[]).sort();
      add('{');
      const parts: string[] = [];
      for (const [index, key] of names.entries()) {
        if (index) add(',');
        const descriptor = Object.getOwnPropertyDescriptor(value, key);
        if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) invalid();
        const encodedKey = string(key);
        add(':');
        parts.push(`${encodedKey}:${encode(descriptor.value, depth + 1)}`);
      }
      add('}');
      return `{${parts.join(',')}}`;
    } finally { ancestors.delete(value); }
  }
  return encode(input, 0);
}

/** Walk supported raw node containers, before any lossy normalization. */
function validateOriginalNodeFields(value: Record<string, unknown>): void {
  const isRecord = (item: unknown): item is Record<string, unknown> =>
    !!item && typeof item === 'object' && !Array.isArray(item);
  const pending: Array<{ nodes: unknown[]; depth: number }> = [];
  let count = 0;
  const enqueue = (nodes: unknown, depth: number, nonempty: boolean): void => {
    if (!Array.isArray(nodes) || (nonempty && !nodes.length)) invalid();
    if (depth > WORKFLOW_SNAPSHOT_MAX_LOOP_DEPTH) throw new Error('Workflow snapshot loop depth limit exceeded');
    count += nodes.length;
    if (count > WORKFLOW_SNAPSHOT_MAX_NODES) throw new Error('Workflow snapshot node limit exceeded');
    pending.push({ nodes, depth });
  };
  enqueue(value.nodes, 0, false);
  if (Object.hasOwn(value, 'nodeTemplates')) {
    if (!isRecord(value.nodeTemplates)) invalid();
    for (const template of Object.values(value.nodeTemplates)) {
      if (!isRecord(template)) invalid();
      enqueue(template.body, 0, true);
    }
  }
  while (pending.length) {
    const { nodes, depth } = pending.pop()!;
    for (const node of nodes) {
      if (!isRecord(node)) invalid();
      if (Object.hasOwn(node, 'dependsOn')
        && (!Array.isArray(node.dependsOn) || node.dependsOn.some(dep => typeof dep !== 'string'))) invalid();
      if (Object.hasOwn(node, 'triggerRule')
        && node.triggerRule !== 'all_success' && node.triggerRule !== 'one_success' && node.triggerRule !== 'all_done') invalid();
      // Body nodes are not checked by the local top-level validator. Validate
      // their minimum shape here; executor-specific runtime rules are out of scope.
      if (typeof node.id !== 'string' || !node.id.trim()) invalid();
      const executor = isRecord(node.executor) ? node.executor : null;
      if (executor?.type === 'loop-group') enqueue(executor.body, depth + 1, true);
      else if (node.type === 'loop-group') enqueue(node.body, depth + 1, true);
      else if (!executor || typeof executor.type !== 'string' || !executor.type.trim()) invalid();
    }
  }
}

/** Validates with the existing workflow validator, while preserving the original document. */
export function projectWorkflowSnapshot(workflowId: string, document: unknown): WorkflowSnapshotProjection {
  if (typeof workflowId !== 'string' || !workflowId || workflowId.trim() !== workflowId) invalid();
  const specJson = canonicalJson(document);
  const value: unknown = JSON.parse(specJson);
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || (value as Record<string, unknown>).id !== workflowId) invalid();
  validateOriginalNodeFields(value as Record<string, unknown>);
  const validation = validateSpec(value);
  if (!validation.valid || !validation.normalizedSpec || validation.normalizedSpec.id !== workflowId) invalid();
  try { validateWorkflowSemantics(validation.normalizedSpec); } catch { invalid(); }
  const yaml = stringify(value, { sortMapEntries: true, lineWidth: 0 });
  if (Buffer.byteLength(yaml) > WORKFLOW_SNAPSHOT_MAX_BYTES) invalid();
  const digest = createHash('sha256').update(DIGEST_DOMAIN).update(specJson).digest('hex');
  return { version: WORKFLOW_SNAPSHOT_PROJECTION_VERSION, digest, specJson, yaml };
}

/**
 * Parse a single Git YAML document without aliases, tags, complex/non-string keys,
 * duplicate keys or unknown extensions. A mismatch is distinct from invalid input.
 */
export function projectWorkflowSnapshotYaml(workflowId: string, yaml: string): WorkflowSnapshotProjection {
  if (typeof yaml !== 'string' || !wellFormed(yaml) || Buffer.byteLength(yaml) > WORKFLOW_SNAPSHOT_MAX_BYTES) invalid();
  const parsed = parseDocument(yaml, { uniqueKeys: false, prettyErrors: false });
  if (parsed.errors.length || parsed.warnings.length) invalid();
  visit(parsed, (_key, node) => {
    if (isAlias(node) || (node && typeof node === 'object'
      && (('anchor' in node && node.anchor) || ('tag' in node && node.tag)))) invalid();
    if (isScalar(node) && typeof node.value === 'number') {
      if (!Number.isFinite(node.value) || Object.is(node.value, -0) || !node.source) invalid();
      if (/^0[xo]/.test(node.source)) {
        if (!safeRadixInteger(node.source, node.value)) invalid();
      } else if (decimalIdentity(node.source) !== decimalIdentity(String(node.value))) invalid();
    }
    if (isMap(node)) {
      const keys = new Set<string>();
      for (const pair of node.items) {
        if (!isScalar(pair.key) || typeof pair.key.value !== 'string' || keys.has(pair.key.value)) invalid();
        keys.add(pair.key.value);
      }
    }
  });
  return projectWorkflowSnapshot(workflowId, parsed.toJS({ maxAliasCount: 0 }));
}

export function compareWorkflowSnapshotYaml(workflowId: string, yaml: string, dbDocument: unknown): Readonly<{
  version: typeof WORKFLOW_SNAPSHOT_PROJECTION_VERSION; matches: boolean; gitDigest: string; dbDigest: string;
}> {
  const git = projectWorkflowSnapshotYaml(workflowId, yaml);
  const db = projectWorkflowSnapshot(workflowId, dbDocument);
  return { version: WORKFLOW_SNAPSHOT_PROJECTION_VERSION, matches: git.digest === db.digest, gitDigest: git.digest, dbDigest: db.digest };
}

/** Project raw JSON only after checking the source text for parse-time loss.
 * JSON.parse alone silently rounds large integers and decimal literals, and
 * discards duplicate object keys. This helper does not establish provenance. */
export function projectWorkflowSnapshotJson(workflowId: string, text: string): WorkflowSnapshotProjection {
  if (typeof text !== 'string' || !wellFormed(text) || Buffer.byteLength(text) > WORKFLOW_SNAPSHOT_MAX_BYTES) invalid();
  let parsed: unknown;
  try { parsed = JSON.parse(text); } catch { return invalid(); }
  if (!compareWorkflowSnapshotYaml(workflowId, text, parsed).matches) invalid();
  return projectWorkflowSnapshot(workflowId, parsed);
}
