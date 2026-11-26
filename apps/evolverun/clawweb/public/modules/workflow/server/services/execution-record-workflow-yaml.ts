import { isDeepStrictEqual } from 'node:util';
import { isAlias, isMap, isScalar, parseDocument, stringify, visit } from 'yaml';

/** Match the existing snapshot document budget without applying today's runtime schema to old runs. */
const MAX_WORKFLOW_BYTES = 1024 * 1024;
const MAX_VALUES = 100_000;
const MAX_DEPTH = 100;

export class ExecutionRecordWorkflowDocumentError extends Error {
  constructor(readonly reason: 'SNAPSHOT_MISSING' | 'IDENTITY_MISMATCH') {
    super('Execution record workflow document unavailable');
  }
}
function invalid(): never { throw new ExecutionRecordWorkflowDocumentError('SNAPSHOT_MISSING'); }
export function workflowObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** A single unambiguous JSON-compatible YAML document, with no alias expansion or coercive keys. */
function parseDocumentValue(text: string, rootFields?: readonly string[]): unknown {
  const document = parseDocument(text, { uniqueKeys: true, prettyErrors: false, strict: true, version: '1.2' });
  if (document.errors.length || document.warnings.length || document.directives.yaml.version !== '1.2') invalid();
  let count = 0;
  const roots = rootFields && isMap(document.contents)
    ? document.contents.items.filter(pair => isScalar(pair.key) && rootFields.includes(String(pair.key.value))).map(pair => pair.value)
    : [document];
  for (const root of roots) visit(root, (_key, node, path) => {
    if (++count > MAX_VALUES || path.length > MAX_DEPTH || isAlias(node)) invalid();
    if (node && typeof node === 'object' && (('anchor' in node && node.anchor) || ('tag' in node && node.tag))) invalid();
    if (isScalar(node) && typeof node.value === 'number'
      && (!Number.isFinite(node.value) || Object.is(node.value, -0)
        || (Number.isInteger(node.value) && !Number.isSafeInteger(node.value)))) invalid();
    if (isMap(node)) {
      for (const pair of node.items) if (!isScalar(pair.key) || typeof pair.key.value !== 'string') invalid();
    }
  });
  return document.toJS({ maxAliasCount: 0 });
}

/** JSON.parse alone silently accepts duplicate keys. Validate the source as well as its JSON value. */
export function parseExecutionRecordWorkflowJson(text: string, rootFields?: readonly string[], maxBytes = MAX_WORKFLOW_BYTES): unknown {
  // Bound before either parser duplicates source strings, including run state and stored associations.
  if (typeof text !== 'string' || text.length > maxBytes || Buffer.byteLength(text) > maxBytes) invalid();
  try {
    const value: unknown = JSON.parse(text);
    if (!isDeepStrictEqual(value, parseDocumentValue(text, rootFields))) invalid();
    return value;
  } catch (error) {
    if (error instanceof ExecutionRecordWorkflowDocumentError) throw error;
    return invalid();
  }
}

export function readExecutionRecordWorkflowSpec(text: string): Record<string, unknown> {
  if (typeof text !== 'string' || Buffer.byteLength(text) > MAX_WORKFLOW_BYTES) invalid();
  const value = parseExecutionRecordWorkflowJson(text);
  if (!workflowObject(value)) invalid();
  return value;
}

function validateIdentity(spec: unknown, workflowId: string): asserts spec is Record<string, unknown> {
  if (!workflowObject(spec)) invalid();
  if (spec.id !== workflowId) throw new ExecutionRecordWorkflowDocumentError('IDENTITY_MISMATCH');
  if (!Array.isArray(spec.nodes) || spec.nodes.some(node => !workflowObject(node))) invalid();
}

/** Structured JSON becomes equivalent YAML, not recovered original bytes or comments. */
export function executionRecordWorkflowYaml(spec: Record<string, unknown>, workflowId: string): string {
  try {
    if (!Object.hasOwn(spec, 'nodes') && typeof spec.content === 'string') {
      if (Object.hasOwn(spec, 'id') && spec.id !== workflowId) throw new ExecutionRecordWorkflowDocumentError('IDENTITY_MISMATCH');
      if (Buffer.byteLength(spec.content) > MAX_WORKFLOW_BYTES) invalid();
      validateIdentity(parseDocumentValue(spec.content), workflowId);
      return spec.content;
    }
    validateIdentity(spec, workflowId);
    const yaml = stringify(spec, { lineWidth: 0 });
    if (Buffer.byteLength(yaml) > MAX_WORKFLOW_BYTES || !isDeepStrictEqual(parseDocumentValue(yaml), spec)) invalid();
    return yaml;
  } catch (error) {
    if (error instanceof ExecutionRecordWorkflowDocumentError) throw error;
    return invalid();
  }
}
