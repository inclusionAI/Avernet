import { executeGit } from '@avernet/clawweb-shared/server/services/git-execution';
import { TextDecoder } from 'node:util';
import { parse as parseYaml } from 'yaml';

export type PackScriptSnapshot = {
  workflowId: string;
  packId: string;
  baseCommit: string;
  baseRef: string;
  files: Array<{ path: string; content: string; mode: '100644' | '100755' }>;
};

// Bound both Git's output and the materialized contents, independently of repo size.
const FILE_LIMIT = 1024 * 1024;
const TOTAL_LIMIT = 8 * 1024 * 1024;
const TREE_LIMIT = 8 * 1024 * 1024;
const MAX_FILES = 500;
const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
type Entry = { mode: string; type: string; oid: string; name: string };

function validId(value: string): boolean {
  return /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(value)
    && !value.includes('..') && !value.endsWith('.') && !value.endsWith('.lock');
}

/** Read an authorized caller's already-cloned repository. Lazy fetching is disabled where Git supports it. */
export async function readPackScriptSnapshot(repoDir: string, workflowId: string, packId: string): Promise<PackScriptSnapshot> {
  if (!validId(workflowId) || !validId(packId)) throw new Error('Invalid workflowId or packId');
  const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')));
  Object.assign(env, { GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: '/dev/null', GIT_NO_REPLACE_OBJECTS: '1', GIT_NO_LAZY_FETCH: '1', GIT_TERMINAL_PROMPT: '0' });
  const git = (args: string[], maxBuffer = TREE_LIMIT) => executeGit(args, { cwd: repoDir, env, maxBuffer });
  const text = async (args: string[]) => (await git(args)).toString('ascii').trim();
  const resolve = async (shortRef: string): Promise<string | undefined> => {
    const full = `refs/remotes/${shortRef}`;
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
  const savedRef = `origin/saved/${workflowId}`;
  const [wf, saved] = await Promise.all([resolve(wfRef), resolve(savedRef)]);
  if (!wf && !saved) throw new Error('Workflow snapshot not found');
  let baseCommit = wf ?? saved!;
  let baseRef = wf ? wfRef : savedRef;
  if (wf && saved && wf !== saved) {
    const ancestor = async (a: string, b: string) => {
      try { await git(['merge-base', '--is-ancestor', a, b]); return true; }
      catch (error) { if ((error as { status?: number }).status === 1) return false; throw error; }
    };
    if (await ancestor(wf, saved)) { baseCommit = saved; baseRef = savedRef; }
    else if (!await ancestor(saved, wf)) throw new Error('Workflow ref conflict: histories diverged');
  }
  const list = async (tree: string, recursive = false): Promise<Entry[]> => {
    const output = await git(['ls-tree', '-z', ...(recursive ? ['-r', '-t'] : []), tree]);
    if (!output.length) return [];
    if (output[output.length - 1] !== 0) throw new Error('Invalid Git tree');
    return output.subarray(0, -1).toString('binary').split('\0').map(record => {
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
  const root = await list(baseCommit);
  const rootManifest = root.find(e => e.name === 'workflow.pack.yaml');
  // A valid root Pack can contain an ordinary file with the same name as a
  // candidate directory. Never treat symlinks or gitlinks as directories.
  const candidateDirectory = async (entry: Entry | undefined) => {
    if (rootManifest && entry?.type === 'blob' && ['100644', '100755'].includes(entry.mode)) return undefined;
    return directory(entry);
  };
  const direct = await candidateDirectory(root.find(e => e.name === packId));
  const packs = await candidateDirectory(root.find(e => e.name === 'packs'));
  const nested = packs && await candidateDirectory(packs.find(e => e.name === packId));
  const manifest = (entries: Entry[] | undefined) => entries?.find(e => e.name === 'workflow.pack.yaml');
  const candidates = [root, direct, nested]
    .flatMap(entries => {
      const candidate = manifest(entries);
      return candidate && entries ? [{ manifest: candidate, entries }] : [];
    });
  if (candidates.length > 1) throw new Error('Pack manifest conflict');
  if (!candidates.length) throw new Error('Pack manifest not found');
  const { manifest: selectedManifest, entries } = candidates[0];
  if (selectedManifest.type !== 'blob' || !['100644', '100755'].includes(selectedManifest.mode)) throw new Error('Invalid pack manifest mode');
  const readYamlObject = async (entry: Entry, label: string): Promise<Record<string, unknown>> => {
    if (entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode)) throw new Error(`Invalid ${label} mode`);
    const sizeText = await text(['cat-file', '-s', entry.oid]);
    if (!/^\d+$/.test(sizeText) || Number(sizeText) > FILE_LIMIT) throw new Error(`Invalid ${label} size`);
    const bytes = await git(['cat-file', 'blob', entry.oid], FILE_LIMIT + 1);
    try {
      if (bytes.includes(0)) throw new Error('binary');
      const value: unknown = parseYaml(decoder.decode(bytes));
      if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('not an object');
      return value as Record<string, unknown>;
    } catch { throw new Error(`Invalid ${label}`); }
  };
  const manifestValue = await readYamlObject(selectedManifest, 'pack manifest');
  if (manifestValue.id !== packId || !Array.isArray(manifestValue.workflows)) throw new Error('Pack manifest identity mismatch');
  // Workflow ACL does not authorize every script of a multi-workflow Pack.
  if (manifestValue.workflows.length !== 1) throw new Error('Shared pack requires separate authorization');
  const matching = manifestValue.workflows.filter(item => typeof item === 'string'
    ? item === workflowId : Boolean(item && typeof item === 'object' && !Array.isArray(item) && (item as Record<string, unknown>).id === workflowId));
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
  const workflowValue = await readYamlObject(workflowEntry, 'workflow file');
  if (workflowValue.id !== workflowId) throw new Error('Workflow identity mismatch');
  const scripts = entries.find(e => e.name === 'scripts');
  if (!scripts) return { workflowId, packId, baseCommit, baseRef, files: [] };
  if (scripts.type !== 'tree' || scripts.mode !== '040000') throw new Error('Invalid scripts directory mode');
  const scriptEntries = await list(scripts.oid, true);
  if (scriptEntries.filter(entry => entry.type !== 'tree').length > MAX_FILES) throw new Error('Script file count limit exceeded');
  const files: PackScriptSnapshot['files'] = [];
  let total = 0;
  for (const entry of scriptEntries) {
    if (entry.name.split('/').some(part => !part || part === '.' || part === '..' || part.includes('\\'))) throw new Error('Invalid script path');
    if (entry.type === 'tree' && entry.mode === '040000') continue;
    if (entry.type !== 'blob' || !['100644', '100755'].includes(entry.mode)) throw new Error('Invalid script file mode');
    if (!/\.(?:py|js|ts|sh|bash)$/.test(entry.name)) throw new Error('Unsupported script extension');
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
  return { workflowId, packId, baseCommit, baseRef, files };
}
