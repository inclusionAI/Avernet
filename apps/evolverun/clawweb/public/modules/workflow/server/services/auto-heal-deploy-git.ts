/** Exact candidate promotion. The caller owns authorization, candidate selection and outcome handling. */
import { parse as parseYaml } from 'yaml';
import { rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { withGitOperation } from '@avernet/clawweb-shared/server/services/git-execution';
import { createConfiguredTemporaryDirectory } from '@avernet/clawweb-shared/server/env';
import { initClawflowsReadRepo, createTempRepo, removeTempRepo, type ClawFlowsGitConfig, type GitAuthor } from './clawflows-git-client.js';
import { createPreparedGitBundle, restorePreparedGitBundle, readPreparedPackMetadata, verifyPreparedPackMetadata, preparedGitRunner,
  readPreparedRemoteRefs, gitOidPattern, validPackGitId, type PreparedPackMetadata } from './pack-git-prepared.js';

import { resolveActiveDeploymentGit, type ActiveDeploymentGitRecord } from './active-deployment-git.js';

export type DeployResult = Readonly<{ branch: string; tagName: string; commit: string; deployNumber: number }>;
export type PreparedDeployment = PreparedPackMetadata & DeployResult & Readonly<{
  workflowId: string;
  candidateCommit: string;
  /** Informational production baseline, not a branch lease. */
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

/** Reads the explicit immutable commit even when the wf draft advanced or disappeared.
 * A matching commit is not evidence of a previous save; the caller must verify its own preconditions. */
export async function inspectDeployCandidate(repoDir: string, workflowId: string, packId: string, candidateCommit: string, active: ActiveDeploymentGitRecord | null = null) {
  if (!validPackGitId(packId)) throw new Error('Invalid deploy candidate');
  const metadata = await readPreparedPackMetadata(repoDir, workflowId, candidateCommit);
  if (metadata.packId !== packId) throw new Error('Candidate pack identity mismatch');
  const baseline = await resolveActiveDeploymentGit(repoDir, workflowId, active);
  return { ...metadata, raw: parseYaml(metadata.content) as Record<string, unknown>,
    previousCommit: baseline?.commit ?? '' };
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
    || prepared.releaseCommit !== prepared.candidateCommit || prepared.commit !== prepared.candidateCommit
    || !gitOidPattern.test(prepared.tagObject)) throw new Error('Invalid prepared deployment');
}
const result = (prepared: PreparedDeployment): DeployResult => ({ branch: prepared.branch, tagName: prepared.tagName,
  commit: prepared.releaseCommit, deployNumber: prepared.deployNumber });

/** Creates local objects only. The returned bundle can contain confidential Git history.
 * A caller without durable operation state must not treat it as a crash recovery receipt.
 * Its tree is exactly S; neither merging nor re-reading the wf draft is permitted. */
export async function prepareDeployCandidate(repoDir: string, workflowId: string, candidateCommit: string,
  previousCommit: string, deployNumber: number, version: number, author: GitAuthor): Promise<PreparedDeployment> {
  validateIdentity(workflowId, candidateCommit, previousCommit, deployNumber, version);
  const metadata = await readPreparedPackMetadata(repoDir, workflowId, candidateCommit);
  const { text } = preparedGitRunner(repoDir);
  const branch = `wf/${workflowId}`; const tagName = `deploy/${workflowId}/#${deployNumber}`;
  const identity = ['-c', `user.name=${author.name}`, '-c', `user.email=${author.email}`];
  const releaseCommit = candidateCommit;
  // Construct the annotated object without installing/replacing any local tag.
  const tagger = await text([...identity, 'var', 'GIT_COMMITTER_IDENT']);
  const dir = await createConfiguredTemporaryDirectory('cw-prepared-tag');
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

/** Check immutable objects on both publication and already-published recovery paths. */
async function assertPreparedDeploymentObjects(repoDir: string, prepared: PreparedDeployment): Promise<void> {
  const { text } = preparedGitRunner(repoDir);
  if (await text(['rev-parse', '--verify', `${prepared.releaseCommit}^{commit}`]) !== prepared.releaseCommit
    || await text(['cat-file', '-t', prepared.tagObject]) !== 'tag'
    || await text(['rev-parse', `${prepared.tagObject}^{commit}`]) !== prepared.releaseCommit) throw new Error('Invalid prepared deployment objects');
  await verifyPreparedPackMetadata(repoDir, prepared.workflowId, prepared.candidateCommit, prepared);
  const annotation = await text(['cat-file', 'tag', prepared.tagObject]);
  if (!annotation.includes(`\ntag ${prepared.tagName}\n`)
    || !annotation.endsWith(`version=${prepared.version}\naction=deploy\nworkflowIds=${prepared.workflowId}\ncandidateCommit=${prepared.candidateCommit}`)) {
    throw new Error('Invalid prepared deployment annotation');
  }
}

/** Immutable tag creation, using exact object IDs, not local ref names.
 * No wf lease/check: later saves do not supersede an already confirmed S. */
export async function pushPreparedDeployment(repoDir: string, input: PreparedDeployment): Promise<DeployResult> {
  const prepared = Object.freeze({ ...input });
  validatePrepared(prepared);
  await assertPreparedDeploymentObjects(repoDir, prepared);
  const { text } = preparedGitRunner(repoDir);
  try {
    await text(['push', `--force-with-lease=refs/tags/${prepared.tagName}:`, 'origin',
      `${prepared.tagObject}:refs/tags/${prepared.tagName}`]);
  } catch { throw new DeploymentOutcomeUnknownError(prepared); }
  return result(prepared);
}

/** Legacy recovery helper for callers that already possess a trusted durable request.
 * The new no-journal flow must not call this after an uncertain push.
 * Never allocates a new deploy number/commit/tag.
 * Exact tag => success without push; absent tag => retry
 * these same objects; any mixed/concurrent state => conflict without overwriting. */
export async function resumePreparedDeployment(input: PreparedDeployment, config: ClawFlowsGitConfig): Promise<DeployResult> {
  const prepared = Object.freeze({ ...input });
  validatePrepared(prepared);
  return withGitOperation({}, async () => {
    const dir = await createTempRepo(); let attempted = false;
    try {
      await initClawflowsReadRepo(dir, config, prepared.candidateCommit.length === 64 ? 'sha256' : 'sha1');
      const tagRef = `refs/tags/${prepared.tagName}`;
      const refs = await readPreparedRemoteRefs(dir, [tagRef]);
      const tag = refs.get(tagRef);
      if (tag === prepared.tagObject) {
        const actual = await resolveActiveDeploymentGit(dir, prepared.workflowId, { tag_name: prepared.tagName });
        if (actual?.tagObject !== prepared.tagObject || actual.commit !== prepared.candidateCommit) throw new DeploymentConflictError();
        await assertPreparedDeploymentObjects(dir, prepared);
        return result(prepared);
      }
      if (tag !== null) throw new DeploymentConflictError();
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
 * callers must handle partial or unknown outcomes without blindly retrying. */
export async function pushDeployCandidate(repoDir: string, workflowId: string, candidateCommit: string,
  previousCommit: string, deployNumber: number, version: number, author: GitAuthor): Promise<DeployResult> {
  return pushPreparedDeployment(repoDir,
    await prepareDeployCandidate(repoDir, workflowId, candidateCommit, previousCommit, deployNumber, version, author));
}
