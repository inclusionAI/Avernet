/** Exact candidate promotion. The caller owns authorization, saved provenance and durable registration. */
import { parse as parseYaml } from 'yaml';
import { randomUUID } from 'node:crypto';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { withGitOperation } from '@avernet/clawweb-shared/server/services/git-execution';
import { cloneClawflows, createTempRepo, removeTempRepo, type ClawFlowsGitConfig, type GitAuthor } from './clawflows-git-client.js';
import { createPreparedGitBundle, restorePreparedGitBundle, readPreparedPackMetadata, verifyPreparedPackMetadata, preparedGitRunner,
  readPreparedRemoteRefs, gitOidPattern, validPackGitId, type PreparedPackMetadata } from './pack-git-prepared.js';

export type DeployResult = Readonly<{ branch: string; tagName: string; commit: string; deployNumber: number }>;
export type PreparedDeployment = PreparedPackMetadata & DeployResult & Readonly<{
  workflowId: string;
  candidateCommit: string;
  /** Empty string means wf must not exist. */
  previousCommit: string;
  releaseCommit: string;
  tagObject: string;
  version: number;
  bundleBase64: string;
}>;

export class DeploymentConflictError extends Error {
  constructor() { super('Deployment refs changed; reconcile before retrying'); this.name = 'DeploymentConflictError'; }
}
export class DeploymentOutcomeUnknownError extends Error {
  readonly branch: string;
  readonly tagName: string;
  readonly commit: string;
  readonly tagObject: string;
  constructor(prepared: PreparedDeployment) {
    super(`Git deployment outcome unknown; reconcile ${prepared.branch}, ${prepared.tagName}, and commit ${prepared.releaseCommit}`);
    this.name = 'DeploymentOutcomeUnknownError';
    this.branch = prepared.branch; this.tagName = prepared.tagName;
    this.commit = prepared.releaseCommit; this.tagObject = prepared.tagObject;
  }
}

/** Reads the explicit immutable commit even when saved was advanced/deleted.
 * A matching commit is not evidence of a previous save; the main service checks its journal. */
export async function inspectDeployCandidate(repoDir: string, workflowId: string, packId: string, candidateCommit: string) {
  if (!validPackGitId(packId)) throw new Error('Invalid deploy candidate');
  const metadata = await readPreparedPackMetadata(repoDir, workflowId, candidateCommit);
  if (metadata.packId !== packId) throw new Error('Candidate pack identity mismatch');
  const refs = await readPreparedRemoteRefs(repoDir, [`refs/heads/wf/${workflowId}`]);
  return { ...metadata, raw: parseYaml(metadata.content) as Record<string, unknown>,
    previousCommit: refs.get(`refs/heads/wf/${workflowId}`) ?? '' };
}

function validateIdentity(workflowId: string, candidateCommit: string, previousCommit: string, deployNumber: number, version: number) {
  if (!validPackGitId(workflowId) || !gitOidPattern.test(candidateCommit)
    || typeof previousCommit !== 'string' || (previousCommit && !gitOidPattern.test(previousCommit))
    || !Number.isSafeInteger(deployNumber) || deployNumber < 1 || !Number.isSafeInteger(version) || version < 1) {
    throw new Error('Invalid deployment identity');
  }
}
function validatePrepared(prepared: PreparedDeployment) {
  validateIdentity(prepared.workflowId, prepared.candidateCommit, prepared.previousCommit, prepared.deployNumber, prepared.version);
  if (prepared.branch !== `wf/${prepared.workflowId}` || prepared.tagName !== `deploy/${prepared.workflowId}/#${prepared.deployNumber}`
    || !gitOidPattern.test(prepared.releaseCommit) || prepared.commit !== prepared.releaseCommit
    || !gitOidPattern.test(prepared.tagObject)) throw new Error('Invalid prepared deployment');
}
const result = (prepared: PreparedDeployment): DeployResult => ({ branch: prepared.branch, tagName: prepared.tagName,
  commit: prepared.releaseCommit, deployNumber: prepared.deployNumber });

/** Creates local objects only. Persist the returned JSON before calling pushPreparedDeployment.
 * A unique release commit ensures every distinct deployment changes wf, including redeploys.
 * Its tree is exactly S; neither merging nor re-reading saved is permitted. */
export async function prepareDeployCandidate(repoDir: string, workflowId: string, candidateCommit: string,
  previousCommit: string, deployNumber: number, version: number, author: GitAuthor): Promise<PreparedDeployment> {
  validateIdentity(workflowId, candidateCommit, previousCommit, deployNumber, version);
  const metadata = await readPreparedPackMetadata(repoDir, workflowId, candidateCommit);
  const { text } = preparedGitRunner(repoDir);
  const branch = `wf/${workflowId}`; const tagName = `deploy/${workflowId}/#${deployNumber}`;
  const tree = await text(['rev-parse', `${candidateCommit}^{tree}`]);
  if (!gitOidPattern.test(tree)) throw new Error('Invalid candidate tree');
  const identity = ['-c', `user.name=${author.name}`, '-c', `user.email=${author.email}`];
  const releaseCommit = await text([...identity, 'commit-tree', tree, '-p', candidateCommit,
    '-m', `Deploy ${workflowId} #${deployNumber} (${randomUUID()})`]);
  if (!gitOidPattern.test(releaseCommit)) throw new Error('Invalid deploy commit');
  // Construct the annotated object without installing/replacing any local tag.
  const tagger = await text([...identity, 'var', 'GIT_COMMITTER_IDENT']);
  const dir = await mkdtemp(path.join(tmpdir(), 'cw-prepared-tag-'));
  let tagObject: string;
  try {
    const file = path.join(dir, 'tag');
    await writeFile(file, `object ${releaseCommit}\ntype commit\ntag ${tagName}\ntagger ${tagger}\n\n`
      + `version=${version}\naction=deploy\nworkflowIds=${workflowId}\ncandidateCommit=${candidateCommit}\n`, { mode: 0o600, flag: 'wx' });
    tagObject = await text(['hash-object', '-t', 'tag', '-w', '--no-filters', '--', file]);
    if (!gitOidPattern.test(tagObject)) throw new Error('Invalid deploy tag object');
  } finally { await rm(dir, { recursive: true, force: true }); }
  return Object.freeze({ ...metadata, workflowId, branch, candidateCommit, previousCommit, releaseCommit,
    commit: releaseCommit, tagName, tagObject, deployNumber, version,
    bundleBase64: await createPreparedGitBundle(repoDir, [releaseCommit, tagObject]) });
}

/** Atomic wf CAS + immutable tag creation, using exact object IDs, not local ref names.
 * No saved lease/check: later saves do not supersede an already confirmed S. */
export async function pushPreparedDeployment(repoDir: string, input: PreparedDeployment): Promise<DeployResult> {
  const prepared = Object.freeze({ ...input });
  validatePrepared(prepared);
  const { text } = preparedGitRunner(repoDir);
  if (await text(['rev-parse', '--verify', `${prepared.releaseCommit}^{commit}`]) !== prepared.releaseCommit
    || await text(['rev-parse', `${prepared.releaseCommit}^{tree}`]) !== await text(['rev-parse', `${prepared.candidateCommit}^{tree}`])
    || await text(['show', '-s', '--format=%P', prepared.releaseCommit]) !== prepared.candidateCommit
    || await text(['cat-file', '-t', prepared.tagObject]) !== 'tag'
    || await text(['rev-parse', `${prepared.tagObject}^{commit}`]) !== prepared.releaseCommit) throw new Error('Invalid prepared deployment objects');
  await verifyPreparedPackMetadata(repoDir, prepared.workflowId, prepared.candidateCommit, prepared);
  const annotation = await text(['cat-file', 'tag', prepared.tagObject]);
  if (!annotation.includes(`\ntag ${prepared.tagName}\n`)
    || !annotation.endsWith(`version=${prepared.version}\naction=deploy\nworkflowIds=${prepared.workflowId}\ncandidateCommit=${prepared.candidateCommit}`)) {
    throw new Error('Invalid prepared deployment annotation');
  }
  try {
    await text(['push', '--atomic', `--force-with-lease=refs/heads/${prepared.branch}:${prepared.previousCommit}`,
      // An empty tag lease permits creation only; no existing tag may be replaced.
      `--force-with-lease=refs/tags/${prepared.tagName}:`, 'origin',
      `${prepared.releaseCommit}:refs/heads/${prepared.branch}`, `${prepared.tagObject}:refs/tags/${prepared.tagName}`]);
  } catch { throw new DeploymentOutcomeUnknownError(prepared); }
  return result(prepared);
}

/** Resume the same durable request. Never allocates a new deploy number/commit/tag.
 * Exact desired pair => success without push; expected wf + absent tag => retry
 * these same objects; any mixed/concurrent state => conflict without overwriting. */
export async function resumePreparedDeployment(input: PreparedDeployment, config: ClawFlowsGitConfig): Promise<DeployResult> {
  const prepared = Object.freeze({ ...input });
  validatePrepared(prepared);
  return withGitOperation({}, async () => {
    const dir = await createTempRepo(); let attempted = false;
    try {
      await cloneClawflows(dir, config);
      const branchRef = `refs/heads/${prepared.branch}`; const tagRef = `refs/tags/${prepared.tagName}`;
      const refs = await readPreparedRemoteRefs(dir, [branchRef, tagRef]);
      const branch = refs.get(branchRef) ?? ''; const tag = refs.get(tagRef);
      if (branch === prepared.releaseCommit && tag === prepared.tagObject) {
        await verifyPreparedPackMetadata(dir, prepared.workflowId, prepared.candidateCommit, prepared);
        return result(prepared);
      }
      if (branch !== prepared.previousCommit || tag !== null) throw new DeploymentConflictError();
      await restorePreparedGitBundle(dir, prepared.bundleBase64);
      attempted = true;
      return await pushPreparedDeployment(dir, prepared);
    } finally {
      try { await removeTempRepo(dir); }
      catch (error) { if (attempted) throw new DeploymentOutcomeUnknownError(prepared); throw error; }
    }
  });
}

/** @deprecated Non-journaled compatibility wrapper. Not crash-safe across DB writes;
 * production callers must use prepare + persist + push. */
export async function pushDeployCandidate(repoDir: string, workflowId: string, candidateCommit: string,
  previousCommit: string, deployNumber: number, version: number, author: GitAuthor): Promise<DeployResult> {
  return pushPreparedDeployment(repoDir,
    await prepareDeployCandidate(repoDir, workflowId, candidateCommit, previousCommit, deployNumber, version, author));
}
