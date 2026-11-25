/**
 * V2 fingerprint foundation for an existing-workflow full save. Not wired to a
 * route, ACL, journal provenance, or Git fencing. The caller must authenticate
 * the actor, verify workflow/pack ownership and select all trusted preconditions.
 * A future service must compare expectedDraftToken against the locked draft
 * baseline before beginning the operation; hashing it is not that comparison.
 *
 * repositoryId is a stable, opaque, host-assigned 256-bit lowercase hex ID,
 * never a URL, credential, or a digest of either. Its provenance and uniqueness
 * cannot be proven by this function: it must not come from client input.
 * No credential/transport fields are accepted. Workflow and file contents are
 * arbitrary user data, not secret-scanned; callers must not submit secrets and
 * must protect the returned request and projection as user content.
 *
 * The SHA-256 input is UTF-8 of the domain followed by JSON.stringify of a
 * fixed-position array described below. Object property order is irrelevant;
 * change array order and every supplied string are exact. specJson is validated
 * by the lossless projection, but its ORIGINAL spelling is hashed (including
 * whitespace, escapes, object order and numeric spelling). The projected JSON
 * is suitable for persistence but is not the fingerprint representation.
 * The returned request is a synchronously copied, deeply frozen snapshot.
 * Subsequent candidate construction MUST use that snapshot, never the original
 * mutable input. This function does not store it or select Git configuration.
 * requestId is a correlation/idempotency key, not semantic content: retries
 * reuse it; the operation repository must compare both key and inputHash.
 */
import { createHash } from 'node:crypto';
import { types } from 'node:util';
import { projectWorkflowSnapshotJson, type WorkflowSnapshotProjection } from './workflow-snapshot-projection.js';

export const WORKFLOW_SNAPSHOT_SAVE_REQUEST_VERSION = 2 as const;
const DOMAIN = 'workflow-snapshot-save-request:v2\0';
const MAX_CHANGES = 500;
const MAX_BLOB = 1024 * 1024;
const MAX_PACK_BYTES = 8 * 1024 * 1024;
const MAX_PACK_FILES = 500;
const OID = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const ID = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/;
const ACTOR = /^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,254}$/;
const REQUEST = /^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$/;
const BASE64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;

/** Paths are relative to the selected Pack root, never repository-root paths. */
export type WorkflowSnapshotSaveChange =
  | Readonly<{ path: string; operation: 'delete' }>
  | Readonly<{ path: string; operation: 'create' | 'replace'; contentBase64: string; mode: '100644' | '100755' }>;
export type WorkflowSnapshotSaveRequest = Readonly<{
  requestId: string; actorId: string; workflowId: string; packId: string;
  repositoryId: string; sourceVersion: 'current' | 'draft'; sourceCommit: string;
  expectedSavedHead: string | null; expectedDraftToken: string; specJson: string; changes: readonly WorkflowSnapshotSaveChange[];
}>;
export type WorkflowSnapshotSaveFingerprint = Readonly<{
  version: typeof WORKFLOW_SNAPSHOT_SAVE_REQUEST_VERSION;
  inputHash: string;
  request: WorkflowSnapshotSaveRequest;
  projection: WorkflowSnapshotProjection;
  operation: Readonly<{ requestId: string; actorId: string; workflowId: string; kind: 'save'; inputHash: string }>;
}>;

function invalid(): never { throw new Error('Invalid workflow snapshot save request'); }
function record(value: unknown, fields: readonly string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || types.isProxy(value) || Array.isArray(value)) return invalid();
  const proto = Object.getPrototypeOf(value);
  if (proto !== Object.prototype && proto !== null) return invalid();
  const keys = Reflect.ownKeys(value);
  if (keys.length !== fields.length || keys.some(key => typeof key !== 'string' || !fields.includes(key))) return invalid();
  const copy: Record<string, unknown> = Object.create(null);
  for (const key of fields) {
    const descriptor = Object.getOwnPropertyDescriptor(value, key);
    if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) return invalid();
    copy[key] = descriptor.value;
  }
  return copy;
}
function array(value: unknown): unknown[] {
  if (types.isProxy(value) || !Array.isArray(value) || Object.getPrototypeOf(value) !== Array.prototype || value.length > MAX_CHANGES) return invalid();
  if (Reflect.ownKeys(value).length !== value.length + 1) return invalid();
  const result: unknown[] = [];
  for (let i = 0; i < value.length; i++) {
    const descriptor = Object.getOwnPropertyDescriptor(value, String(i));
    if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) return invalid();
    result.push(descriptor.value);
  }
  return result;
}
function identifier(value: unknown): value is string {
  return typeof value === 'string' && ID.test(value) && !value.includes('..') && !value.endsWith('.') && !value.endsWith('.lock');
}
function safePath(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && Buffer.byteLength(value) <= 4096
    && !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)
    && !/[\\\u0000-\u001f\u007f-\u009f]/.test(value)
    && value.split('/').every(part => part !== '' && part !== '.' && part !== '..'
      && part.toLowerCase() !== '.git' && !/^[A-Za-z]:/.test(part));
}

export function fingerprintWorkflowSnapshotSaveRequest(input: WorkflowSnapshotSaveRequest): WorkflowSnapshotSaveFingerprint {
  const v = record(input, ['requestId', 'actorId', 'workflowId', 'packId', 'repositoryId', 'sourceVersion',
    'sourceCommit', 'expectedSavedHead', 'expectedDraftToken', 'specJson', 'changes']);
  if (typeof v.requestId !== 'string' || !REQUEST.test(v.requestId)
    || typeof v.actorId !== 'string' || !ACTOR.test(v.actorId)
    || !identifier(v.workflowId) || !identifier(v.packId)
    || typeof v.repositoryId !== 'string' || !/^[a-f0-9]{64}$/.test(v.repositoryId)
    || (v.sourceVersion !== 'current' && v.sourceVersion !== 'draft')
    || typeof v.sourceCommit !== 'string' || !OID.test(v.sourceCommit)
    || (v.expectedSavedHead !== null && (typeof v.expectedSavedHead !== 'string' || !OID.test(v.expectedSavedHead)))
    || typeof v.expectedDraftToken !== 'string' || !/^[a-f0-9]{64}$/.test(v.expectedDraftToken)
    || typeof v.specJson !== 'string') return invalid();
  // Match candidate-save's source/saved preconditions; initial creation is a
  // different protocol, and a draft must identify the saved commit itself.
  if ((v.sourceVersion === 'draft' && v.expectedSavedHead !== v.sourceCommit)
    || (v.sourceVersion === 'current' && v.expectedSavedHead !== null && v.expectedSavedHead !== v.sourceCommit)) return invalid();
  const changes = array(v.changes);
  const seen = new Set<string>();
  const operations = new Map<string, 'delete' | 'create' | 'replace'>();
  let createCount = 0;
  let deleteCount = 0;
  let changedBytes = 0;
  const validatedChanges: WorkflowSnapshotSaveChange[] = changes.map(item => {
    // Inspect the operation without invoking getters; record() validates all keys.
    if (!item || typeof item !== 'object' || types.isProxy(item)) return invalid();
    const descriptor = Object.getOwnPropertyDescriptor(item, 'operation');
    if (!descriptor || !descriptor.enumerable || !('value' in descriptor)) return invalid();
    const change = descriptor.value === 'delete' ? record(item, ['path', 'operation'])
      : record(item, ['path', 'operation', 'contentBase64', 'mode']);
    if (!safePath(change.path) || seen.has(change.path)) return invalid();
    seen.add(change.path);
    if (change.operation === 'delete') {
      operations.set(change.path, 'delete'); deleteCount++;
      return Object.freeze({ path: change.path, operation: 'delete' as const });
    }
    if ((change.operation !== 'create' && change.operation !== 'replace')
      || (change.mode !== '100644' && change.mode !== '100755')
      || typeof change.contentBase64 !== 'string' || change.contentBase64.length > Math.ceil(MAX_BLOB / 3) * 4
      || !BASE64.test(change.contentBase64)) return invalid();
    const bytes = Buffer.from(change.contentBase64, 'base64');
    if (bytes.length > MAX_BLOB || (changedBytes += bytes.length) > MAX_PACK_BYTES
      || bytes.toString('base64') !== change.contentBase64) return invalid();
    operations.set(change.path, change.operation); if (change.operation === 'create') createCount++;
    return Object.freeze({ path: change.path, operation: change.operation, contentBase64: change.contentBase64, mode: change.mode });
  });
  // Changes are Pack-relative by contract, matching PackCandidateChange. A
  // create/replace file cannot coexist with any descendant or ancestor file.
  // The candidate builder deletes first, so delete-parent + create-child is the
  // one intentional exception; replace-child is not accepted in that shape.
  for (const [file, operation] of operations) {
    for (let slash = file.indexOf('/'); slash !== -1; slash = file.indexOf('/', slash + 1)) {
      const parent = operations.get(file.slice(0, slash));
      if (parent !== undefined && !(parent === 'delete' && operation === 'create')) return invalid();
    }
  }
  const request: WorkflowSnapshotSaveRequest = Object.freeze({
    requestId: v.requestId, actorId: v.actorId, workflowId: v.workflowId, packId: v.packId,
    repositoryId: v.repositoryId, sourceVersion: v.sourceVersion, sourceCommit: v.sourceCommit,
    expectedSavedHead: typeof v.expectedSavedHead === 'string' ? v.expectedSavedHead : null,
    expectedDraftToken: v.expectedDraftToken, specJson: v.specJson, changes: Object.freeze(validatedChanges),
  });
  const projection = Object.freeze(projectWorkflowSnapshotJson(request.workflowId, request.specJson));
  // Every create/replace target is selected-Pack-relative and therefore its
  // bytes are a final Pack lower bound. The selected workflow projection is a
  // distinct required final target. The two required final files (manifest and
  // selected workflow) are balanced against definite creates and deletes;
  // replacements do not increase file count. This deliberately does not count
  // unknown unchanged inventory or arbitrary repository-root paths; only the
  // later candidate builder can inspect the selected Pack tree.
  if (2 + createCount - deleteCount > MAX_PACK_FILES
    || changedBytes + Buffer.byteLength(projection.yaml, 'utf8') > MAX_PACK_BYTES) return invalid();
  const encoded = request.changes.map(change => change.operation === 'delete' ? [change.path, change.operation]
    : [change.path, change.operation, change.contentBase64, change.mode]);
  const encoding = JSON.stringify([request.actorId, request.workflowId, request.packId, request.repositoryId, request.sourceVersion,
    request.sourceCommit, request.expectedSavedHead, request.expectedDraftToken, request.specJson, encoded]);
  const inputHash = createHash('sha256').update(DOMAIN, 'utf8').update(encoding, 'utf8').digest('hex');
  return Object.freeze({ version: WORKFLOW_SNAPSHOT_SAVE_REQUEST_VERSION, inputHash, request, projection,
    operation: Object.freeze({ requestId: request.requestId, workflowId: request.workflowId, actorId: request.actorId, kind: 'save' as const, inputHash }) });
}
