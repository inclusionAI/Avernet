/** Compatibility writer for normalized workflow specs, including first saves.
 * The caller authorizes the write and durably journals prepared before push.
 * All Git publication uses the same CAS/recovery primitive as binary Pack edits.
 */
import { stat, readFile } from 'node:fs/promises';
import path from 'node:path';
import { withGitOperation } from '@avernet/clawweb-shared/server/services/git-execution';
import type { WorkflowSpec } from '../workflow.js';
import { cloneClawflows, createTempRepo, removeTempRepo, writeWorkflowSpecToRepo, ensurePackManifest,
  resolvePackPaths, type ClawFlowsGitConfig, type GitAuthor } from './clawflows-git-client.js';
import { PackCandidateConflictError, PackCandidateOutcomeUnknownError, pushPreparedPackCandidate,
  type PreparedPackCandidate, type SavePackCandidateResult } from './pack-candidate-save.js';
import { createPreparedGitBundle, readPreparedPackMetadata, readPreparedRemoteRefs, preparedGitRunner,
  gitOidPattern, validPackGitId } from './pack-git-prepared.js';

export type PrepareWorkflowSpecInput = {
  workflowId: string;
  packId: string;
  spec: WorkflowSpec;
  config: ClawFlowsGitConfig;
  author: GitAuthor;
  /** Omission captures the observed remote saved head; null requires absence. */
  expectedSavedHead?: string | null;
  /** If supplied, must match the selected saved, wf, or default-HEAD base. */
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
      await cloneClawflows(dir, input.config);
      const { text } = preparedGitRunner(dir);
      const savedRef = `refs/heads/saved/${input.workflowId}`;
      const wfRef = `refs/heads/wf/${input.workflowId}`;
      const refs = await readPreparedRemoteRefs(dir, [savedRef, wfRef]);
      const expectedSavedHead = refs.get(savedRef) ?? null;
      if (input.expectedSavedHead !== undefined && input.expectedSavedHead !== expectedSavedHead) throw new PackCandidateConflictError();
      const wf = refs.get(wfRef) ?? null;
      let sourceCommit = expectedSavedHead ?? wf;
      const sourceVersion = expectedSavedHead ? 'draft' : wf ? 'current' : 'initial';
      if (sourceCommit === null) {
        try { sourceCommit = await text(['rev-parse', '--verify', 'HEAD^{commit}']); }
        catch (error) {
          if ((error as { status?: number }).status !== 128) throw error;
          // Only an absent branch behind a valid symbolic HEAD is truly unborn.
          const head = await text(['symbolic-ref', '--quiet', 'HEAD']);
          if (!head.startsWith('refs/heads/')) throw new Error('Invalid initial HEAD');
          try { await text(['show-ref', '--verify', '--quiet', head]); throw new Error('Invalid initial commit'); }
          catch (missing) { if ((missing as { status?: number }).status !== 1) throw missing; }
        }
      }
      if ((sourceCommit !== null && !gitOidPattern.test(sourceCommit))
        || (input.sourceCommit !== undefined && input.sourceCommit !== sourceCommit)) throw new PackCandidateConflictError();
      if (sourceCommit !== null) {
        // Fail closed if the remote advanced beyond the clone; never fall back to
        // a different base or drop the Pack's inherited resources.
        if (await text(['rev-parse', '--verify', `${sourceCommit}^{commit}`]) !== sourceCommit) throw new PackCandidateConflictError();
        await text(['checkout', '--quiet', '--detach', sourceCommit]);
        await text(['read-tree', sourceCommit]);
      } else await text(['read-tree', '--empty']);
      await writeWorkflowSpecToRepo(dir, input.packId, input.workflowId, input.spec);
      const previousPaths = resolvePackPaths(dir, input.packId, input.workflowId);
      let previousManifest: Buffer | undefined;
      try {
        if ((await stat(previousPaths.manifestPath)).size > 1024 * 1024) throw new Error('Manifest or workflow too large');
        previousManifest = await readFile(previousPaths.manifestPath);
      } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error; }
      await ensurePackManifest(dir, input.packId, input.workflowId);
      const paths = resolvePackPaths(dir, input.packId, input.workflowId);
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
      const commit = await text(['-c', `user.name=${input.author.name}`, '-c', `user.email=${input.author.email}`,
        'commit-tree', tree, ...(sourceCommit === null ? [] : ['-p', sourceCommit]), '-m', `Save workflow ${input.workflowId}`]);
      if (!gitOidPattern.test(commit)) throw new Error('Invalid candidate commit');
      const metadata = await readPreparedPackMetadata(dir, input.workflowId, commit);
      if (metadata.packId !== input.packId) throw new Error('Candidate pack identity mismatch');
      const prepared: PreparedPackCandidate = Object.freeze({ ...metadata, workflowId: input.workflowId,
        branch: `saved/${input.workflowId}`, commit, sourceVersion, sourceCommit, expectedSavedHead,
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
