import { rm } from 'node:fs/promises';
import path from 'node:path';
import { executeGit } from '@avernet/clawweb-shared/server/services/git-execution';
import { initClawflowsReadRepo, type ClawFlowsGitConfig } from './clawflows-git-client.js';
import { packVersionGitEnvironment } from './pack-version-git-env.js';

function validateWorkflowId(workflowId: string): void {
  if (!/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(workflowId)
    || /[\r\n]/.test(workflowId) || workflowId.includes('..') || workflowId.endsWith('.') || workflowId.endsWith('.lock')) {
    throw new Error('Invalid workflowId');
  }
}

export async function preparePackVersionRepo(repoDir: string, config: ClawFlowsGitConfig, workflowId: string): Promise<void> {
  validateWorkflowId(workflowId);
  await initClawflowsReadRepo(repoDir, config);
}

/** Fetch workflow tips and, for active recovery only, the bound deployment tag. */
export async function fetchPackVersionRefs(repoDir: string, config: ClawFlowsGitConfig, workflowId: string, activeTag?: string, _allowMissingActiveBranch = false, _allowDifferentActiveBranch = false): Promise<(() => Promise<void>) & { activeCommit?: string }> {
  validateWorkflowId(workflowId);
  if (activeTag !== undefined && (!activeTag.startsWith(`deploy/${workflowId}/#`) || !/^[1-9][0-9]*$/.test(activeTag.slice(`deploy/${workflowId}/#`.length)))) throw new Error('Invalid active tag');
  const refs = [`refs/heads/wf/${workflowId}`];
  const tagRef = activeTag ? `refs/tags/${activeTag}` : undefined;
  const requestedRefs = tagRef ? [...refs, tagRef, `${tagRef}^{}`] : refs;
  const env = packVersionGitEnvironment();
  const git = async (args: string[]) => (await executeGit(args, { cwd: repoDir, env, timeout: 120_000, maxBuffer: 16 * 1024 })).toString('utf8').trim();
  const discover = async (): Promise<Map<string, string>> => {
    const output = await git(['ls-remote', ...(tagRef ? [] : ['--refs']), '--', 'origin', ...requestedRefs]);
    const found = new Map<string, string>();
    if (output) for (const line of output.split('\n')) {
      const match = /^(?:([0-9a-f]{40})|([0-9a-f]{64}))\t([^\s]+)$/.exec(line);
      if (!match || !requestedRefs.includes(match[3]) || found.has(match[3])) throw new Error('Invalid remote refs');
      found.set(match[3], match[1] || match[2]);
    }
    return found;
  };
  const observed = await discover();
  const changed = (after: Map<string, string>) => requestedRefs.some(ref => observed.get(ref) !== after.get(ref));
  // Compare the fetched tag object and commit with both remote observations.
  // wf is a mutable saved draft, not the production baseline; only the bound tag identifies production.
  const activeCommit = tagRef ? observed.get(`${tagRef}^{}`) ?? observed.get(tagRef) : undefined;
  if (tagRef && (!observed.get(tagRef) || !activeCommit)) {
    throw Object.assign(new Error('Active deployment ref mismatch'), { code: 'PACK_REFS_CHANGED' });
  }
  const refConflict = () => Object.assign(new Error('Workflow refs changed during read'), { code: 'PACK_REFS_CHANGED' });
  // ls-remote is hash-format agnostic, while fetch requires a matching local
  // object database. Recreate only this newly initialized temporary repo.
  const lengths = new Set([...observed.values()].map(oid => oid.length));
  if (lengths.size > 1) throw new Error('Invalid remote refs');
  if (lengths.has(64)) {
    await rm(path.join(repoDir, '.git'), { recursive: true, force: true });
    await initClawflowsReadRepo(repoDir, config, 'sha256');
  }
  const refspecs = refs.filter(ref => observed.has(ref)).map(ref => `+${ref}:refs/remotes/origin/${ref.slice('refs/heads/'.length)}`);
  if (tagRef) refspecs.push(`+${tagRef}:refs/pack-version/active`);
  if (refspecs.length) {
    try { await git(['fetch', '--quiet', '--no-tags', '--depth=1', '--', 'origin', ...refspecs]); }
    catch (error) {
      // Distinguish a changed ref from a transport failure without retrying
      // the fetch or falling back to an unbounded history transfer.
      if ((error as { code?: unknown } | null)?.code !== 'GIT_COMMAND_FAILED') throw error;
      if (changed(await discover())) throw refConflict();
      throw error;
    }
    if (tagRef && (await git(['rev-parse', '--verify', 'refs/pack-version/active']) !== observed.get(tagRef)
      || await git(['rev-parse', '--verify', 'refs/pack-version/active^{commit}']) !== activeCommit)) throw refConflict();
    for (const ref of refs.filter(value => observed.has(value))) {
      const actual = await git(['rev-parse', '--verify', `refs/remotes/origin/${ref.slice('refs/heads/'.length)}^{commit}`]);
      if (actual !== observed.get(ref)) throw refConflict();
    }
  }
  return Object.assign(async () => {
    const after = await discover();
    if (changed(after)) throw refConflict();
  }, { activeCommit });
}
