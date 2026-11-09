/** Compare the exact repository trees: publishing replaces the whole wf ref, not just one Pack. */
import { TextDecoder } from 'node:util';
import { preparedGitRunner } from './pack-git-prepared.js';
import { PackOperationError } from './pack-operation-service.js';

type TreeEntry = { mode: string; type: 'blob' | 'tree' | 'commit'; oid: string };

async function treeEntries(dir: string, commit: string): Promise<Map<string, TreeEntry>> {
  const data = await preparedGitRunner(dir).bytes(['ls-tree', '-r', '-t', '-z', '--full-tree', commit]);
  if (data.length && data[data.length - 1] !== 0) throw new Error('Invalid deployment tree');
  const entries = new Map<string, TreeEntry>();
  const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
  let start = 0;
  while (start < data.length) {
    const end = data.indexOf(0, start);
    const tab = data.indexOf(9, start);
    if (end < 0 || tab < start || tab >= end) throw new Error('Invalid deployment tree');
    const header = data.subarray(start, tab).toString('ascii').match(/^(\d{6}) (blob|tree|commit) ([a-f0-9]{40}|[a-f0-9]{64})$/);
    const name = decoder.decode(data.subarray(tab + 1, end));
    if (!header || !name || entries.has(name)) throw new Error('Invalid deployment tree');
    entries.set(name, { mode: header[1], type: header[2] as TreeEntry['type'], oid: header[3] });
    start = end + 1;
  }
  return entries;
}

/** The caller supplies the active history tag's commit, never the saved wf draft.
 * Preserve all existing paths and modes. For a nested selected Pack, also preserve
 * outside leaf object IDs (including symlinks and gitlinks). A root Pack has no
 * outside directory, so this cannot protect content belonging to other concerns. */
export async function assertDeploymentTreePreserved(dir: string, active: string, candidate: string, requestId: string, packPath: string): Promise<void> {
  if (packPath && (!packPath.endsWith('/') || packPath.slice(0, -1).split('/').some(part => !part || part === '.' || part === '..')
    || /[\\\0]/.test(packPath))) throw new Error('Invalid Pack directory');
  const [previous, next] = await Promise.all([treeEntries(dir, active), treeEntries(dir, candidate)]);
  const paths = [...previous].filter(([name, entry]) => {
    const current = next.get(name);
    return !current || current.mode !== entry.mode || current.type !== entry.type
      || (packPath !== '' && !name.startsWith(packPath) && entry.type !== 'tree' && current.oid !== entry.oid);
  }).map(([name]) => name);
  if (paths.length) throw Object.assign(new PackOperationError(409, 'DEPLOY_TREE_CONFLICT', requestId), { paths });
}
