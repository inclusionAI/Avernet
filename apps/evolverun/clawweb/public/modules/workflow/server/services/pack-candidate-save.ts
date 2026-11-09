/** Git-only candidate saving. Authorization and trusted source selection belong to the caller.
 * This does not publish a version or coordinate a database transaction. */
import { workflowSavedGitRunner, initSavedWorkflowRepo, resolveSavedWorkflowRef, workflowSavedCommitMessage } from './workflow-saved-ref.js';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { TextDecoder } from 'node:util';
import { parse as parseYaml } from 'yaml';
import { executeGit, withGitOperation } from '@avernet/clawweb-shared/server/services/git-execution';
import { initClawflowsReadRepo, createTempRepo, removeTempRepo, type ClawFlowsGitConfig } from './clawflows-git-client.js';
import { createPreparedGitBundle, restorePreparedGitBundle, readPreparedPackMetadata, verifyPreparedPackMetadata, type PreparedPackMetadata } from './pack-git-prepared.js';

export type PackCandidateChange =
  | { path: string; operation: 'delete' }
  | { path: string; operation: 'create' | 'replace'; contentBase64: string; mode: '100644' | '100755' };
export type SavePackCandidateInput = {
  workflowId: string;
  sourceVersion: 'current' | 'draft';
  sourceCommit: string;
  /** Wire name retained: the wf/<id> write lease, NOT the selected read source. */
  expectedSavedHead: string | null;
  config: ClawFlowsGitConfig;
  changes: PackCandidateChange[];
  /** Keep the selected workflow content in the DB; permit byte-identical structural moves. */
  preserveWorkflowContent?: boolean;
};
export type SavePackCandidateResult = { branch: string; commit: string };
/** Persist this entire server-owned object before push. It contains repository history
 * but no transport configuration, auth files or temp paths. */
export type PreparedPackCandidate = PreparedPackMetadata & Readonly<{
  workflowId: string;
  branch: string;
  commit: string;
  sourceVersion: 'current' | 'draft' | 'initial';
  /** null only for a first save into an unborn repository. */
  sourceCommit: string | null;
  expectedSavedHead: string | null;
  bundleBase64: string;
}>;
export class PackCandidateConflictError extends Error {
  constructor(message = 'Workflow source or wf write lease changed') { super(message); this.name = 'PackCandidateConflictError'; }
}

export class PackWorkflowContentChangedError extends Error {
  constructor() { super('Selected workflow YAML content requires a database save'); this.name = 'PackWorkflowContentChangedError'; }
}

/** A push was attempted but its outcome cannot safely be inferred from an error or a later ref read. */
export class PackCandidateOutcomeUnknownError extends Error {
  constructor(readonly branch: string, readonly commit: string) {
    super('Candidate save outcome unknown; reconcile before retrying');
    this.name = 'PackCandidateOutcomeUnknownError';
  }
}

type Entry = { mode: string; type: string; oid: string; size: number | null };
class MalformedCandidateYamlError extends Error {}
const oidPattern = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const decoder = new TextDecoder('utf-8', { fatal: true });
const maxBlob = 1024 * 1024;
const maxPackBytes = 8 * 1024 * 1024;
const maxPackFiles = 500;
function rejectCurrentOverDifferentSaved(sourceVersion: 'current' | 'draft' | 'initial',
  sourceCommit: string | null, expectedSavedHead: string | null): void {
  if (sourceVersion === 'current' && expectedSavedHead !== sourceCommit) {
    throw new PackCandidateConflictError('Cannot save from current: wf contains a different saved version; edit the saved snapshot instead');
  }
}
function idValid(id: unknown): id is string {
  return typeof id === 'string' && /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(id)
    && !id.includes('..') && !id.endsWith('.') && !id.endsWith('.lock');
}
function safePath(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && Buffer.byteLength(value) <= 4096
    && !/[\\\u0000-\u001f\u007f]/.test(value) && !/^[A-Za-z]:/.test(value)
    && value.split('/').every(part => part !== '' && part !== '.' && part !== '..'
      && part.toLowerCase() !== '.git' && !/^[A-Za-z]:/.test(part));
}
function workflowFile(item: unknown): string {
  const file = typeof item === 'string' ? `workflows/${item}.yaml`
    : item && typeof item === 'object' && !Array.isArray(item) ? (item as Record<string, unknown>).file : undefined;
  if (!safePath(file)) throw new Error('Invalid workflow file path');
  return file;
}
function itemId(item: unknown): unknown {
  return typeof item === 'string' ? item : item && typeof item === 'object' && !Array.isArray(item)
    ? (item as Record<string, unknown>).id : undefined;
}
function isCandidate(file: string): boolean {
  const parts = file.split('/');
  return parts.at(-1) === 'workflow.pack.yaml' && (parts.length === 1 || parts.length === 2
    || (parts.length === 3 && parts[0] === 'packs'));
}

/** Prototype-only ASCII alias guard. Every protected path and its ancestors must
 * have one spelling in the entire Git tree. This is not a complete model of
 * Unicode or every destination filesystem; the host still owns that check. */
function rejectProtectedTreeAliases(entries: Map<string, Entry>, root: string, manifest: Record<string, unknown>): void {
  if (!Array.isArray(manifest.workflows) || manifest.workflows.length > 500) throw new Error('Invalid pack manifest workflows');
  const paths = [`${root}workflow.pack.yaml`];
  const ids = new Set<string>();
  for (const item of manifest.workflows) {
    const id = itemId(item);
    if (!idValid(id) || ids.has(id.toLowerCase())) throw new Error('Aliased workflow identity');
    ids.add(id.toLowerCase());
    paths.push(root + workflowFile(item));
  }
  const protectedNames = new Map<string, string>();
  const protectedFiles = new Set<string>();
  for (const file of paths) {
    const parts = file.split('/');
    if (parts.some(part => !/^[A-Za-z0-9_.-]+$/.test(part) || part === '.' || part === '..' || part.endsWith('.'))) {
      throw new Error('Non-portable protected path');
    }
    let prefix = '';
    for (const [index, part] of parts.entries()) {
      prefix = prefix ? `${prefix}/${part}` : part;
      const key = prefix.toLowerCase();
      const prior = protectedNames.get(key);
      if ((prior !== undefined && prior !== prefix) || protectedFiles.has(key)
        || (index === parts.length - 1 && prior !== undefined)) throw new Error('Aliased protected paths');
      protectedNames.set(key, prefix);
      if (index === parts.length - 1) protectedFiles.add(key);
    }
  }
  for (const name of entries.keys()) {
    // Non-ASCII and other filesystem equivalences need authoritative host checks.
    if (!/^[\x00-\x7f]+$/.test(name)) continue;
    let prefix = '';
    for (const part of name.split('/')) {
      prefix = prefix ? `${prefix}/${part}` : part;
      const key = prefix.toLowerCase();
      const expected = protectedNames.get(key);
      if ((expected !== undefined && expected !== prefix)
        || (protectedFiles.has(key) && prefix !== name)) throw new Error('Aliased protected tree path');
    }
  }
}

/** All fields other than changes must be obtained/validated server-side, not from the request.
 * The caller grants the existing workflow editor authority over the entire selected Pack,
 * including files shared with other workflows. No separate Pack ACL or credential is used.
 * Only wf is read and compare-and-swapped. expectedSavedHead is that lease;
 * sourceCommit must be the observed wf source. No legacy branch is consulted.
 * A successful push is not atomic with any database update or publication. */
export async function withPreparedPackCandidate<T>(input: SavePackCandidateInput,
  callback: (prepared: PreparedPackCandidate, push: () => Promise<SavePackCandidateResult>) => Promise<T>): Promise<T> {
  return preparePackCandidate(input, callback);
}

/** Server-only entry point: workflowYaml must be the authorized, canonical workflow
 * projection, not an untrusted file-API field. This explicit authority takes
 * precedence over preserveWorkflowContent; the old entry point is unchanged. The caller owns authorization,
 * DB persistence and journaling before push; this function only constructs Git. */
export async function withPreparedPackCandidateWithWorkflow<T>(input: SavePackCandidateInput, workflowYaml: string,
  callback: (prepared: PreparedPackCandidate, push: () => Promise<SavePackCandidateResult>) => Promise<T>): Promise<T> {
  if (typeof workflowYaml !== 'string' || Buffer.byteLength(workflowYaml) > maxBlob
    || Buffer.from(workflowYaml).toString('utf8') !== workflowYaml) throw new Error('Invalid workflow content');
  const workflowYamlSnapshot = workflowYaml;
  return preparePackCandidate(input, callback, workflowYamlSnapshot);
}

/** Isolated unified-save prototype guard; legacy candidate and AutoHeal callers
 * retain their existing rules. The trusted host must still authorize writes and
 * address Unicode/filesystem aliases, concurrency and crash recovery. */
export async function withPreparedPackCandidateForUnifiedSave<T>(input: SavePackCandidateInput, workflowYaml: string,
  callback: (prepared: PreparedPackCandidate, push: () => Promise<SavePackCandidateResult>) => Promise<T>): Promise<T> {
  if (typeof workflowYaml !== 'string' || Buffer.byteLength(workflowYaml) > maxBlob
    || Buffer.from(workflowYaml).toString('utf8') !== workflowYaml) throw new Error('Invalid workflow content');
  return preparePackCandidate(input, callback, workflowYaml, true);
}

async function preparePackCandidate<T>(callerInput: SavePackCandidateInput,
  callback: (prepared: PreparedPackCandidate, push: () => Promise<SavePackCandidateResult>) => Promise<T>,
  workflowYamlSnapshot?: string, protectTreeAliases = false): Promise<T> {
  // Capture every value used by construction and transport before the first
  // asynchronous boundary. In particular repository initialization reads config again
  // after awaits, so copying just the outer input is not sufficient.
  const { workflowId, sourceVersion, sourceCommit, expectedSavedHead, preserveWorkflowContent,
    config: callerConfig, changes: callerChanges } = callerInput;
  if (!Array.isArray(callerChanges) || callerChanges.length > 500) throw new Error('Invalid candidate save input');
  let config = callerConfig;
  if (callerConfig && typeof callerConfig === 'object') {
    const { remoteUrl, username, email, token } = callerConfig;
    config = { remoteUrl, username, email, token };
  }
  const input: SavePackCandidateInput = { workflowId, sourceVersion, sourceCommit, expectedSavedHead,
    preserveWorkflowContent, config, changes: callerChanges.map(change => {
      if (!change || typeof change !== 'object') return change as PackCandidateChange;
      const { path, operation, contentBase64, mode } = change as {
        path: string; operation: string; contentBase64?: string; mode?: '100644' | '100755';
      };
      return { path, operation, contentBase64, mode } as PackCandidateChange;
    }) };
  if (!idValid(input.workflowId) || !oidPattern.test(input.sourceCommit)
    || (input.expectedSavedHead !== null && !oidPattern.test(input.expectedSavedHead))
    || !['current', 'draft'].includes(input.sourceVersion)) throw new Error('Invalid candidate save input');
  rejectCurrentOverDifferentSaved(input.sourceVersion, input.sourceCommit, input.expectedSavedHead);
  const seen = new Set<string>();
  let total = 0;
  const changes = input.changes.map(change => {
    if (!change || !safePath(change.path) || seen.has(change.path)) throw new Error('Invalid or duplicate change path');
    seen.add(change.path);
    if (change.operation === 'delete') return { ...change, bytes: undefined };
    if ((change.operation !== 'create' && change.operation !== 'replace') || !['100644', '100755'].includes(change.mode)
      || typeof change.contentBase64 !== 'string' || change.contentBase64.length > Math.ceil(maxBlob / 3) * 4
      || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(change.contentBase64)) {
      throw new Error('Invalid candidate content or mode');
    }
    const bytes = Buffer.from(change.contentBase64, 'base64');
    if (bytes.toString('base64') !== change.contentBase64 || bytes.length > maxBlob || (total += bytes.length) > 16 * 1024 * 1024) {
      throw new Error('Candidate content limit exceeded');
    }
    return { ...change, bytes };
  });
  return withGitOperation({}, async () => {
    const dir = await createTempRepo();
    let attempted: SavePackCandidateResult | undefined;
    let callbackActive = true;
    let pushPromise: Promise<SavePackCandidateResult> | undefined;
    try {
      const savedState = await initSavedWorkflowRepo(dir, input.workflowId, input.config);
      const env: NodeJS.ProcessEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')));
      Object.assign(env, { GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_NO_REPLACE_OBJECTS: '1',
        GIT_NO_LAZY_FETCH: '1', GIT_TERMINAL_PROMPT: '0', GIT_INDEX_FILE: path.join(dir, '.git', 'candidate-index') });
      const git = (args: string[], maxBuffer = 16 * 1024 * 1024) => executeGit(args, { cwd: dir, env, maxBuffer });
      const text = async (args: string[]) => (await git(args)).toString('utf8').trim();
      if (savedState.commit !== input.sourceCommit || savedState.wfHead !== input.expectedSavedHead) {
        throw new PackCandidateConflictError();
      }
      const resolved = await text(['rev-parse', '--verify', `${input.sourceCommit}^{commit}`]);
      if (resolved !== input.sourceCommit) throw new Error('Invalid source commit');
      const readTreeEntries = async (ref: string): Promise<Map<string, Entry>> => {
        const tree = await git(['ls-tree', '-r', '-t', '-l', '-z', '--full-tree', ref]);
        if (tree.length && tree.at(-1) !== 0) throw new Error('Invalid Git tree');
        const entries = new Map<string, Entry>();
        let count = 0;
        for (const raw of tree.toString('binary').split('\0')) {
          if (!raw) continue;
          if (++count > 50000) throw new Error('Git tree limit exceeded');
          const data = Buffer.from(raw, 'binary');
          const tab = data.indexOf(9);
          const header = data.subarray(0, tab).toString('ascii').match(/^(\d{6}) (blob|tree|commit) ([a-f0-9]{40}|[a-f0-9]{64}) +(-|\d+)$/);
          if (tab < 0 || !header) throw new Error('Invalid Git tree');
          let name: string;
          try { name = decoder.decode(data.subarray(tab + 1)); } catch { throw new Error('Invalid Git path'); }
          if (!safePath(name) || entries.has(name)) throw new Error('Invalid Git path');
          const size = header[4] === '-' ? null : Number(header[4]);
          if (header[2] === 'blob' ? size === null || !Number.isSafeInteger(size) : size !== null) throw new Error('Invalid Git tree size');
          entries.set(name, { mode: header[1], type: header[2], oid: header[3], size });
        }
        return entries;
      };
      const entries = await readTreeEntries(input.sourceCommit);
      const yamlObject = async (entry: Entry | undefined): Promise<Record<string, unknown>> => {
        if (!entry || entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode)) throw new Error('Invalid manifest or workflow mode');
        const size = await text(['cat-file', '-s', entry.oid]);
        if (!/^\d+$/.test(size) || Number(size) > 1024 * 1024) throw new Error('Manifest or workflow too large');
        const bytes = await git(['cat-file', 'blob', entry.oid], 1024 * 1024 + 1);
        try {
          if (bytes.includes(0)) throw new Error();
          const text = decoder.decode(bytes);
          let value: unknown;
          try { value = parseYaml(text); } catch { throw new MalformedCandidateYamlError('Invalid manifest or workflow YAML'); }
          if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
          return value as Record<string, unknown>;
        } catch (error) {
          if (error instanceof MalformedCandidateYamlError) throw error;
          throw new Error('Invalid manifest or workflow YAML');
        }
      };
      const rootManifestDeclaresWorkflow = async (): Promise<boolean> => {
        const entry = entries.get('workflow.pack.yaml');
        if (!entry) return false;
        const manifest = await yamlObject(entry);
        if (!Array.isArray(manifest.workflows)) throw new Error('Invalid pack manifest workflows');
        return manifest.workflows.some(item => itemId(item) === input.workflowId);
      };
      const locate = async (allowMalformedSource = false): Promise<{ root: string; id: string; workflow: string; entry: Entry }> => {
        let found: { root: string; id: string; workflow: string; entry: Entry } | undefined;
        const files = await rootManifestDeclaresWorkflow()
          ? ['workflow.pack.yaml']
          : [...entries.keys()].filter(isCandidate);
        for (const file of files) {
          const entry = entries.get(file)!;
          const manifest = await yamlObject(entry);
          if (!Array.isArray(manifest.workflows)) throw new Error('Invalid pack manifest workflows');
          const matches = manifest.workflows.filter(item => itemId(item) === input.workflowId);
          if (!matches.length) continue;
          if (matches.length !== 1 || found || !idValid(manifest.id)) throw new Error('Pack manifest identity conflict');
          const root = file.slice(0, -'workflow.pack.yaml'.length);
          if (root && root.split('/').at(-2) !== manifest.id) throw new Error('Pack manifest location conflict');
          const workflow = workflowFile(matches[0]);
          if (workflow === 'workflow.pack.yaml') throw new Error('Workflow conflicts with manifest');
          try {
            const spec = await yamlObject(entries.get(root + workflow));
            if (spec.id !== input.workflowId) throw new Error('Workflow identity mismatch');
          } catch (error) {
            // Only unified save may replace syntactically malformed source YAML.
            // Mode, size, UTF-8, manifest/path and valid but wrong IDs still fail closed.
            if (!(allowMalformedSource && protectTreeAliases && workflowYamlSnapshot !== undefined
              && error instanceof MalformedCandidateYamlError)) throw error;
          }
          found = { root, id: manifest.id, workflow, entry: entries.get(root + workflow)! };
        }
        if (!found) throw new Error('Pack manifest not found');
        return found;
      };
      const rejectMixedRootLayout = async () => {
        if (entries.has('workflow.pack.yaml') && [...entries.keys()].some(name => name.endsWith('/workflow.pack.yaml'))) {
          // A root manifest declaring the selected workflow makes the packs/
          // subtree ordinary; nested manifests there are preserved resources.
          const nested = [...entries.keys()].filter(name => name !== 'workflow.pack.yaml'
            && name.endsWith('/workflow.pack.yaml'));
          if (await rootManifestDeclaresWorkflow() && nested.every(name => name.startsWith('packs/'))) return;
          throw new Error('Mixed root and nested Pack layout');
        }
      };
      await rejectMixedRootLayout();
      const sourcePack = await locate(true);
      let pack: { root: string; id: string } = sourcePack;
      if (protectTreeAliases) rejectProtectedTreeAliases(entries, sourcePack.root,
        await yamlObject(entries.get(`${sourcePack.root}workflow.pack.yaml`)));
      const rejectOverlappingPacks = () => {
        for (const name of entries.keys()) {
          if (name !== 'workflow.pack.yaml' && !name.endsWith('/workflow.pack.yaml')) continue;
          // The packs/ subtree under a selected root Pack is ordinary.
          if (pack.root === '' && name !== 'workflow.pack.yaml' && name.startsWith('packs/')) continue;
          const otherRoot = name.slice(0, -'workflow.pack.yaml'.length);
          if (otherRoot !== pack.root && (otherRoot.startsWith(pack.root) || pack.root.startsWith(otherRoot))) {
            throw new Error('Overlapping Pack layout');
          }
        }
      };
      rejectOverlappingPacks();
      for (const [name, entry] of entries) {
        if (name.startsWith(pack.root) && !(entry.type === 'tree' && entry.mode === '040000')
          && !(entry.type === 'blob' && ['100644', '100755'].includes(entry.mode))) throw new Error('Unsafe Pack entry');
      }
      // No filesystem checkout or link traversal: update an isolated index from immutable objects.
      await git(['read-tree', input.sourceCommit]);
      for (const change of changes) {
        const full = pack.root + change.path;
        const existing = entries.get(full);
        if (existing && (existing.type !== 'blob' || !['100644', '100755'].includes(existing.mode))) throw new Error('Unsafe target');
        if (change.operation === 'create' && existing) throw new Error('Create target already exists');
        if (change.operation === 'replace' && !existing) throw new Error('Replace target does not exist');
        for (const [name, entry] of entries) {
          if (full.startsWith(`${name}/`) && entry.type !== 'tree') throw new Error('Unsafe target parent');
        }
        if (change.operation === 'delete') {
          if (!existing) throw new Error('Delete target does not exist');
          await git(['update-index', '--force-remove', '--', full]);
          entries.delete(full);
        }
      }
      let index = 0;
      for (const change of changes) {
        if (change.operation === 'delete') continue;
        const full = pack.root + change.path;
        if ([...entries.keys()].some(name => name.startsWith(`${full}/`))) throw new Error('Target is a directory');
        const file = path.join(dir, '.git', `candidate-blob-${index++}`);
        await writeFile(file, change.bytes!, { mode: 0o600, flag: 'wx' });
        const oid = await text(['hash-object', '-w', '--no-filters', '--', file]);
        if (!oidPattern.test(oid)) throw new Error('Invalid blob id');
        await git(['update-index', '--add', '--cacheinfo', change.mode, oid, full]);
        entries.set(full, { mode: change.mode, type: 'blob', oid, size: change.bytes!.length });
      }
      if (workflowYamlSnapshot !== undefined) {
        // Resolve the final manifest after ordinary file changes. The selected
        // workflow has exactly one authority: the separate trusted argument.
        const manifest = await yamlObject(entries.get(`${pack.root}workflow.pack.yaml`));
        if (!Array.isArray(manifest.workflows)) throw new Error('Invalid pack manifest workflows');
        const matches = manifest.workflows.filter(item => itemId(item) === input.workflowId);
        if (matches.length !== 1) throw new Error('Pack manifest identity conflict');
        const selected = workflowFile(matches[0]);
        if (selected === 'workflow.pack.yaml' || seen.has(selected)) throw new Error('Conflicting selected workflow change');
        const full = pack.root + selected;
        const existing = entries.get(full);
        if (existing && (existing.type !== 'blob' || !['100644', '100755'].includes(existing.mode))) throw new Error('Unsafe target');
        // Compare Pack-relative paths before any Pack rename. A different
        // existing file is never implicitly replaced by the workflow projection.
        if (existing && selected !== sourcePack.workflow) throw new Error('Workflow target already exists');
        for (const [name, entry] of entries) {
          if ((full.startsWith(`${name}/`) && entry.type !== 'tree') || name.startsWith(`${full}/`)) throw new Error('Unsafe target');
        }
        const bytes = Buffer.from(workflowYamlSnapshot, 'utf8');
        const file = path.join(dir, '.git', `candidate-blob-${index++}`);
        await writeFile(file, bytes, { mode: 0o600, flag: 'wx' });
        const oid = await text(['hash-object', '-w', '--no-filters', '--', file]);
        if (!oidPattern.test(oid)) throw new Error('Invalid blob id');
        const mode = existing?.mode ?? sourcePack.entry.mode;
        await git(['update-index', '--add', '--cacheinfo', mode, oid, full]);
        entries.set(full, { mode, type: 'blob', oid, size: bytes.length });
      }
      if (input.preserveWorkflowContent && workflowYamlSnapshot === undefined) {
        // Check the old path as well as the path selected by the new manifest.
        // Otherwise a manifest move could leave a modified old YAML in Git, or
        // point to a different pre-existing blob without mentioning it in changes.
        const original = await git(['cat-file', 'blob', sourcePack.entry.oid], maxBlob + 1);
        const requireOriginalBytes = async (entry: Entry | undefined) => {
          if (!entry || entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode)) return;
          if (entry.size === null || entry.size > maxBlob
            || !original.equals(await git(['cat-file', 'blob', entry.oid], maxBlob + 1))) {
            throw new PackWorkflowContentChangedError();
          }
        };
        await requireOriginalBytes(entries.get(sourcePack.root + sourcePack.workflow));
        const manifest = await yamlObject(entries.get(`${pack.root}workflow.pack.yaml`));
        if (!Array.isArray(manifest.workflows)) throw new Error('Invalid pack manifest workflows');
        const matches = manifest.workflows.filter(item => itemId(item) === input.workflowId);
        if (matches.length !== 1) throw new Error('Pack manifest identity conflict');
        await requireOriginalBytes(entries.get(pack.root + workflowFile(matches[0])));
      }
      await rejectMixedRootLayout();
      rejectOverlappingPacks();
      // Full inventory reads every regular Pack blob, including unchanged legacy
      // files. Reject oversized final trees rather than publish unreadable drafts.
      let fileCount = 0;
      let totalBytes = 0;
      for (const [name, entry] of entries) {
        if (!name.startsWith(pack.root) || entry.type === 'tree') continue;
        if (entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode) || entry.size === null) throw new Error('Unsafe Pack entry');
        if (++fileCount > maxPackFiles) throw new Error('Pack file count limit exceeded');
        totalBytes += entry.size;
        if (entry.size > maxBlob || totalBytes > maxPackBytes) throw new Error('Pack size limit exceeded');
      }
      const renamedManifest = await yamlObject(entries.get(`${pack.root}workflow.pack.yaml`));
      if (!idValid(renamedManifest.id)) throw new Error('Invalid Pack identity');
      if (renamedManifest.id !== pack.id) {
        const newId = renamedManifest.id;
        // Pack IDs are versioned content. For nested layouts move the complete
        // selected tree in the isolated index, preserving untouched OIDs/modes.
        for (const [file, entry] of entries) {
          if (file !== `${pack.root}workflow.pack.yaml` && isCandidate(file)
            && (await yamlObject(entry)).id === newId) throw new Error('Pack rename destination identity is occupied');
        }
        if (pack.root) {
          const newRoot = `${pack.root.split('/').length === 3 ? 'packs/' : ''}${newId}/`;
          const newBase = newRoot.slice(0, -1);
          for (const [name, entry] of entries) {
            if (name === newBase || name.startsWith(newRoot)
              || (newBase.startsWith(`${name}/`) && entry.type !== 'tree')) throw new Error('Pack rename destination is occupied');
            if (name !== `${pack.root}workflow.pack.yaml` && (name === 'workflow.pack.yaml' || name.endsWith('/workflow.pack.yaml'))) {
              const otherRoot = name.slice(0, -'workflow.pack.yaml'.length);
              if (otherRoot.startsWith(newRoot) || newRoot.startsWith(otherRoot)) throw new Error('Pack rename destination overlaps another Pack');
            }
          }
          const moving = [...entries].filter(([name]) => name.startsWith(pack.root));
          for (const [name] of moving) {
            if (!safePath(newRoot + name.slice(pack.root.length))) throw new Error('Invalid Pack rename destination path');
          }
          for (const [name, entry] of moving) {
            if (entry.type === 'blob') await git(['update-index', '--force-remove', '--', name]);
          }
          for (const [name, entry] of moving) {
            const destination = newRoot + name.slice(pack.root.length);
            if (entry.type === 'blob') await git(['update-index', '--add', '--cacheinfo', entry.mode, entry.oid, destination]);
            entries.delete(name); entries.set(destination, entry);
          }
          entries.delete(pack.root.slice(0, -1));
          pack = { root: newRoot, id: newId };
        } else pack = { root: '', id: newId };
      }
      await rejectMixedRootLayout();
      rejectOverlappingPacks();
      const after = await locate();
      if (after.root !== pack.root || after.id !== pack.id) throw new Error('Pack manifest identity conflict');
      // Validate the complete resulting shared Pack, not just the edited workflow.
      // This runs after *all* changes so moving another workflow and updating its
      // manifest entry in the same candidate is possible, but dangling references
      // and mismatched, duplicate, or unsafe identities never reach saved.
      const manifest = await yamlObject(entries.get(`${pack.root}workflow.pack.yaml`));
      if (!Array.isArray(manifest.workflows) || manifest.workflows.length > 500) throw new Error('Invalid pack manifest workflows');
      const workflowIds = new Set<string>();
      const workflowPaths = new Set<string>();
      for (const item of manifest.workflows) {
        const id = itemId(item);
        if (!idValid(id) || workflowIds.has(id)) throw new Error('Invalid or duplicate workflow identity');
        const file = workflowFile(item);
        if (file === 'workflow.pack.yaml' || workflowPaths.has(file)) throw new Error('Invalid or duplicate workflow path');
        workflowIds.add(id);
        workflowPaths.add(file);
        const spec = await yamlObject(entries.get(pack.root + file));
        if (spec.id !== id) throw new Error('Workflow identity mismatch');
      }
      const treeId = await text(['write-tree']);
      if (protectTreeAliases) rejectProtectedTreeAliases(await readTreeEntries(treeId), pack.root, manifest);
      const message = await workflowSavedCommitMessage(dir, input.workflowId, treeId, { packId: after.id, packPath: after.root, workflowFile: after.workflow });
      const commit = await text(['commit-tree', treeId, '-p', input.sourceCommit, '-m', message]);
      if (!oidPattern.test(commit)) throw new Error('Invalid candidate commit');
      const metadata = await readPreparedPackMetadata(dir, input.workflowId, commit);
      const prepared: PreparedPackCandidate = Object.freeze({ ...metadata, workflowId: input.workflowId,
        branch: `wf/${input.workflowId}`, commit, sourceVersion: input.sourceVersion,
        sourceCommit: input.sourceCommit, expectedSavedHead: input.expectedSavedHead,
        bundleBase64: await createPreparedGitBundle(dir, [commit]) });
      // A zero-argument, one-shot capability. The callback must durably register
      // prepared before invoking it; returning/throwing without it never pushes.
      const result = await callback(prepared, () => {
        if (!callbackActive) return Promise.reject(new Error('Prepared candidate callback is no longer active'));
        if (pushPromise) return Promise.reject(new Error('Prepared candidate push already attempted'));
        attempted = { branch: prepared.branch, commit: prepared.commit };
        pushPromise = pushPreparedPackCandidate(dir, prepared);
        // Observe rejection immediately even if a caller forgets to await push.
        void pushPromise.catch(() => {});
        return pushPromise;
      });
      callbackActive = false;
      if (pushPromise) await pushPromise;
      return result;
    } catch (error) {
      // Once push started, even a callback failure can follow remote success.
      // Preserve the immutable reconciliation identity, not just a DB error.
      if (attempted) throw new PackCandidateOutcomeUnknownError(attempted.branch, attempted.commit);
      throw error;
    } finally {
      callbackActive = false;
      // Do not delete the clone under an in-flight push if a callback exits early.
      if (pushPromise) await Promise.allSettled([pushPromise]);
      try { await removeTempRepo(dir); } catch (error) {
        // Do not lose reconciliation information if cleanup fails after a push.
        if (attempted) throw new PackCandidateOutcomeUnknownError(attempted.branch, attempted.commit);
        throw error;
      }
    }
  });
}


function validatePreparedCandidate(prepared: PreparedPackCandidate) {
  if (!idValid(prepared.workflowId) || prepared.branch !== `wf/${prepared.workflowId}`
    || !oidPattern.test(prepared.commit) || (prepared.sourceCommit !== null && !oidPattern.test(prepared.sourceCommit))
    || (prepared.expectedSavedHead !== null && !oidPattern.test(prepared.expectedSavedHead))
    || [prepared.sourceCommit, prepared.expectedSavedHead].some(oid => oid !== null && oid.length !== prepared.commit.length)
    || !['current', 'draft', 'initial'].includes(prepared.sourceVersion)
    || (prepared.sourceVersion !== 'initial' && prepared.sourceCommit === null)
    || (prepared.sourceVersion === 'initial' && (prepared.expectedSavedHead !== null || prepared.sourceCommit !== null))) {
    throw new Error('Invalid prepared candidate');
  }
}

/** Push an already prepared, caller-owned journal entry. After restart restore its
 * bundle first. Authorization/provenance and unknown-outcome reconciliation belong
 * to the caller. Only wf is read/written/leased; other branches and tags are not consulted. */
export async function pushPreparedPackCandidate(repoDir: string, input: PreparedPackCandidate): Promise<SavePackCandidateResult> {
  const prepared = Object.freeze({ ...input });
  validatePreparedCandidate(prepared);
  rejectCurrentOverDifferentSaved(prepared.sourceVersion, prepared.sourceCommit, prepared.expectedSavedHead);
  const { text } = workflowSavedGitRunner(repoDir);
  if (await text(['rev-parse', '--verify', `${prepared.commit}^{commit}`]) !== prepared.commit
    || await text(['show', '-s', '--format=%P', prepared.commit]) !== (prepared.sourceCommit ?? '')) throw new Error('Invalid prepared candidate commit');
  await verifyPreparedPackMetadata(repoDir, prepared.workflowId, prepared.commit, prepared);
  const state = await resolveSavedWorkflowRef(repoDir, prepared.workflowId);
  if (state.wfHead !== prepared.expectedSavedHead
    || (prepared.sourceVersion !== 'initial' && state.commit !== prepared.sourceCommit)) throw new PackCandidateConflictError();
  const saved = `refs/heads/${prepared.branch}`;
  try {
    await text(['push', '--no-follow-tags', `--force-with-lease=${saved}:${prepared.expectedSavedHead ?? ''}`, 'origin', `${prepared.commit}:${saved}`]);

  } catch {
    // An error or a later ref read cannot prove that the server did not apply it.
    throw new PackCandidateOutcomeUnknownError(prepared.branch, prepared.commit);
  }
  return { branch: prepared.branch, commit: prepared.commit };
}

/** @deprecated Non-journaled compatibility wrapper. Not crash-safe across DB writes;
 * production callers must use withPreparedPackCandidate and persist before push. */
export async function savePackCandidate(input: SavePackCandidateInput): Promise<SavePackCandidateResult> {
  return withPreparedPackCandidate(input, (_prepared, push) => push());
}


/** Replay a durable save, never regenerate its commit. Only exact desired or the
 * original expected head is acceptable; a newer wf head is never replaced. */
export async function resumePreparedPackCandidate(input: PreparedPackCandidate, config: ClawFlowsGitConfig): Promise<SavePackCandidateResult> {
  const prepared = Object.freeze({ ...input });
  validatePreparedCandidate(prepared);
  return withGitOperation({}, async () => {
    const dir = await createTempRepo(); let attempted = false;
    try {
      await initClawflowsReadRepo(dir, config, prepared.commit.length === 64 ? 'sha256' : 'sha1');
      const head = (await resolveSavedWorkflowRef(dir, prepared.workflowId)).wfHead;
      if (head === prepared.commit) {
        // The exact objects are already remote: no bundle/re-push is needed, but
        // the projection must still agree with the journal's Git identity.
        const { text } = workflowSavedGitRunner(dir);
        if (await text(['show', '-s', '--format=%P', prepared.commit]) !== (prepared.sourceCommit ?? '')) {
          throw new Error('Invalid prepared candidate commit provenance');
        }
        await verifyPreparedPackMetadata(dir, prepared.workflowId, prepared.commit, prepared);
        return { branch: prepared.branch, commit: prepared.commit };
      }
      if (head !== prepared.expectedSavedHead) throw new PackCandidateConflictError();
      rejectCurrentOverDifferentSaved(prepared.sourceVersion, prepared.sourceCommit, prepared.expectedSavedHead);
      await restorePreparedGitBundle(dir, prepared.bundleBase64);
      attempted = true;
      return await pushPreparedPackCandidate(dir, prepared);
    } finally {
      try { await removeTempRepo(dir); }
      catch (error) { if (attempted) throw new PackCandidateOutcomeUnknownError(prepared.branch, prepared.commit); throw error; }
    }
  });
}
