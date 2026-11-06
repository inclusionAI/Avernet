/**
 * Read/preparation primitive only: a matching saved ref and matching DB document
 * do NOT prove that a save completed or that Git and DB belong to one operation.
 * The trusted caller must independently validate candidate provenance,
 * authorization and existing deployment guards before any publication. This
 * function does not push refs, coordinate other writers, or make Git and DB atomic.
 * The caller must treat uncertain or partially completed writes as requiring review.
 *
 * repoDir must be a trusted full local repository with origin configured by the
 * server and the observed objects already available. Missing objects fail closed.
 * Ref checks detect differing observations, not ABA changes or changes after
 * return; the eventual publisher must use its own CAS and recovery protocol.
 */
import { prepareDeployCandidate, type PreparedDeployment } from './auto-heal-deploy-git.js';
import type { GitAuthor } from './clawflows-git-client.js';
import { assertDeploymentTreePreserved } from './deploy-tree-guard.js';
import { gitOidPattern, preparedGitRunner, readPreparedPackMetadata, readPreparedRemoteRefs, validPackGitId } from './pack-git-prepared.js';
import { compareWorkflowSnapshotYaml, projectWorkflowSnapshotJson } from './workflow-snapshot-projection.js';

export type PrepareWorkflowSnapshotReleaseInput = Readonly<{
  repoDir: string;
  workflowId: string;
  packId: string;
  candidateCommit: string;
  /** Exact spec_json from the authorized DB row, before JSON.parse. */
  dbSpecJson: string;
  deployNumber: number;
  version: number;
  author: GitAuthor;
  requestId: string;
}>;

export async function prepareWorkflowSnapshotRelease(input: PrepareWorkflowSnapshotReleaseInput): Promise<PreparedDeployment> {
  if (!input || typeof input.repoDir !== 'string' || !input.repoDir
    || !validPackGitId(input.workflowId) || !validPackGitId(input.packId)
    || typeof input.candidateCommit !== 'string' || !gitOidPattern.test(input.candidateCommit)
    || !Number.isSafeInteger(input.deployNumber) || input.deployNumber < 1
    || !Number.isSafeInteger(input.version) || input.version < 1
    || typeof input.requestId !== 'string' || !input.requestId
    || !input.author || typeof input.author.name !== 'string' || !input.author.name.trim()
    || typeof input.author.email !== 'string' || !input.author.email.trim()
    || /[\r\n\0]/.test(input.author.name + input.author.email)) throw new Error('Invalid snapshot release input');
  // Snapshot caller-owned inputs before the first asynchronous read.
  const { repoDir, workflowId, packId, candidateCommit, deployNumber, version, requestId } = input;
  const author = { name: input.author.name, email: input.author.email };
  const dbDocument: unknown = JSON.parse(projectWorkflowSnapshotJson(workflowId, input.dbSpecJson).specJson);
  const savedRef = `refs/heads/saved/${workflowId}`;
  const activeRef = `refs/heads/wf/${workflowId}`;
  const observed = await readPreparedRemoteRefs(repoDir, [savedRef, activeRef]);
  if (observed.get(savedRef) !== candidateCommit) throw new Error('Saved ref does not match candidate');
  const previousCommit = observed.get(activeRef) ?? '';
  const metadata = await readPreparedPackMetadata(repoDir, workflowId, candidateCommit);
  if (metadata.packId !== packId) throw new Error('Candidate pack identity mismatch');
  if (!compareWorkflowSnapshotYaml(workflowId, metadata.content, dbDocument).matches) throw new Error('Candidate DB projection mismatch');
  if (previousCommit) {
    const actual = await preparedGitRunner(repoDir).text(['rev-parse', '--verify', `${previousCommit}^{commit}`]);
    if (actual !== previousCommit) throw new Error('Invalid active commit');
    await assertDeploymentTreePreserved(repoDir, previousCommit, candidateCommit, requestId, metadata.packPath);
  }
  const prepared = await prepareDeployCandidate(repoDir, workflowId, candidateCommit,
    previousCommit, deployNumber, version, author);
  const after = await readPreparedRemoteRefs(repoDir, [savedRef, activeRef]);
  if (after.get(savedRef) !== observed.get(savedRef) || after.get(activeRef) !== observed.get(activeRef)) {
    throw new Error('Workflow refs changed during preparation');
  }
  return prepared;
}
