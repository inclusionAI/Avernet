import { gitOidPattern, preparedGitRunner, validPackGitId } from './pack-git-prepared.js';
import { PackOperationError } from './pack-operation-service.js';

export type ActiveDeploymentGitRecord = Readonly<{ tag_name: string | null }>;
export type ActiveDeploymentGit = Readonly<{ tagName: string; tagObject: string; commit: string }>;

/** A missing/invalid baseline fails closed; a changed observation reports DEPLOY_BASELINE_STALE. */
export async function resolveActiveDeploymentGit(repoDir: string, workflowId: string, active: ActiveDeploymentGitRecord | null): Promise<ActiveDeploymentGit | null> {
  if (!validPackGitId(workflowId)) throw new PackOperationError(409, 'DEPLOY_BASELINE_MISSING');
  if (active === null) return null;
  const name = active.tag_name;
  const prefix = `deploy/${workflowId}/#`;
  if (typeof name !== 'string' || !name.startsWith(prefix) || !/^[1-9][0-9]*$/.test(name.slice(prefix.length))) {
    throw new PackOperationError(409, 'DEPLOY_BASELINE_MISSING');
  }
  const ref = `refs/tags/${name}`;
  const { text } = preparedGitRunner(repoDir);
  const observe = async () => {
    const output = await text(['ls-remote', 'origin', ref, `${ref}^{}`]);
    const found = new Map<string, string>();
    for (const line of output ? output.split('\n') : []) {
      const match = /^([a-f0-9]{40}|[a-f0-9]{64})\t(.+)$/.exec(line);
      if (!match || (match[2] !== ref && match[2] !== `${ref}^{}`) || found.has(match[2])) throw new Error('Invalid tag advertisement');
      found.set(match[2], match[1]);
    }
    const tagObject = found.get(ref), peeled = found.get(`${ref}^{}`);
    if (!tagObject || !gitOidPattern.test(tagObject) || (peeled && !gitOidPattern.test(peeled))) throw new Error('Missing tag');
    return { tagObject, peeled };
  };
  try {
    const before = await observe();
    await text(['fetch', '--no-tags', '--no-write-fetch-head', '--refmap=', 'origin', ref]);
    const type = await text(['cat-file', '-t', before.tagObject]);
    if (type !== 'tag' && type !== 'commit') throw new Error('Invalid tag object');
    const commit = await text(['rev-parse', '--verify', `${before.tagObject}^{commit}`]);
    if (!gitOidPattern.test(commit) || await text(['cat-file', '-t', commit]) !== 'commit'
      || (type === 'tag' ? before.peeled !== commit : !!before.peeled || commit !== before.tagObject)) throw new Error('Invalid peeled tag');
    const after = await observe();
    if (before.tagObject !== after.tagObject || before.peeled !== after.peeled) throw new PackOperationError(409, 'DEPLOY_BASELINE_STALE');
    return Object.freeze({ tagName: name, tagObject: before.tagObject, commit });
  } catch (error) {
    if (error instanceof PackOperationError) throw error;
    throw Object.assign(new PackOperationError(409, 'DEPLOY_BASELINE_MISSING'), { cause: error });
  }
}

/** Reader-facing resolver. The caller validates its authorized DB row and active token. */
export async function resolveActiveDeploymentCommit(repoDir: string, workflowId: string, tagName: string | null): Promise<string | null> {
  const active = await resolveActiveDeploymentGit(repoDir, workflowId, tagName === null ? null : { tag_name: tagName });
  return active?.commit ?? null;
}
