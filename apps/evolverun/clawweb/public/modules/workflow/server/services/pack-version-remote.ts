import { randomUUID } from 'node:crypto';
import { isMap, isScalar, parseDocument } from 'yaml';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { withGitOperation } from '@avernet/clawweb-shared/server/services/git-execution';
import { resolveAutoHealGitSettings } from '../config/auto-heal-git-config.js';
import { WorkflowDeployHistoryRepository } from '../repositories/workflow-deploy-history-repository.js';
import { createTempRepo, removeTempRepo, type ClawFlowsGitConfig, type GitAuthor } from './clawflows-git-client.js';
import { fetchPackVersionRefs, preparePackVersionRepo } from './pack-version-git-read.js';
import { packVersionPhase } from './pack-version-observability.js';
import { readPackScriptSnapshot, type PackScriptVersion } from './pack-script-snapshot.js';
import { withPreparedPackCandidate, PackCandidateConflictError, PackWorkflowContentChangedError, type PackCandidateChange } from './pack-candidate-save.js';
import { PackOperationError, requestIdentity, legacyWorkflowDocument } from './pack-operation-service.js';
import { editorActiveDeploymentToken, type EditorActiveDeploymentTokenRow } from './editor-active-deployment-token.js';
import { readPreparedPackMetadata } from './pack-git-prepared.js';
import { projectWorkflowSnapshotJson, WORKFLOW_SNAPSHOT_MAX_BYTES } from './workflow-snapshot-projection.js';
import { assertNoUnresolvedLegacyPackOperation } from './legacy-pack-operation-guard.js';

type Context = { db: IDatabase; gitConfig: ClawFlowsGitConfig; gitAuthor: GitAuthor; workflowId: string };
export type SavePackVersionInput = Context & { actorId: string; requestId?: string; sourceVersion: PackScriptVersion;
  sourceCommit: string; expectedSavedHead: string | null; changes: PackCandidateChange[] };
export type SavedPackVersion = { ok: true; saved: true; requestId: string; workflowId: string; packId: string; branch: string; commit: string; version: number };
export async function readPackVersionFromRemote(input: Context & { packId: string; version: PackScriptVersion; includeVersionFiles?: boolean; active?: { tag: string; specJson: string; action: 'deploy' | 'rollback' } }) {
  // Current means the active published tag, never the latest editable wf tip.
  const observedActive = input.version === 'current' && !input.active ? await readBoundActive(input.db, input.workflowId) : null;
  if (input.version === 'current' && !input.active && !observedActive) throw new Error('Workflow snapshot not found');
  if (observedActive) input = { ...input, packId: observedActive.row.pack_id, active: { tag: observedActive.row.tag_name!, specJson: observedActive.row.spec_json, action: observedActive.row.action as 'deploy' | 'rollback' } };
  const { gitConfig } = await packVersionPhase('git_config', () => resolveAutoHealGitSettings(input.db, input.gitConfig, input.gitAuthor));
  return withGitOperation({ workflowId: input.workflowId, operation: 'pack_version_read' }, async () => {
    const dir = await packVersionPhase('git_prepare', async () => {
      const temp = await createTempRepo();
      try { await preparePackVersionRepo(temp, gitConfig, input.workflowId); return temp; }
      catch (error) { await removeTempRepo(temp); throw error; }
    });
    try {
      const verifyRefs = await packVersionPhase('git_fetch', () => fetchPackVersionRefs(dir, gitConfig, input.workflowId, input.active?.tag, !!input.active && input.version === 'draft', input.active?.action === 'rollback'));
      try {
        const snapshot = await packVersionPhase('snapshot', () => readPackScriptSnapshot(dir, input.workflowId, input.packId, input.version, { includeVersionFiles: input.includeVersionFiles !== false, activeTag: input.active?.tag, allowMalformedDraftWorkflow: input.version === 'draft' }));
        let activeHistoryMismatch = false;
        if (input.active) {
          if (!verifyRefs.activeCommit) throw new Error('Active deployment unavailable');
          const metadata = await readPreparedPackMetadata(dir, input.workflowId, verifyRefs.activeCommit);
          const gitDocument = projectLegacyWorkflow(input.workflowId, metadata.content);
          const dbDocument = activeHistoryDocument(input.workflowId, input.active.specJson, gitDocument.digest);
          // The inventory reader omits the trailing slash used by prepared metadata.
          // Compare the actual canonical tree paths, not just matching manifest IDs.
          // Content (digest) differences are actionable editor warnings, not publication
          // identity corruption; #N+1 deployment must not be blocked by stale #N metadata.
          activeHistoryMismatch = gitDocument.digest
            !== projectWorkflowSnapshotJson(input.workflowId, JSON.stringify(dbDocument)).digest;
          if (snapshot.packId !== input.packId || metadata.packId !== input.packId
            || (input.includeVersionFiles !== false && !samePackDirectory(snapshot.packPath, metadata.packPath))) throw new Error('Active deployment snapshot mismatch');

        }
        if (observedActive) {
          const after = await readBoundActive(input.db, input.workflowId);
          if (!after || after.activeHash !== observedActive.activeHash) throw Object.assign(new Error('Active baseline changed'), { code: 'PACK_REFS_CHANGED' });
        }
        if (input.version === 'current' && activeHistoryMismatch) return { ...snapshot, activeHistoryMismatch: true as const };
        return snapshot;
      } finally {
        await packVersionPhase('git_verify', verifyRefs);
      }
    } finally { await removeTempRepo(dir); }
  });
}

function samePackDirectory(snapshotPath: unknown, metadataPath: string): boolean {
  if (typeof snapshotPath !== 'string' || typeof metadataPath !== 'string') return false;
  if (!metadataPath) return snapshotPath === '';
  if (!metadataPath.endsWith('/')) return false;
  const path = metadataPath.slice(0, -1);
  return !!path && path.split('/').every(part => !!part && part !== '.' && part !== '..') && snapshotPath === path;
}
function projectLegacyWorkflow(workflowId: string, yaml: string) {
  return projectWorkflowSnapshotJson(workflowId, JSON.stringify(legacyWorkflowDocument(workflowId, yaml)));
}

/** A direct historical document may itself have authored content. Compare the
 * entire DB document to the active Git YAML first. Only when it differs may
 * content be read as a legacy archive, after validating the entire projection.
 * This checks consistency, not historical provenance. Never unwrap twice. */
function activeHistoryDocument(workflowId: string, specJson: string, gitDigest: string): unknown {
  // History can be a complete JSON/YAML document or one JSON/YAML string
  // containing that document. Decode only one string layer; token calculation
  // remains over the exact original DB text, never this parsed projection.
  if (typeof specJson !== 'string' || Buffer.byteLength(specJson) > WORKFLOW_SNAPSHOT_MAX_BYTES) throw new Error('Invalid active history');
  const parsed = parseDocument(specJson, { uniqueKeys: true, prettyErrors: false });
  if (parsed.errors.length || parsed.warnings.length) throw new Error('Invalid active history');
  let source: string;
  if (isMap(parsed.contents)) source = specJson;
  else if (isScalar(parsed.contents) && typeof parsed.contents.value === 'string'
    && (!parsed.contents.tag || parsed.contents.tag === 'tag:yaml.org,2002:str')) source = parsed.contents.value;
  else throw new Error('Invalid active history');
  // The legacy parser checks duplicate keys, numeric precision, supported tags,
  // alias expansion, bounds, and workflow identity before projection.
  const outer = projectLegacyWorkflow(workflowId, source);
  const document = JSON.parse(outer.specJson) as Record<string, unknown>;
  if (outer.digest === gitDigest || typeof document.content !== 'string') return document;
  const archived = projectLegacyWorkflow(workflowId, document.content);
  const archiveDocument = JSON.parse(archived.specJson) as Record<string, unknown>;
  const reconstructed = projectWorkflowSnapshotJson(workflowId, JSON.stringify({ ...archiveDocument, content: document.content }));
  if (outer.digest !== reconstructed.digest) throw new Error('Invalid active history archive');
  return archiveDocument;
}

type ActiveRow = EditorActiveDeploymentTokenRow & { workflow_id: string; is_active: number };
async function readBoundActive(db: IDatabase, workflowId: string) {
  const rows = await db.query<ActiveRow>('SELECT workflow_id, is_active, pack_id, deploy_number, version, tag_name, action, spec_json FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', [workflowId]);
  if (!rows.length) return null;
  const row = rows[0], prefix = `deploy/${workflowId}/#`;
  if (rows.length !== 1 || row.workflow_id !== workflowId || Number(row.is_active) !== 1
    || (row.action !== 'deploy' && row.action !== 'rollback') || typeof row.tag_name !== 'string'
    || !row.tag_name.startsWith(prefix) || !/^[1-9][0-9]*$/.test(row.tag_name.slice(prefix.length))
    || !/^[1-9][0-9]*$/.test(String(row.version)) || !/^[1-9][0-9]*$/.test(String(row.deploy_number))
    || (row.action === 'deploy' && row.tag_name !== `${prefix}${row.deploy_number}`)) throw new Error('Active deployment unavailable');
  return { row, activeHash: editorActiveDeploymentToken(workflowId, row) };
}
/** Read-only fallback for a verified editor without a database draft. The tag and
 * any selected current Pack must agree with the unique active DB row. This is not an atomic view:
 * refs and rows can change after the final checks; writes must revalidate. */
export async function readActivePackVersionFromRemote(input: Context & { version: PackScriptVersion }) {
  const readState = async () => {
    const draft = await input.db.query('SELECT workflow_id FROM workflow_specs WHERE workflow_id = ?', [input.workflowId]);
    const active = await input.db.query<ActiveRow>('SELECT workflow_id, is_active, pack_id, deploy_number, version, tag_name, action, spec_json FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', [input.workflowId]);
    if (draft.length || active.length !== 1) return null;
    const row = active[0];
    if (row.workflow_id !== input.workflowId || Number(row.is_active) !== 1 || (row.action !== 'deploy' && row.action !== 'rollback')
      || typeof row.pack_id !== 'string' || !/^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/.test(row.pack_id) || row.pack_id.includes('..') || row.pack_id.endsWith('.') || row.pack_id.endsWith('.lock')
      || !/^[1-9][0-9]*$/.test(String(row.deploy_number)) || typeof row.tag_name !== 'string'
      || !row.tag_name.startsWith(`deploy/${input.workflowId}/#`) || !/^[1-9][0-9]*$/.test(row.tag_name.slice(`deploy/${input.workflowId}/#`.length))
      || (row.action === 'deploy' && row.tag_name !== `deploy/${input.workflowId}/#${row.deploy_number}`)) return null;
    return { row, activeHash: editorActiveDeploymentToken(input.workflowId, row) };
  };
  const before = await readState();
  if (!before) throw Object.assign(new Error('Active baseline unavailable'), { status: 404, code: 'WORKFLOW_NOT_FOUND' });
  const snapshot = await readPackVersionFromRemote({ ...input, packId: before.row.pack_id,
    active: { tag: before.row.tag_name!, specJson: before.row.spec_json, action: before.row.action as 'deploy' | 'rollback' } });
  const after = await readState();
  if (!after || before.activeHash !== after.activeHash) throw Object.assign(new Error('Active baseline changed'), { status: 409, code: 'VERSION_CONFLICT' });
  // This attests only the no-draft, active-history path after its final DB and
  // Git ref rechecks. It is a read observation, not a write authorization or lock.
  return input.version === 'current'
    ? { ...snapshot, activeHistory: { protocol: 'active-history-v1' as const, activeToken: after.activeHash } }
    : snapshot;
}

/** Saves version files to Git without a database operation journal. Callers must
 * treat an uncertain push as unknown; a request ID is correlation only, not
 * durable idempotency or a recovery receipt. */
export async function savePackVersionToRemote(input: SavePackVersionInput): Promise<SavedPackVersion> {
  if (!input.actorId?.trim()) throw new PackOperationError(403, 'FORBIDDEN');
  const requestId = requestIdentity(input.requestId, randomUUID);
  await assertNoUnresolvedLegacyPackOperation(input.db, input.workflowId);
  const { gitConfig } = await resolveAutoHealGitSettings(input.db, input.gitConfig, input.gitAuthor);
  // Read version before pushing: a later database failure must not be presented
  // as a definite Git failure. This value is informational, not a reservation.
  const version = await new WorkflowDeployHistoryRepository(input.db).getLatestVersion(input.workflowId) + 1;
  try {
    return await withPreparedPackCandidate({ workflowId: input.workflowId, sourceVersion: input.sourceVersion,
      sourceCommit: input.sourceCommit, expectedSavedHead: input.expectedSavedHead, changes: input.changes, config: gitConfig, preserveWorkflowContent: true },
    async (git, push) => {
      // Re-read after candidate preparation, immediately before pushing. Legacy
      // NULL pack_id means workflow_id, as it does for Pack reads; it does not
      // authorize an arbitrary rename or cause an implicit database update.
      const rows = await input.db.query<{ workflow_id: string; pack_id: string | null }>(
        'SELECT workflow_id, pack_id FROM workflow_specs WHERE workflow_id = ?', [input.workflowId]);
      const row = rows[0];
      if (!row || row.workflow_id !== input.workflowId) throw new PackOperationError(404, 'WORKFLOW_NOT_FOUND', requestId);
      if ((row.pack_id ?? row.workflow_id) !== git.packId) throw new PackOperationError(409, 'PACK_ID_MISMATCH', requestId);
      await push();
      return { ok: true, saved: true, requestId, workflowId: input.workflowId, packId: git.packId,
        branch: git.branch, commit: git.commit, version };
    });
  } catch (error) {
    if (error instanceof PackWorkflowContentChangedError) throw new PackOperationError(400, 'WORKFLOW_YAML_REQUIRES_DB_SAVE', requestId);
    if (error instanceof PackCandidateConflictError) throw new PackOperationError(409, 'VERSION_CONFLICT', requestId);
    throw error;
  }
}

/** No durable journal exists; do not claim status or reconciliation support. */
export async function readPackOperationFromRemote(_input: Context & { requestId: string; actorId: string }): Promise<never> {
  throw new PackOperationError(503, 'OPERATION_RECOVERY_UNAVAILABLE');
}
export async function reconcilePackOperationFromRemote(_input: Context & { requestId: string; actorId: string }): Promise<never> {
  throw new PackOperationError(503, 'OPERATION_RECOVERY_UNAVAILABLE');
}
