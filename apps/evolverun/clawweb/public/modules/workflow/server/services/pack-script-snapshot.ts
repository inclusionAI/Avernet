import { executeGit } from '@avernet/clawweb-shared/server/services/git-execution';
import { TextDecoder } from 'node:util';
import { parse as parseYaml } from 'yaml';

export type PackScriptVersion = 'current' | 'draft';

export type PackScriptSnapshot = {
  workflowId: string;
  packId: string;
  baseCommit: string;
  baseRef: string;
  files: Array<{ path: string; content: string; mode: '100644' | '100755' }>;
  skippedFiles?: { count: number; paths: string[]; truncated: boolean };
  /** Full version inventory, only on explicit request. Paths are Pack-relative. */
  packPath?: string;
  draftHead?: string | null;
  /** True only when the active DB history content differs from the active Git workflow. */
  activeHistoryMismatch?: true;
  versionFiles?: Array<{ path: string; contentBase64: string; mode: '100644' | '100755' }>;
};

// Bound both Git's output and the materialized contents, independently of repo size.
const FILE_LIMIT = 1024 * 1024;
const TOTAL_LIMIT = 8 * 1024 * 1024;
const TREE_LIMIT = 8 * 1024 * 1024;
const MAX_FILES = 500;
const MAX_TREE_ENTRIES = 50_000;
const MAX_CANDIDATE_DIRECTORIES = 4096;
const DISCOVERY_BATCH_SIZE = 128;
const DISCOVERY_BATCH_BYTES = 16 * 1024;
const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
type Entry = { mode: string; type: string; oid: string; name: string };
class MalformedSnapshotYamlError extends Error {}

function validId(value: string): boolean {
  return /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(value)
    && !value.includes('..') && !value.endsWith('.') && !value.endsWith('.lock');
}

/** Read an authorized caller's already-cloned repository. Lazy fetching is disabled where Git supports it. */
export async function readPackScriptSnapshot(repoDir: string, workflowId: string, packId: string, version?: PackScriptVersion, options?: { includeVersionFiles?: boolean; activeTag?: string; allowMalformedDraftWorkflow?: boolean }): Promise<PackScriptSnapshot> {
  if (version !== undefined && version !== 'current' && version !== 'draft') throw new Error('Invalid snapshot version');
  if (!validId(workflowId) || !validId(packId)) throw new Error('Invalid workflowId or packId');
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')));
  Object.assign(env, { GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_NO_REPLACE_OBJECTS: '1', GIT_NO_LAZY_FETCH: '1', GIT_TERMINAL_PROMPT: '0' });
  const git = (args: string[], maxBuffer = TREE_LIMIT) => executeGit(args, { cwd: repoDir, env, maxBuffer });
  const text = async (args: string[]) => (await git(args)).toString('ascii').trim();
  const resolve = async (shortRef: string): Promise<string | undefined> => {
    const full = shortRef.startsWith('refs/') ? shortRef : `refs/remotes/${shortRef}`;
    try { await git(['show-ref', '--verify', '--quiet', full]); }
    catch (error) {
      if ((error as { status?: number }).status === 1) return undefined;
      throw error;
    }
    const oid = await text(['rev-parse', '--verify', `${full}^{commit}`]);
    if (!/^(?:[0-9a-f]{40}|[0-9a-f]{64})$/.test(oid)) throw new Error('Invalid Git commit');
    return oid;
  };
  const wfRef = `origin/wf/${workflowId}`;
  let baseCommit: string;
  let baseRef: string;
  if (version === 'current') {
    const tag = options?.activeTag;
    const prefix = `deploy/${workflowId}/#`;
    if (!tag?.startsWith(prefix) || !/^[1-9][0-9]*$/.test(tag.slice(prefix.length))) throw new Error('Workflow snapshot not found');
    const selected = await resolve('refs/pack-version/active');
    if (!selected) throw new Error('Workflow snapshot not found');
    baseRef = `refs/tags/${tag}`;
    baseCommit = selected;
  } else {
    baseRef = wfRef;
    const selected = await resolve(wfRef);
    if (!selected) throw new Error('Workflow snapshot not found');
    baseCommit = selected;
  }
  let treeBytes = 0;
  let treeCalls = 0;
  let treeEntries = 0;
  const list = async (tree: string, recursive = false, paths?: string[]): Promise<Entry[]> => {
    if (++treeCalls > 512) throw new Error('Git tree discovery limit exceeded');
    const output = await git(['ls-tree', '-z', ...(recursive ? ['-r', '-t'] : []),
      ...(paths ? ['--full-tree'] : []), tree, ...(paths ? ['--', ...paths.map(path => `:(literal)${path}`)] : [])]);
    treeBytes += output.length;
    if (treeBytes > 16 * 1024 * 1024) throw new Error('Git tree discovery limit exceeded');
    if (!output.length) return [];
    if (output[output.length - 1] !== 0) throw new Error('Invalid Git tree');
    const records = output.subarray(0, -1).toString('binary').split('\0');
    treeEntries += records.length;
    if (treeEntries > MAX_TREE_ENTRIES) throw new Error('Git tree discovery limit exceeded');
    return records.map(record => {
      const raw = Buffer.from(record, 'binary');
      const tab = raw.indexOf(9);
      if (tab < 0) throw new Error('Invalid Git tree');
      const header = raw.subarray(0, tab).toString('ascii').match(/^(\d{6}) (blob|tree|commit) ([0-9a-f]{40}|[0-9a-f]{64})$/);
      if (!header) throw new Error('Invalid Git tree');
      let name: string;
      try { name = decoder.decode(raw.subarray(tab + 1)); } catch { throw new Error('Invalid UTF-8 Git path'); }
      return { mode: header[1], type: header[2], oid: header[3], name };
    });
  };
  const directory = async (entry: Entry | undefined): Promise<Entry[] | undefined> => {
    if (!entry) return undefined;
    if (entry.mode !== '040000' || entry.type !== 'tree') throw new Error('Invalid pack directory mode');
    return list(entry.oid);
  };
  let yamlBytes = 0;
  const readYamlObject = async (entry: Entry, label: string): Promise<Record<string, unknown>> => {
    if (entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode)) throw new Error(`Invalid ${label} mode`);
    const sizeText = await text(['cat-file', '-s', entry.oid]);
    const size = Number(sizeText);
    if (!/^\d+$/.test(sizeText) || !Number.isSafeInteger(size) || size > FILE_LIMIT) throw new Error(`Invalid ${label} size`);
    yamlBytes += size;
    if (yamlBytes > TOTAL_LIMIT) throw new Error('Pack manifest discovery limit exceeded');
    const bytes = await git(['cat-file', 'blob', entry.oid], FILE_LIMIT + 1);
    try {
      if (bytes.includes(0)) throw new Error('binary');
      const text = decoder.decode(bytes);
      let value: unknown;
      try { value = parseYaml(text); } catch { throw new MalformedSnapshotYamlError(`Invalid ${label}`); }
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('not an object');
      return value as Record<string, unknown>;
    } catch (error) {
      if (error instanceof MalformedSnapshotYamlError) throw error;
      throw new Error(`Invalid ${label}`);
    }
  };
  const root = await list(baseCommit);
  const rootManifest = root.find(e => e.name === 'workflow.pack.yaml');
  const manifest = (entries: Entry[] | undefined) => entries?.find(e => e.name === 'workflow.pack.yaml');
  type Candidate = { manifest: Entry; entries?: Entry[]; directory?: Entry; directoryName?: string; packPath: string };
  const candidates: Candidate[] = [];
  const addCandidate = (entries: Entry[] | undefined, directoryName?: string, packPath = '') => {
    const value = manifest(entries);
    if (value && entries) candidates.push({ manifest: value, entries, directoryName, packPath });
  };
  addCandidate(root);
  if (version) {
    // Probe exact manifest paths, in bounded batches, without recursive traversal.
    // Parents come only from actual tree entries in this immutable commit.
    const directories = new Map<string, { entry: Entry; name: string; path: string }>();
    const addDirectory = (path: string, entry: Entry) => {
      if (entry.name === '.' || entry.name === '..' || entry.name.includes('/')
        || directories.has(`${path}/workflow.pack.yaml`) || directories.size >= MAX_CANDIDATE_DIRECTORIES
        || Buffer.byteLength(path, 'utf8') > DISCOVERY_BATCH_BYTES / 2) {
        throw new Error('Git tree discovery limit exceeded');
      }
      directories.set(`${path}/workflow.pack.yaml`, { entry, name: entry.name, path });
    };
    for (const entry of root) {
      if (entry.name === 'packs') {
        if (entry.type === 'blob' && ['100644', '100755'].includes(entry.mode)) continue;
        const packs = await directory(entry);
        addCandidate(packs, 'packs', 'packs');
        for (const child of packs ?? []) {
          if (child.type === 'tree' && child.mode === '040000') addDirectory(`packs/${child.name}`, child);
        }
      } else if (entry.type === 'tree' && entry.mode === '040000') {
        addDirectory(entry.name, entry);
      }
    }
    let batch: string[] = [];
    let batchBytes = 0;
    const probe = async () => {
      if (!batch.length) return;
      const expected = new Set(batch);
      for (const found of await list(baseCommit, false, batch)) {
        const parent = directories.get(found.name);
        if (!parent || !expected.delete(found.name)) throw new Error('Invalid Git tree');
        candidates.push({ manifest: { ...found, name: 'workflow.pack.yaml' }, directory: parent.entry, directoryName: parent.name, packPath: parent.path });
      }
      batch = [];
      batchBytes = 0;
    };
    for (const path of directories.keys()) {
      const bytes = Buffer.byteLength(path, 'utf8') + 16;
      if (batch.length && (batch.length >= DISCOVERY_BATCH_SIZE || batchBytes + bytes > DISCOVERY_BATCH_BYTES)) await probe();
      batch.push(path);
      batchBytes += bytes;
    }
    await probe();
  } else {
    // Preserve the original DB-derived Pack lookup for unversioned callers.
    const candidateDirectory = async (entry: Entry | undefined) => {
      if (rootManifest && entry?.type === 'blob' && ['100644', '100755'].includes(entry.mode)) return undefined;
      return directory(entry);
    };
    addCandidate(await candidateDirectory(root.find(e => e.name === packId)), packId, packId);
    const packs = await candidateDirectory(root.find(e => e.name === 'packs'));
    addCandidate(packs && await candidateDirectory(packs.find(e => e.name === packId)), packId, `packs/${packId}`);
    if (candidates.length > 1) throw new Error('Pack manifest conflict');
  }
  type Match = { candidate: Candidate; value: Record<string, unknown>; matching: unknown[] };
  const inspect = async (candidate: Candidate): Promise<Match> => {
    const value = await readYamlObject(candidate.manifest, 'pack manifest');
    if (!Array.isArray(value.workflows)) throw new Error('Pack manifest identity mismatch');
    const matching = value.workflows.filter(item => typeof item === 'string'
      ? item === workflowId : Boolean(item && typeof item === 'object' && !Array.isArray(item) && (item as Record<string, unknown>).id === workflowId));
    return { candidate, value, matching };
  };
  // A repository-root manifest that declares the requested workflow is
  // authoritative. Candidates under packs/ are ordinary resources and are not
  // parsed; a matching manifest outside packs/ is still ambiguous.
  const rootCandidate = candidates.find(candidate => candidate.packPath === '' && !candidate.directoryName);
  const rootMatch = rootCandidate ? await inspect(rootCandidate) : undefined;
  const rootActive = Boolean(rootMatch?.matching.length);
  const selectedCandidates = rootActive
    ? candidates.filter(candidate => candidate !== rootCandidate
      && candidate.packPath !== 'packs' && !candidate.packPath.startsWith('packs/'))
    : candidates.filter(candidate => candidate !== rootCandidate);
  const inspected: Match[] = [];
  if (!version && rootMatch) inspected.push(rootMatch);
  if (rootActive) inspected.push(rootMatch!);
  for (const candidate of selectedCandidates) {
    const match = await inspect(candidate);
    if (!version || match.matching.length) inspected.push(match);
  }
  const outsidePacksDuplicate = inspected.some(match =>
    match !== rootMatch && match.matching.length > 0 && match.candidate.packPath !== 'packs' && !match.candidate.packPath.startsWith('packs/'));
  const matches = rootActive ? (outsidePacksDuplicate ? inspected : [rootMatch!]) : inspected;
  if (matches.length > 1) throw new Error('Pack manifest conflict');
  if (!matches.length) throw new Error('Pack manifest not found');
  const { candidate, value: manifestValue, matching } = matches[0];
  const entries = candidate.entries ?? await directory(candidate.directory);
  if (!entries) throw new Error('Invalid pack directory mode');
  const verifiedManifest = manifest(entries);
  if (!verifiedManifest || verifiedManifest.oid !== candidate.manifest.oid
    || verifiedManifest.mode !== candidate.manifest.mode || verifiedManifest.type !== candidate.manifest.type) throw new Error('Invalid Git tree');
  if (typeof manifestValue.id !== 'string' || !validId(manifestValue.id)
    || (version ? candidate.directoryName !== undefined && manifestValue.id !== candidate.directoryName : manifestValue.id !== packId)) {
    throw new Error('Pack manifest identity mismatch');
  }
  const actualPackId = manifestValue.id;
  if (matching.length !== 1) throw new Error('Pack manifest workflow mismatch');
  const matched = matching[0];
  const workflowPath = typeof matched === 'string' ? `workflows/${matched}.yaml` : (matched as Record<string, unknown>).file;
  if (typeof workflowPath !== 'string' || !workflowPath || workflowPath.includes('\\') || workflowPath.includes('\0')
    || workflowPath.split('/').some(part => !part || part === '.' || part === '..') || /^[A-Za-z]:/.test(workflowPath)) {
    throw new Error('Invalid workflow path');
  }
  const parts = workflowPath.split('/');
  let workflowEntries = entries;
  for (const part of parts.slice(0, -1)) {
    const children = await directory(workflowEntries.find(entry => entry.name === part));
    if (!children) throw new Error('Workflow file not found');
    workflowEntries = children;
  }
  const workflowEntry = workflowEntries.find(entry => entry.name === parts[parts.length - 1]);
  if (!workflowEntry) throw new Error('Workflow file not found');
  try {
    const workflowValue = await readYamlObject(workflowEntry, 'workflow file');
    if (workflowValue.id !== workflowId) throw new Error('Workflow identity mismatch');
  } catch (error) {
    // Full draft inventory is informational; the manifest and regular file path
    // remain authoritative only for a later, separately authorized unified save.
    if (!(version === 'draft' && options?.includeVersionFiles && options.allowMalformedDraftWorkflow
      && error instanceof MalformedSnapshotYamlError)) throw error;
  }
  // An inventory is complete or unavailable: never silently omit file modes or
  // binary resources. The legacy scripts-only response remains unchanged.
  if (options?.includeVersionFiles) {
    if (!version) throw new Error('Version required for full inventory');
    const packTree = candidate.directory?.oid ?? (candidate.packPath === 'packs'
      ? root.find(entry => entry.name === 'packs')?.oid : baseCommit);
    if (!packTree) throw new Error('Invalid pack directory');
    const versionEntries = await list(packTree, true);
    // A root Pack can contain another Pack manifest as an ordinary resource
    // under packs/. Only reject nested manifests outside the ordinary packs/
    // directory, including deeper overlapping layouts under scripts/.
    if (versionEntries.some(entry => entry.name.endsWith('/workflow.pack.yaml') && !entry.name.startsWith('packs/'))) {
      throw new Error('Mixed Pack layout');
    }
    const fileEntries = versionEntries.filter(entry => entry.type !== 'tree');
    if (fileEntries.length > MAX_FILES) throw new Error('Version file count limit exceeded');
    const versionFiles: NonNullable<PackScriptSnapshot['versionFiles']> = [];
    let totalSize = 0;
    for (const entry of fileEntries) {
      if (entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode)
        || /^[A-Za-z]:/.test(entry.name)
        || entry.name.split('/').some(part => !part || part === '.' || part === '..' || part.includes('\\') || part.toLowerCase() === '.git')
        || /[\x00-\x1f\x7f]/.test(entry.name)) throw new Error('Unsupported version file');
      const rawSize = await text(['cat-file', '-s', entry.oid]);
      const size = Number(rawSize);
      if (!/^\d+$/.test(rawSize) || !Number.isSafeInteger(size) || size > FILE_LIMIT || totalSize + size > TOTAL_LIMIT) throw new Error('Version size limit exceeded');
      totalSize += size;
      const data = await git(['cat-file', 'blob', entry.oid], FILE_LIMIT + 1);
      if (data.length !== size) throw new Error('Invalid version file size');
      versionFiles.push({ path: entry.name, contentBase64: data.toString('base64'), mode: entry.mode as '100644' | '100755' });
    }
    return { workflowId, packId: actualPackId, baseCommit, baseRef, files: [],
      packPath: candidate.packPath, draftHead: (await resolve(wfRef)) ?? null, versionFiles };
  }
  const skippedFiles: NonNullable<PackScriptSnapshot['skippedFiles']> = { count: 0, paths: [], truncated: false };
  const scripts = entries.find(e => e.name === 'scripts');
  if (!scripts) return { workflowId, packId: actualPackId, baseCommit, baseRef, files: [], skippedFiles };
  if (scripts.type !== 'tree' || scripts.mode !== '040000') throw new Error('Invalid scripts directory mode');
  const scriptEntries = await list(scripts.oid, true);
  // Do not treat a nested Pack under scripts/ as part of the parent Pack.
  if (scriptEntries.some(entry => entry.name === 'workflow.pack.yaml' || entry.name.endsWith('/workflow.pack.yaml'))) throw new Error('Mixed Pack layout');
  if (scriptEntries.filter(entry => entry.type !== 'tree').length > MAX_FILES) throw new Error('Script file count limit exceeded');
  const files: PackScriptSnapshot['files'] = [];
  let total = 0;
  for (const entry of scriptEntries) {
    if (entry.name.split('/').some(part => !part || part === '.' || part === '..' || part.includes('\\'))) throw new Error('Invalid script path');
    if (entry.type === 'tree' && entry.mode === '040000') continue;
    if (entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode)) throw new Error('Invalid script file mode');
    // Only regular blobs may be ignored; never follow symlinks or gitlinks.
    if (!/\.(?:py|js|ts|sh|bash)$/.test(entry.name)) {
      if (!version) throw new Error('Unsupported script extension');
      skippedFiles.count++;
      const skippedPath = `scripts/${entry.name}`;
      if (skippedFiles.paths.length < 100 && Buffer.byteLength(skippedPath, 'utf8') <= 256
        && !/[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/u.test(skippedPath)) {
        skippedFiles.paths.push(skippedPath);
      } else skippedFiles.truncated = true;
      continue;
    }
    const sizeText = await text(['cat-file', '-s', entry.oid]);
    if (!/^\d+$/.test(sizeText)) throw new Error('Invalid script size');
    const size = Number(sizeText);
    if (!Number.isSafeInteger(size) || size > FILE_LIMIT || total + size > TOTAL_LIMIT) throw new Error('Script size limit exceeded');
    total += size;
    const bytes = await git(['cat-file', 'blob', entry.oid], FILE_LIMIT + 1);
    if (bytes.length !== size || bytes.includes(0)) throw new Error('Invalid binary script');
    let content: string;
    try { content = decoder.decode(bytes); } catch { throw new Error('Invalid UTF-8 script'); }
    files.push({ path: `scripts/${entry.name}`, content, mode: entry.mode as '100644' | '100755' });
  }
  return { workflowId, packId: actualPackId, baseCommit, baseRef, files, skippedFiles };
}
