/**
 * Experimental existing-workflow, non-atomic application service; not crash durable.
 * Trusted composition must authenticate actor, bind repositoryId to config, provide
 * workflow edit authorization and quiesce legacy writers. requestId is correlation only:
 * there is no idempotent deduplication, retry, distributed lock, journal, or recovery.
 *
 * We validate the complete Git candidate before DB CAS, then push with a wf
 * lease. After DB confirmation we re-read the committed draft immediately
 * before push and reject if it changed or cannot be read. This is not a lock:
 * another writer may still change or delete the row after the re-read and before
 * or during push; a crash may also leave DB and Git different. A module-local,
 * per-workflow in-flight guard rejects overlapping calls through this loaded
 * service module; it is not a distributed lock or a cross-store transaction.
 * Confirmed means both operations were acknowledged in this invocation, not a simultaneous snapshot
 * or a guarantee against later changes. Any possibly-issued write failure is
 * unknown, including a rejected lease. The guard ends when this service promise
 * settles, not when an ambiguous remote side effect is proven quiescent; a ref
 * read cannot settle a late push. Hosts must reconcile unknown/partial outcomes
 * and prevent unsafe follow-up writes from every relevant writer.
 * Manifest changes are deliberately unsupported: the candidate API only
 * returns the final Pack ID, so an unchanged manifest is required to prove
 * that the source Pack has the DB's original identity before writing either side.
 * As with pack-version, edit authority for the selected workflow grants write
 * access to the entire DB-bound selected Pack, including shared scripts and
 * resources. It is not a separate Pack ACL. A host can optionally impose an
 * additional candidate restriction; it must never replace workflow edit checks.
 * The manifest and its declared workflow files are reserved from file changes;
 * only the selected workflow projection can update its own YAML. The prototype
 * checks ASCII aliases of protected paths and ancestors in both Git trees; this
 * is not complete filesystem alias verification or an inventory of DB-backed
 * workflows absent from the manifest. Hosts must account for their target
 * filesystems and protect those workflows before enabling writes. This does not
 * resolve concurrent DB or Git changes or provide cross-store atomicity.
 */
import { parse as parseYaml } from 'yaml';
import { performance } from 'node:perf_hooks';
import type { ClawFlowsGitConfig } from './clawflows-git-client.js';
import { EditorDraftCasService } from './editor-draft-cas-service.js';
import { withPreparedPackCandidateForUnifiedSave } from './pack-candidate-save.js';
import { fingerprintWorkflowSnapshotSaveRequest, type WorkflowSnapshotSaveChange, type WorkflowSnapshotSaveRequest } from './workflow-snapshot-save-request.js';
import { projectWorkflowSnapshotJson } from './workflow-snapshot-projection.js';

type Phase = 'not_started' | 'unknown' | 'confirmed';
export type WorkflowUnifiedSaveEvidence = Readonly<{
  db: Phase; git: Phase; inputHash?: string; commit?: string; draftToken?: string;
}>;
export type WorkflowUnifiedSaveResult = Readonly<{
  /** confirmed is only the two acknowledgements from this invocation, never current consistency. */
  outcome: 'rejected' | 'partial' | 'unknown' | 'confirmed'; evidence: WorkflowUnifiedSaveEvidence;
}>;
/** Immutable server-derived candidate for an optional additional restriction.
 * It is not a client-provided grant or proof of a durable or current ACL.
 */
export type WorkflowPackWriteAuthorization = Readonly<{
  actorId: string; workflowId: string; packId: string; repositoryId: string;
  packPath: string; workflowFile: string; sourceVersion: 'current' | 'draft';
  sourceCommit: string; expectedSavedHead: string | null; branch: string; commit: string; inputHash: string;
  /** Exact Pack-relative operations, including contents and modes, not an ACL grant. */
  changes: readonly WorkflowSnapshotSaveChange[];
  /** Complete, confidential Git history. Never log, record, persist, forward or
   * otherwise disclose this bundle; inspect it only within the trusted host.
   */
  bundleBase64: string;
  manifestContent: string;
}>;
type Dependencies = {
  draft: Pick<EditorDraftCasService, 'read' | 'save'>;
  /** Server-owned edit ACL, not the read (view-or-edit) permission. */
  canEdit: (workflowId: string, actorId: string) => Promise<boolean>;
  /** Optional, additional restriction on the server-prepared candidate. When
   * supplied, throwing or non-true denies; absence uses the existing workflow
   * edit authority over the DB-bound selected Pack, including shared files.
   * The bundle contains full Git history: never log, record, persist, forward or
   * disclose it. Inspect it only within the trusted host.
   */
  canWritePack?: (candidate: WorkflowPackWriteAuthorization) => Promise<boolean>;
  /** Both values are trusted server configuration, never a request URL/token. */
  repositoryId: string;
  config: ClawFlowsGitConfig;
  prepare?: typeof withPreparedPackCandidateForUnifiedSave;
};

// ASCII-only portable subset: do not guess Unicode case folding, normalization,
// NTFS short names, streams, or trailing-dot/space aliases. Restrict BOTH change
// paths and protected paths before comparing. This deliberately rejects valid
// names on some filesystems; it is not an inventory of the entire source tree.
function pathKey(file: string): string {
  const parts = file.split('/');
  if (!parts.length || parts.some(part => !/^[A-Za-z0-9_.-]+$/.test(part)
    || part === '.' || part === '..' || part.endsWith('.'))) throw new Error('Non-portable path');
  return file.toLowerCase();
}
function overlaps(a: string, b: string): boolean {
  return a === b || a.startsWith(`${b}/`) || b.startsWith(`${a}/`);
}
function validateChangePaths(changes: readonly WorkflowSnapshotSaveChange[]): void {
  const paths: string[] = [];
  for (const change of changes) {
    const key = pathKey(change.path);
    if (key.split('/').includes('workflow.pack.yaml') || paths.some(path => overlaps(path, key))) {
      throw new Error('Conflicting Pack path');
    }
    paths.push(key);
  }
}

// Manifest edits and aliases are disallowed, so its candidate paths are also the
// source paths. Compare case-insensitively, including ancestors and descendants.
function rejectProtectedChanges(changes: readonly WorkflowSnapshotSaveChange[], manifestContent: string, workflowFile: string): void {
  const manifest: unknown = parseYaml(manifestContent);
  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) throw new Error('Invalid manifest');
  const workflows = (manifest as Record<string, unknown>).workflows;
  if (!Array.isArray(workflows)) throw new Error('Invalid manifest');
  const protectedPaths = ['workflow.pack.yaml', workflowFile];
  for (const workflow of workflows) {
    const file: unknown = typeof workflow === 'string' ? `workflows/${workflow}.yaml`
      : workflow && typeof workflow === 'object' && !Array.isArray(workflow) ? (workflow as Record<string, unknown>).file : undefined;
    if (typeof file !== 'string' || !file) throw new Error('Invalid workflow path');
    protectedPaths.push(file);
  }
  const keys = protectedPaths.map(pathKey);
  for (const change of changes) {
    if (keys.some(file => overlaps(file, pathKey(change.path)))) throw new Error('Protected Pack path');
  }
}

/** deadline uses node:perf_hooks performance.now() in this process. Abort or
 * expiry stops only new writes; an already-issued operation is not cancelled.
 */
export type WorkflowUnifiedSaveControl = Readonly<{ signal: AbortSignal; deadline: number }>;

// Shared by instances using this loaded module, deliberately keyed only by
// workflow ID: independent stores with the same ID may conservatively conflict.
// No timers or queues: a hung operation stays guarded until it actually settles.
// Other workers, module copies and writers do not participate.
const inFlightWorkflows = new Set<string>();

export class WorkflowUnifiedSaveService {
  constructor(private readonly deps: Dependencies) {}

  async save(input: WorkflowSnapshotSaveRequest, authenticatedActorId: string, control?: WorkflowUnifiedSaveControl): Promise<WorkflowUnifiedSaveResult> {
    let db: Phase = 'not_started', git: Phase = 'not_started';
    let inputHash: string | undefined, commit: string | undefined, draftToken: string | undefined;
    let guardedWorkflow: string | undefined;
    const result = (outcome: WorkflowUnifiedSaveResult['outcome']): WorkflowUnifiedSaveResult => ({
      outcome, evidence: { db, git, ...(inputHash ? { inputHash } : {}), ...(commit ? { commit } : {}), ...(draftToken ? { draftToken } : {}) },
    });
    const phases = (): { db: Phase; git: Phase } => ({ db, git });
    const assertLive = (): void => {
      if (control && (control.signal.aborted || !Number.isFinite(control.deadline) || performance.now() >= control.deadline)) throw new Error('Save deadline expired');
    };
    try {
      assertLive();
      // Copy and validate user input, including all changes, before the first await.
      const snapshot = fingerprintWorkflowSnapshotSaveRequest(input);
      inputHash = snapshot.inputHash;
      const request = snapshot.request;
      const config = { ...this.deps.config };
      const repositoryId = this.deps.repositoryId;
      const canWritePack = this.deps.canWritePack;
      if (authenticatedActorId !== request.actorId || request.repositoryId !== repositoryId) return result('rejected');
      if (await this.deps.canEdit(request.workflowId, request.actorId) !== true) return result('rejected');
      validateChangePaths(request.changes);
      assertLive();
      // Acquire synchronously before reading either baseline. Do not let a
      // contender release the owner's guard, and retain it through cleanup.
      if (inFlightWorkflows.has(request.workflowId)) return result('rejected');
      inFlightWorkflows.add(request.workflowId);
      guardedWorkflow = request.workflowId;
      const baseline = await this.deps.draft.read(request.workflowId, request.actorId);
      // Legacy NULL bindings refer to this workflow's own Pack. Preserve NULL in
      // storage: it is not permission to bind an arbitrary Pack or rename it.
      const rawPackId = baseline.packId;
      if (baseline.workflowId !== request.workflowId || (rawPackId ?? request.workflowId) !== request.packId
        || baseline.draftToken !== request.expectedDraftToken) return result('rejected');
      assertLive();
      const prepare = this.deps.prepare ?? withPreparedPackCandidateForUnifiedSave;
      await prepare({ workflowId: request.workflowId, sourceVersion: request.sourceVersion,
        sourceCommit: request.sourceCommit, expectedSavedHead: request.expectedSavedHead,
        config, changes: request.changes.map(change => ({ ...change })) }, snapshot.projection.yaml, async (candidate, push) => {
        if (candidate.packId !== request.packId || candidate.content !== snapshot.projection.yaml
          || candidate.workflowId !== request.workflowId || candidate.sourceVersion !== request.sourceVersion
          || candidate.sourceCommit !== request.sourceCommit
          || candidate.expectedSavedHead !== request.expectedSavedHead || candidate.branch !== `wf/${request.workflowId}`
          || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(candidate.commit)) throw new Error('Candidate mismatch');
        rejectProtectedChanges(request.changes, candidate.manifestContent, candidate.workflowFile);
        // Preserve the exact server-prepared identity for a host that imposes
        // an additional restriction. The workflow edit check and DB-bound Pack
        // checks remain mandatory regardless of whether that restriction exists.
        const authorization: WorkflowPackWriteAuthorization = Object.freeze({
          actorId: authenticatedActorId, workflowId: candidate.workflowId, packId: candidate.packId,
          repositoryId, packPath: candidate.packPath, workflowFile: candidate.workflowFile,
          sourceVersion: request.sourceVersion, sourceCommit: request.sourceCommit,
          expectedSavedHead: request.expectedSavedHead, branch: candidate.branch, commit: candidate.commit,
          inputHash: snapshot.inputHash, changes: request.changes, bundleBase64: candidate.bundleBase64,
          manifestContent: candidate.manifestContent,
        });
        if (canWritePack && await canWritePack(authorization) !== true) throw new Error('Pack write denied');
        assertLive();
        commit = authorization.commit;
        // The CAS repeats the edit ACL, original pack binding and token inside
        // its own transaction. On any exception the commit outcome is unknown.
        db = 'unknown';
        const written = await this.deps.draft.save({ workflowId: request.workflowId, actorId: request.actorId,
          expectedDraftToken: request.expectedDraftToken, ...(rawPackId === null ? {} : { packId: rawPackId }),
          spec: JSON.parse(snapshot.projection.specJson) });
        if (written.workflowId !== request.workflowId || written.packId !== rawPackId
          || !/^[a-f0-9]{64}$/.test(written.draftToken)
          || projectWorkflowSnapshotJson(request.workflowId, written.specJson).digest !== snapshot.projection.digest) throw new Error('Draft mismatch');
        const committedToken = written.draftToken;
        const committedSpecJson = written.specJson;
        draftToken = committedToken;
        db = 'confirmed';
        // Revocation or optional restriction failure after DB acknowledgement must not
        // initiate a Git write: this is partial, not an unknown push. These
        // checks narrow the revocation window but do not provide a lock.
        assertLive();
        if (await this.deps.canEdit(request.workflowId, request.actorId) !== true) throw new Error('Write authorization revoked');
        assertLive();
        if (canWritePack && await canWritePack(authorization) !== true) throw new Error('Write authorization revoked');
        assertLive();
        // A current read is a best-effort fence, not a lock across Git push.
        // Check the raw Pack binding (including NULL) and exact persisted bytes,
        // not only the effective Pack ID or a semantically equivalent projection.
        const latest = await this.deps.draft.read(request.workflowId, request.actorId);
        if (latest.workflowId !== request.workflowId || latest.packId !== rawPackId
          || latest.draftToken !== committedToken || latest.specJson !== committedSpecJson) throw new Error('Draft changed after save');
        assertLive();
        git = 'unknown';
        const pushed = await push();
        if (pushed.commit !== authorization.commit || pushed.branch !== authorization.branch) throw new Error('Push mismatch');
      });
      // Even cleanup errors after an acknowledged push are treated as unknown.
      const finished = phases();
      if (finished.db !== 'confirmed' || finished.git !== 'unknown') return result('unknown');
      git = 'confirmed';
      return result('confirmed');
    } catch {
      // No raw exception, candidate content, config, spec, or credential escapes.
      const failed = phases();
      return result(failed.db === 'unknown' || failed.git === 'unknown' ? 'unknown' : failed.db === 'confirmed' ? 'partial' : 'rejected');
    } finally {
      if (guardedWorkflow !== undefined) inFlightWorkflows.delete(guardedWorkflow);
    }
  }
}
