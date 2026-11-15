/** Compatibility writer for normalized workflow specs, including first saves.
 * The caller authorizes the write and durably journals prepared before push.
 * All Git publication uses the same CAS/recovery primitive as binary Pack edits.
 */
import { workflowSavedGitRunner, initSavedWorkflowRepo, workflowSavedCommitMessage } from './workflow-saved-ref.js';
import { stat, lstat, readFile, writeFile } from 'node:fs/promises';
import { stringify as stringifyYaml, parse as parseYaml } from 'yaml';
import path from 'node:path';
import { withGitOperation } from '@avernet/clawweb-shared/server/services/git-execution';
import type { WorkflowSpec } from '../workflow.js';
import { createTempRepo, removeTempRepo, writeWorkflowSpecToRepo, ensurePackManifest,
  resolvePackPaths, type ClawFlowsGitConfig, type GitAuthor } from './clawflows-git-client.js';
import { PackCandidateConflictError, PackCandidateOutcomeUnknownError, pushPreparedPackCandidate,
  type PreparedPackCandidate, type SavePackCandidateResult } from './pack-candidate-save.js';
import { createPreparedGitBundle, readPreparedPackMetadata,
  gitOidPattern, validPackGitId } from './pack-git-prepared.js';

/** No fallback source exists for an already-known workflow. */
export class WorkflowSaveRequiredError extends Error {
  readonly code = 'SAVE_REQUIRED';
  constructor() { super('Workflow wf snapshot is required before saving'); this.name = 'WorkflowSaveRequiredError'; }
}

export type PrepareWorkflowSpecInput = {
  workflowId: string;
  packId: string;
  spec: WorkflowSpec;
  config: ClawFlowsGitConfig;
  author: GitAuthor;
  /** New identities may not adopt an existing wf snapshot. */
  createOnly?: boolean;
  /** Omission captures the observed remote wf head (wire name retained); null requires absence. */
  expectedSavedHead?: string | null;
  /** If supplied, must match the observed wf commit. */
  sourceCommit?: string;
};

export async function withPreparedWorkflowSpec<T>(input: PrepareWorkflowSpecInput,
  callback: (prepared: PreparedPackCandidate, push: () => Promise<SavePackCandidateResult>) => Promise<T>): Promise<T> {
  if (!validPackGitId(input.workflowId) || !validPackGitId(input.packId)
    || (input.sourceCommit !== undefined && !gitOidPattern.test(input.sourceCommit))
    || (input.expectedSavedHead !== undefined && input.expectedSavedHead !== null && !gitOidPattern.test(input.expectedSavedHead))) {
    throw new Error('Invalid workflow spec save input');
  }
  return withGitOperation({}, async () => {
    const dir = await createTempRepo();
    let active = true;
    let attempted: SavePackCandidateResult | undefined;
    let pending: Promise<SavePackCandidateResult> | undefined;
    try {
      const savedState = await initSavedWorkflowRepo(dir, input.workflowId, input.config);
      const { text } = workflowSavedGitRunner(dir);
      const expectedSavedHead = savedState.wfHead;
      if (input.expectedSavedHead !== undefined && input.expectedSavedHead !== expectedSavedHead) throw new PackCandidateConflictError();
      if (input.createOnly && expectedSavedHead !== null) throw new PackCandidateConflictError();
      if (savedState.commit === null && input.createOnly !== true) throw new WorkflowSaveRequiredError();
      const sourceCommit = savedState.commit;
      const sourceVersion = sourceCommit ? 'current' : 'initial';
      // With no wf, initialize a new Pack in this empty repo. Never checkout or
      // inherit the remote default branch, which may be an unrelated old draft.
      if ((sourceCommit !== null && !gitOidPattern.test(sourceCommit))
        || (input.sourceCommit !== undefined && input.sourceCommit !== sourceCommit)) throw new PackCandidateConflictError();
      if (sourceCommit !== null) {
        // Fail closed if the remote advanced beyond the clone; never fall back to
        // a different base or drop the Pack's inherited resources.
        if (await text(['rev-parse', '--verify', `${sourceCommit}^{commit}`]) !== sourceCommit) throw new PackCandidateConflictError();
        await text(['checkout', '--quiet', '--detach', sourceCommit]);
        await text(['read-tree', sourceCommit]);
      } else await text(['read-tree', '--empty']);
      if (input.createOnly) {
        // New Pack files must not pre-exist in this isolated empty repository.
        const paths = resolvePackPaths(dir, input.packId, input.workflowId);
        for (const file of [paths.manifestPath, paths.specPath]) {
          try { await lstat(file); throw new PackCandidateConflictError(); }
          catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
        }
      }
      await writeWorkflowSpecToRepo(dir, input.packId, input.workflowId, input.spec);
      const previousPaths = resolvePackPaths(dir, input.packId, input.workflowId);
      // Preserve the DB document exactly, including numeric/absent version;
      // normalization is for validation only, not an alternate Git document.
      await writeFile(previousPaths.specPath, stringifyYaml(input.spec, { lineWidth: 0 }));
      let previousManifest: Buffer | undefined;
      try {
        if ((await stat(previousPaths.manifestPath)).size > 1024 * 1024) throw new Error('Manifest or workflow too large');
        previousManifest = await readFile(previousPaths.manifestPath);
      } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
      // A YAML-only save must not normalize an unchanged legacy manifest.
      // Its existing representation, comments, bytes and index mode are Pack data.
      const declared = previousManifest ? parseYaml(previousManifest.toString('utf8'))?.workflows : undefined;
      const includesWorkflow = Array.isArray(declared) && declared.some(item =>
        (typeof item === 'string' ? item : item?.id) === input.workflowId);
      if (!includesWorkflow) await ensurePackManifest(dir, input.packId, input.workflowId);
      const paths = resolvePackPaths(dir, input.packId, input.workflowId);
      // A new workflow owns only the newly initialized Pack.
      if (input.createOnly) await text(['read-tree', '--empty']);
      // Stage only the two owned files, directly from bytes. git add . would
      // apply filters and could accidentally rewrite inherited binary resources.
      for (const file of [paths.specPath, paths.manifestPath]) {
        if ((await stat(file)).size > 1024 * 1024) throw new Error('Manifest or workflow too large');
        // If ensurePackManifest made no edit, preserve the immutable source blob,
        // not checkout-converted (e.g. CRLF) bytes from the worktree.
        if (file === paths.manifestPath && previousManifest?.equals(await readFile(file))) continue;
        const relative = path.relative(dir, file).split(path.sep).join('/');
        const staged = await text(['ls-files', '--stage', '--', `:(literal)${relative}`]);
        const mode = staged ? staged.slice(0, 6) : '100644';
        if (!['100644', '100755'].includes(mode)) throw new Error('Unsafe manifest or workflow mode');
        const oid = await text(['hash-object', '-w', '--no-filters', '--', file]);
        if (!gitOidPattern.test(oid)) throw new Error('Invalid workflow blob');
        await text(['update-index', '--add', '--cacheinfo', mode, oid, relative]);
      }
      const tree = await text(['write-tree']);
      const packPath = path.relative(dir, path.dirname(paths.manifestPath)).split(path.sep).join('/');
      const message = await workflowSavedCommitMessage(dir, input.workflowId, tree, { packId: input.packId,
        packPath, workflowFile: path.relative(path.dirname(paths.manifestPath), paths.specPath).split(path.sep).join('/') });
      const commit = await text(['-c', `user.name=${input.author.name}`, '-c', `user.email=${input.author.email}`,
        'commit-tree', tree, ...(sourceCommit === null ? [] : ['-p', sourceCommit]), '-m', message]);
      if (!gitOidPattern.test(commit)) throw new Error('Invalid candidate commit');
      const metadata = await readPreparedPackMetadata(dir, input.workflowId, commit);
      if (metadata.packId !== input.packId) throw new Error('Candidate pack identity mismatch');
      const prepared: PreparedPackCandidate = Object.freeze({ ...metadata, workflowId: input.workflowId,
        branch: `wf/${input.workflowId}`, commit, sourceVersion, sourceCommit, expectedSavedHead,
        bundleBase64: await createPreparedGitBundle(dir, [commit]) });
      const result = await callback(prepared, () => {
        if (!active) return Promise.reject(new Error('Prepared candidate callback is no longer active'));
        if (pending) return Promise.reject(new Error('Prepared candidate push already attempted'));
        attempted = { branch: prepared.branch, commit: prepared.commit };
        pending = pushPreparedPackCandidate(dir, prepared);
        // Observe rejection immediately even if a caller forgets to await push.
        void pending.catch(() => {});
        return pending;
      });
      active = false;
      if (pending) await pending;
      return result;
    } catch (error) {
      // Once push started, even a callback failure can follow remote success.
      // Preserve the immutable reconciliation identity, not just a DB error.
      if (attempted) throw new PackCandidateOutcomeUnknownError(attempted.branch, attempted.commit);
      throw error;
    } finally {
      active = false;
      if (pending) await Promise.allSettled([pending]);
      try { await removeTempRepo(dir); }
      catch (error) { if (attempted) throw new PackCandidateOutcomeUnknownError(attempted.branch, attempted.commit); throw error; }
    }
  });
}
