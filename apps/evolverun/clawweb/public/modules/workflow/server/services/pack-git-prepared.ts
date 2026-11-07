/** Git-only prepared-object storage and immutable Pack metadata reads.
 * Bundles belong in the caller's durable, access-controlled journal, never in logs.
 * They contain complete object history, including other Packs or sensitive historical
 * blobs. Transport config/credentials are not packaged; treat the history as confidential.
 */
import { randomUUID } from 'node:crypto';
import { rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { TextDecoder } from 'node:util';
import { parse as parseYaml } from 'yaml';
import { createConfiguredTemporaryDirectory } from '@avernet/clawweb-shared/server/env';
import { executeGit } from '@avernet/clawweb-shared/server/services/git-execution';

/** Decoded bundle limit; base64 persistence needs up to 4/3 this space. No incremental fallback. */
export const MAX_PREPARED_BUNDLE_BYTES = 32 * 1024 * 1024;
export const gitOidPattern = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
export const validPackGitId = (value: string) => typeof value === 'string'
  && /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(value)
  && !value.includes('..') && !value.endsWith('.') && !value.endsWith('.lock');
const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
const safePath = (value: unknown): value is string => typeof value === 'string' && value.length > 0
  && Buffer.byteLength(value) <= 4096 && !/[\\\u0000-\u001f\u007f]/.test(value)
  && value.split('/').every(part => part && part !== '.' && part !== '..'
    && part.toLowerCase() !== '.git' && !/^[A-Za-z]:/.test(part));

export function preparedGitRunner(repoDir: string) {
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')));
  Object.assign(env, { GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_NO_REPLACE_OBJECTS: '1',
    GIT_NO_LAZY_FETCH: '1', GIT_TERMINAL_PROMPT: '0' });
  const bytes = (args: string[], maxBuffer = 16 * 1024 * 1024) => executeGit(args, { cwd: repoDir, env, maxBuffer });
  const text = async (args: string[]) => (await bytes(args)).toString('utf8').trim();
  return { bytes, text };
}

export type PreparedPackMetadata = Readonly<{
  packId: string;
  /** Repository-relative directory, empty for a root Pack, otherwise with trailing slash. */
  packPath: string;
  /** Pack-relative path declared by the selected manifest. */
  workflowFile: string;
  manifestContent: string;
  content: string;
}>;

/** Resolve solely from the supplied commit, never from saved/current or the worktree.
 * This proves content identity, NOT that the candidate was saved/authorized previously.
 */
export async function readPreparedPackMetadata(repoDir: string, workflowId: string, commit: string): Promise<PreparedPackMetadata> {
  if (!validPackGitId(workflowId) || !gitOidPattern.test(commit)) throw new Error('Invalid deploy candidate');
  const { bytes, text } = preparedGitRunner(repoDir);
  if (await text(['rev-parse', '--verify', `${commit}^{commit}`]) !== commit) throw new Error('Invalid candidate commit');
  const tree = await bytes(['ls-tree', '-r', '-z', '--full-tree', commit]);
  if (tree.length && tree.at(-1) !== 0) throw new Error('Invalid Git tree');
  const entries = new Map<string, { mode: string; type: string; oid: string }>();
  for (const record of tree.toString('binary').split('\0')) {
    if (!record) continue;
    const raw = Buffer.from(record, 'binary'); const tab = raw.indexOf(9);
    const header = raw.subarray(0, tab).toString('ascii').match(/^(\d{6}) (blob|commit) ([a-f0-9]{40}|[a-f0-9]{64})$/);
    const name = decoder.decode(raw.subarray(tab + 1));
    if (tab < 0 || !header || !safePath(name) || entries.has(name) || entries.size >= 50_000) throw new Error('Invalid Git tree');
    entries.set(name, { mode: header[1], type: header[2], oid: header[3] });
  }
  const blob = async (file: string) => {
    const entry = entries.get(file);
    if (!entry || entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode)) throw new Error('Invalid candidate file mode');
    const size = await text(['cat-file', '-s', entry.oid]);
    if (!/^\d+$/.test(size) || Number(size) > 1024 * 1024) throw new Error('Candidate file too large');
    const data = await bytes(['cat-file', 'blob', entry.oid], 1024 * 1024 + 1);
    if (data.includes(0)) throw new Error('Invalid candidate text');
    return decoder.decode(data);
  };
  const yamlObject = (content: string): Record<string, unknown> => {
    const raw: unknown = parseYaml(content);
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('Invalid candidate YAML');
    return raw as Record<string, unknown>;
  };
  const manifests = [...entries.keys()].filter(name => name === 'workflow.pack.yaml' || name.endsWith('/workflow.pack.yaml'));
  let candidateManifests = manifests;
  if (manifests.includes('workflow.pack.yaml')) {
    const manifest = yamlObject(await blob('workflow.pack.yaml'));
    const rootDeclaresWorkflow = Array.isArray(manifest.workflows)
      && manifest.workflows.some(entry => (typeof entry === 'string' ? entry : entry?.id) === workflowId);
    const outsidePacksManifest = manifests.some(name => name !== 'workflow.pack.yaml' && !name.startsWith('packs/'));
    if (rootDeclaresWorkflow && !outsidePacksManifest) {
      // A root manifest that declares the workflow is authoritative. Files under
      // packs/, including a historical nested manifest, are ordinary resources.
      candidateManifests = ['workflow.pack.yaml'];
    } else if (manifests.length > 1) {
      throw new Error('Mixed root and nested Pack layout');
    }
  }
  let found: PreparedPackMetadata | undefined;
  for (const name of candidateManifests) {
    const parts = name.split('/');
    if (!(parts.length <= 2 || (parts.length === 3 && parts[0] === 'packs'))) continue;
    const manifestContent = await blob(name); const manifest = yamlObject(manifestContent);
    if (!Array.isArray(manifest.workflows)) throw new Error('Invalid candidate manifest workflows');
    const matches = manifest.workflows.filter(entry => (typeof entry === 'string' ? entry : entry?.id) === workflowId);
    if (!matches.length) continue;
    if (found || matches.length !== 1) throw new Error('Candidate manifest workflow mismatch');
    if (typeof manifest.id !== 'string' || !validPackGitId(manifest.id)
      || (parts.length > 1 && parts.at(-2) !== manifest.id)) throw new Error('Candidate pack identity mismatch');
    const packPath = name.slice(0, -'workflow.pack.yaml'.length);
    if (candidateManifests.some(other => {
      const root = other.slice(0, -'workflow.pack.yaml'.length);
      return other !== name && (root.startsWith(packPath) || packPath.startsWith(root));
    })) throw new Error('Overlapping Pack layout');
    for (const [file, entry] of entries) {
      if (file.startsWith(packPath) && (entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode))) {
        throw new Error('Unsafe Pack entry');
      }
    }
    const selected = matches[0];
    const workflowFile: unknown = typeof selected === 'string' ? `workflows/${selected}.yaml` : selected.file;
    if (!safePath(workflowFile) || workflowFile === 'workflow.pack.yaml') throw new Error('Invalid candidate workflow path');
    const content = await blob(packPath + workflowFile);
    if (yamlObject(content).id !== workflowId) throw new Error('Workflow identity mismatch');
    found = { packId: manifest.id, packPath, workflowFile, manifestContent, content };
  }
  if (!found) throw new Error('Candidate manifest workflow mismatch');
  return Object.freeze(found);
}

/** Fail closed if a journal payload was mixed with another candidate. These are
 * server-owned records, never client-supplied proof of authorization/provenance. */
export async function verifyPreparedPackMetadata(repoDir: string, workflowId: string, commit: string, prepared: PreparedPackMetadata): Promise<void> {
  const actual = await readPreparedPackMetadata(repoDir, workflowId, commit);
  for (const key of ['packId', 'packPath', 'workflowFile', 'manifestContent', 'content'] as const) {
    if (actual[key] !== prepared[key]) throw new Error('Prepared Pack metadata does not match immutable objects');
  }
}

/** Temporary refs are local only. No negative revisions: all ancestors and trees are included. */
export async function createPreparedGitBundle(repoDir: string, objects: string[]): Promise<string> {
  if (!objects.length || objects.length > 16 || objects.some(oid => !gitOidPattern.test(oid))) throw new Error('Invalid prepared objects');
  const { bytes, text } = preparedGitRunner(repoDir);
  if (await text(['rev-parse', '--is-shallow-repository']) !== 'false') {
    throw new Error('Cannot create a self-contained prepared bundle from a shallow repository');
  }
  const prefix = `refs/pack-prepared/${randomUUID()}`;
  const refs: string[] = [];
  try {
    for (const [index, oid] of objects.entries()) {
      const ref = `${prefix}/${index}`;
      await text(['update-ref', ref, oid, '']); refs.push(ref);
    }
    // stdout is bounded by executeGit, which kills the child on overflow.
    const bundle = await bytes(['bundle', 'create', '-', ...refs], MAX_PREPARED_BUNDLE_BYTES);
    return bundle.toString('base64');
  } finally {
    for (const ref of refs) await text(['update-ref', '-d', ref]);
  }
}

/** Import objects only; never updates local/remote branches or tags. Safe to repeat.
 * The caller still must reconcile an unknown push before deciding to retry a write.
 */
export async function restorePreparedGitBundle(repoDir: string, bundleBase64: string): Promise<void> {
  if (typeof bundleBase64 !== 'string' || !bundleBase64.length
    || bundleBase64.length > Math.ceil(MAX_PREPARED_BUNDLE_BYTES / 3) * 4) throw new Error('Invalid prepared bundle size');
  const data = Buffer.from(bundleBase64, 'base64');
  if (data.length > MAX_PREPARED_BUNDLE_BYTES || data.toString('base64') !== bundleBase64) throw new Error('Invalid prepared bundle encoding');
  const headerEnd = data.indexOf('\n\n');
  const header = data.subarray(0, Math.max(0, headerEnd)).toString('ascii');
  if (headerEnd < 0 || headerEnd > 16 * 1024 || !/^# v[23] git bundle\n/.test(header)
    || /^-/m.test(header) || /^@filter=/m.test(header)) throw new Error('Prepared bundle must be self-contained');
  const dir = await createConfiguredTemporaryDirectory('cw-prepared-bundle');
  try {
    const file = path.join(dir, 'objects.bundle');
    await writeFile(file, data, { mode: 0o600, flag: 'wx' });
    const { text } = preparedGitRunner(repoDir);
    await text(['bundle', 'verify', file]);
    await text(['bundle', 'unbundle', file]);
  } finally { await rm(dir, { recursive: true, force: true }); }
}

/** One advertised remote snapshot; --refs excludes peeled tag lines. Missing means null. */
export async function readPreparedRemoteRefs(repoDir: string, refs: string[]): Promise<Map<string, string | null>> {
  if (!refs.length || refs.some(ref => !/^refs\/(heads|tags)\/[A-Za-z0-9_/#.-]+$/.test(ref))) throw new Error('Invalid prepared refs');
  const heads = new Map<string, string | null>(refs.map(ref => [ref, null]));
  const { text } = preparedGitRunner(repoDir);
  const output = await text(['ls-remote', '--refs', 'origin', ...refs]);
  if (!output) return heads;
  for (const line of output.split('\n')) {
    const match = line.match(/^([a-f0-9]{40}|[a-f0-9]{64})\t([^\n]+)$/);
    if (!match || !heads.has(match[2]) || heads.get(match[2]) !== null) throw new Error('Invalid remote ref');
    heads.set(match[2], match[1]);
  }
  return heads;
}
