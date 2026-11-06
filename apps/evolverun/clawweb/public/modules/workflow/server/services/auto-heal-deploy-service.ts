/** Deploy the database workflow with the Pack at saved, or its own existing wf ref if unsaved.
 * There is no durable operation journal: a Git/DB partial result requires manual reconciliation. */
import { randomUUID } from 'node:crypto';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { stringify as stringifyYaml } from 'yaml';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { withGitOperation } from '@avernet/clawweb-shared/server/services/git-execution';
import type { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { WorkflowSpecRepository, type WorkflowSpecRow } from '../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../repositories/workflow-deploy-history-repository.js';
import { cloneClawflows, createTempRepo, getMaxDeployNumberFromGitTags, removeTempRepo,
  type ClawFlowsGitConfig, type GitAuthor } from './clawflows-git-client.js';
import { prepareDeployCandidate, pushPreparedDeployment, type PreparedDeployment } from './auto-heal-deploy-git.js';
import { gitOidPattern, readPreparedPackMetadata, readPreparedRemoteRefs, preparedGitRunner, validPackGitId } from './pack-git-prepared.js';
import { candidateProjection, PackOperationError, requestIdentity } from './pack-operation-service.js';
import { assertNoUnresolvedLegacyPackOperation } from './legacy-pack-operation-guard.js';
import { assertDeploymentTreePreserved } from './deploy-tree-guard.js';
import { editorDraftToken } from './editor-draft-token.js';
import { assertLosslessJson } from './lossless-json-validation.js';
import { compareWorkflowSnapshotYaml, projectWorkflowSnapshotJson } from './workflow-snapshot-projection.js';

export type AutoHealDeployInput = { workflowId: string; actorId: string; candidateCommit?: string; requestId?: string;
  packId?: string | null; diagnosisId?: string | null; sourceFlowId?: string | null; note?: string | null; autoRun?: boolean };
/** Legacy non-v2 Editor deployment requires this independent host policy.
 * Snapshot v2 uses workflow edit permission plus saved-candidate, Pack binding,
 * DB and tree checks; when a host supplies this policy it also restricts v2.
 * Permission and ref observations are not an atomic lease across Git, DB and authorization. */
export type SnapshotDeployAuthorization = Readonly<{ actorId: string; workflowId: string; packId: string; candidateCommit: string }>;
export type AuthorizeSnapshotDeploy = (candidate: SnapshotDeployAuthorization) => Promise<boolean>;
export type EditorDeployInput = AutoHealDeployInput & { expectedDraftToken: string; snapshotProtocol?: 'saved-tree-v2'; expectedActiveCommit?: string; confirmedFileDiff?: true };
export type AutoHealDeployResult = { ok: boolean; deployed: boolean; workflowId: string; packId: string; deployNumber: number;
  version: number; tagName: string; requestId?: string; commit?: string; candidateCommit?: string; messageId?: string | null; snapshotProtocol?: 'saved-tree-v2'; expectedActiveCommit?: string };

type Projection = ReturnType<typeof candidateProjection>;
function workflowYaml(row: WorkflowSpecRow): string {
  try {
    assertLosslessJson(row.spec_json);
    const value: unknown = JSON.parse(row.spec_json);
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid workflow');
    const { content, ...spec } = value as Record<string, unknown>;
    // Only an unstructured content-only wrapper has unambiguous provenance.
    // A structured document may legitimately contain any `content` string,
    // including one resembling its own YAML; never silently discard it.
    if (typeof content === 'string' && !Array.isArray(spec.nodes)) return content;
    return stringifyYaml(value);
  } catch { throw new PackOperationError(400, 'INVALID_WORKFLOW'); }
}

/** Build an immutable tree from the observed workflow ref, staging only the selected YAML bytes without filters. */
async function combineSourceAndYaml(dir: string, workflowId: string, source: string, content: string, author: GitAuthor) {
  const metadata = await readPreparedPackMetadata(dir, workflowId, source);
  const file = `${metadata.packPath}${metadata.workflowFile}`;
  const { text } = preparedGitRunner(dir);
  await text(['read-tree', source]);
  const staged = await text(['ls-files', '--stage', '--', `:(literal)${file}`]);
  const mode = staged.slice(0, 6);
  if (!['100644', '100755'].includes(mode)) throw new Error('Unsafe workflow file mode');
  if (Buffer.byteLength(content) > 1024 * 1024 || content.includes('\0')) throw new PackOperationError(400, 'INVALID_WORKFLOW');
  // Outside the checkout: never follow paths from the remote tree on the filesystem.
  const temp = path.join(dir, '.git', `workflow-${randomUUID()}`);
  await writeFile(temp, content, { mode: 0o600, flag: 'wx' });
  const oid = await text(['hash-object', '-w', '--no-filters', '--', temp]);
  if (!gitOidPattern.test(oid)) throw new Error('Invalid workflow blob');
  await text(['update-index', '--add', '--cacheinfo', mode, oid, file]);
  const tree = await text(['write-tree']);
  const commit = await text(['-c', `user.name=${author.name}`, '-c', `user.email=${author.email}`,
    'commit-tree', tree, '-p', source, '-m', `Prepare workflow ${workflowId}`]);
  if (!gitOidPattern.test(commit)) throw new Error('Invalid candidate commit');
  return { commit, metadata };
}

/** Nested Pack previews contain only Pack-relative files; never publish unseen outside changes. */
async function assertNoOutsideChanges(dir: string, active: string, candidate: string, packPath: string, requestId: string): Promise<void> {
  const data = await preparedGitRunner(dir).bytes(active
    ? ['diff', '--name-only', '--no-renames', '-z', active, candidate, '--']
    : ['ls-tree', '-r', '-z', '--name-only', candidate]);
  if (data.length && data[data.length - 1] !== 0) throw new Error('Invalid deployment diff');
  const decoder = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true });
  const names = data.length ? data.subarray(0, -1).toString('binary').split('\0').map(name => decoder.decode(Buffer.from(name, 'binary'))) : [];
  if (names.some(name => !name.startsWith(packPath))) throw new PackOperationError(409, 'DEPLOY_TREE_CONFLICT', requestId);
}

async function checkFacades(db: IDatabase, workflowId: string, commands: string[] | undefined, lock = '') {
  for (const command of commands ?? []) {
    const owners = await db.query<{ workflow_id: string }>(`SELECT workflow_id FROM facade_bindings WHERE command = ?${lock}`, [command]);
    if (owners.some(owner => owner.workflow_id !== workflowId)) throw new PackOperationError(409, 'FACADE_CONFLICT');
  }
}

export class AutoHealDeployService {
  constructor(private db: IDatabase, private specRepo: WorkflowSpecRepository, private deployHistoryRepo: WorkflowDeployHistoryRepository,
    private botPermRepo: BotWorkflowPermissionRepository | null, private gitConfig: ClawFlowsGitConfig, private author: GitAuthor,
    private triggerRun?: (workflowId: string, actorId: string, sourceFlowId?: string | null) => Promise<string | null>,
    private authorizeSnapshotDeploy?: AuthorizeSnapshotDeploy) {}

  /** Independent authorized observation; HTTP errors are not proof of absence. */
  async editorDeploymentState(workflowId: string, actorId: string) {
    if (!validPackGitId(workflowId) || /^__.+__$/.test(workflowId)) throw new PackOperationError(400, 'INVALID_REQUEST');
    if (!actorId?.trim() || !this.botPermRepo) throw new PackOperationError(403, 'FORBIDDEN');
    // Match the pack-version read boundary, not the broader AutoHeal deploy permission.
    if (await this.botPermRepo.hasEditPermission(workflowId, actorId) !== true) throw new PackOperationError(403, 'FORBIDDEN');
    const row = await this.specRepo.findByWorkflowId(workflowId);
    if (!row || row.workflow_id !== workflowId) throw new PackOperationError(404, 'WORKFLOW_NOT_FOUND');
    const packId = row.pack_id ?? workflowId;
    if (!validPackGitId(packId)) throw new PackOperationError(409, 'PACK_ID_MISMATCH');
    return withGitOperation({ workflowId, operation: 'autoheal_deploy' }, async () => {
      const dir = await createTempRepo();
      try {
        await cloneClawflows(dir, this.gitConfig);
        const savedRef = `refs/heads/saved/${workflowId}`, activeRef = `refs/heads/wf/${workflowId}`;
        const refs = await readPreparedRemoteRefs(dir, [savedRef, activeRef]);
        const active = await this.db.query('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1 ORDER BY id', [workflowId]);
        const after = await readPreparedRemoteRefs(dir, [savedRef, activeRef]);
        if (refs.get(savedRef) !== after.get(savedRef) || refs.get(activeRef) !== after.get(activeRef)) throw new PackOperationError(409, 'DEPLOY_BASELINE_STALE');
        const savedCommit = refs.get(savedRef), activeCommit = refs.get(activeRef);
        if (!savedCommit) throw new PackOperationError(409, 'SAVE_REQUIRED');
        if (!activeCommit && active.length) throw new PackOperationError(409, 'DEPLOY_BASELINE_MISSING');
        const metadata = await readPreparedPackMetadata(dir, workflowId, savedCommit);
        if (metadata.packId !== packId) throw new PackOperationError(409, 'PACK_ID_MISMATCH');
        if (this.authorizeSnapshotDeploy) await this.assertSnapshotDeployAuthorized({ actorId, workflowId, packId, candidateCommit: savedCommit });
        return { protocol: 'editor-deployment-state-v1', workflowId, packId, canDeploy: true, savedCommit, activeCommit: activeCommit ?? null };
      } finally { await removeTempRepo(dir); }
    });
  }

  async deploy(input: AutoHealDeployInput): Promise<AutoHealDeployResult> {
    return this.deployWithPolicy(input, false);
  }

  async editorDeploy(input: EditorDeployInput): Promise<AutoHealDeployResult> {
    return this.deployWithPolicy(input, true);
  }

  private async deployWithPolicy(input: AutoHealDeployInput | EditorDeployInput, preserveTree: boolean): Promise<AutoHealDeployResult> {
    const { workflowId, actorId } = input;
    const protocol = preserveTree ? (input as EditorDeployInput).snapshotProtocol : undefined;
    if (protocol !== undefined && protocol !== 'saved-tree-v2') throw new PackOperationError(400, 'INVALID_REQUEST');
    const exactSnapshot = protocol === 'saved-tree-v2';
    const expectedActiveCommit = preserveTree ? (input as EditorDeployInput).expectedActiveCommit : undefined;
    if (exactSnapshot && ((input as EditorDeployInput).confirmedFileDiff !== true || !input.candidateCommit || typeof expectedActiveCommit !== 'string' || (expectedActiveCommit !== '' && !gitOidPattern.test(expectedActiveCommit)))) throw new PackOperationError(400, 'INVALID_REQUEST');
    if (!validPackGitId(workflowId) || /^__.+__$/.test(workflowId)
      || (input.candidateCommit !== undefined && !gitOidPattern.test(input.candidateCommit))) throw new PackOperationError(400, 'INVALID_REQUEST');
    if (!actorId?.trim() || !this.botPermRepo) throw new PackOperationError(403, 'FORBIDDEN');
    const [canExecute, canEdit] = await Promise.all([this.botPermRepo.hasExecutePermission(workflowId, actorId), this.botPermRepo.hasEditPermission(workflowId, actorId)]);
    // Both Editor routes publish a complete Pack; execute-only access remains
    // sufficient only for the separate legacy AutoHeal deploy route.
    if (preserveTree ? canEdit !== true : !canExecute && !canEdit) throw new PackOperationError(403, 'FORBIDDEN');
    if (preserveTree && !exactSnapshot && !this.authorizeSnapshotDeploy) throw new PackOperationError(403, 'SNAPSHOT_DEPLOY_FORBIDDEN');
    const expectedDraftToken = preserveTree ? (input as EditorDeployInput).expectedDraftToken : undefined;
    if (preserveTree && (typeof expectedDraftToken !== 'string' || !/^[a-f0-9]{64}$/.test(expectedDraftToken))) throw new PackOperationError(400, 'INVALID_REQUEST');
    const row = await this.specRepo.findByWorkflowId(workflowId);
    // Preserve the legacy AutoHeal behavior; an absent Editor draft is a CAS conflict.
    if (!preserveTree && (!row || row.workflow_id !== workflowId)) throw new PackOperationError(404, 'WORKFLOW_NOT_FOUND');
    const requestId = preserveTree ? requestIdentity(input.requestId, randomUUID) : undefined;
    if (preserveTree && (!row || row.workflow_id !== workflowId || editorDraftToken(row) !== expectedDraftToken)) throw new PackOperationError(409, 'DRAFT_CONFLICT', requestId);
    if (!row) throw new PackOperationError(404, 'WORKFLOW_NOT_FOUND');
    await assertNoUnresolvedLegacyPackOperation(this.db, workflowId);
    const operationRequestId = requestId ?? requestIdentity(input.requestId, randomUUID);
    const content = workflowYaml(row);
    let pushStarted = false, pushConfirmed = false, dbConfirmed = false;
    let prepared: PreparedDeployment | undefined;
    let result: AutoHealDeployResult;
    try {
      result = await withGitOperation({ workflowId, operation: 'autoheal_deploy' }, async () => {
        const dir = await createTempRepo();
        try {
          await cloneClawflows(dir, this.gitConfig);
          const savedRef = `refs/heads/saved/${workflowId}`, activeRef = `refs/heads/wf/${workflowId}`;
          const refs = await readPreparedRemoteRefs(dir, [savedRef, activeRef]);
          // Legacy workflows may have a published wf but no saved ref. Never use the
          // default branch or a caller-supplied object as provenance for their Pack.
          const activeCommit = refs.get(activeRef) ?? '';
          if (exactSnapshot && expectedActiveCommit !== activeCommit) throw new PackOperationError(409, 'DEPLOY_BASELINE_STALE', operationRequestId);
          // A missing wf is a first deployment only if the observed DB baseline
          // also has no active release. This observation is not atomic with Git.
          const missingWfActive = preserveTree && !activeCommit
            ? await this.db.query('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1 ORDER BY id', [workflowId])
            : undefined;
          if (missingWfActive?.length) throw new PackOperationError(409, 'DEPLOY_BASELINE_MISSING', operationRequestId);
          const source = exactSnapshot ? refs.get(savedRef) : refs.get(savedRef) ?? activeCommit;
          if (!source) throw new PackOperationError(409, 'SAVE_REQUIRED', operationRequestId);
          if (input.candidateCommit !== undefined && input.candidateCommit !== source) throw new PackOperationError(409, 'CANDIDATE_NOT_SAVED', operationRequestId);
          if (preserveTree) await this.assertEditorDraftCurrent(row, expectedDraftToken!, operationRequestId);
          const { commit, metadata } = exactSnapshot
            ? { commit: source, metadata: await readPreparedPackMetadata(dir, workflowId, source) }
            : await combineSourceAndYaml(dir, workflowId, source, content, this.author);
          if (exactSnapshot) {
            try {
              const dbDocument: unknown = JSON.parse(projectWorkflowSnapshotJson(workflowId, row.spec_json).specJson);
              if (!compareWorkflowSnapshotYaml(workflowId, metadata.content, dbDocument).matches) {
                throw new PackOperationError(409, 'SNAPSHOT_DB_MISMATCH', operationRequestId);
              }
            } catch (error) {
              if (error instanceof PackOperationError) throw error;
              throw new PackOperationError(409, 'SNAPSHOT_DB_MISMATCH', operationRequestId);
            }
          }
          if (exactSnapshot && metadata.packPath) await assertNoOutsideChanges(dir, activeCommit, source, metadata.packPath, operationRequestId);
          if (preserveTree && activeCommit) await assertDeploymentTreePreserved(dir, activeCommit, commit, operationRequestId, metadata.packPath);
          if ((input.packId && input.packId !== metadata.packId) || (row.pack_id ?? row.workflow_id) !== metadata.packId) throw new PackOperationError(409, 'PACK_ID_MISMATCH', operationRequestId);
          if (preserveTree && (!exactSnapshot || this.authorizeSnapshotDeploy)) await this.assertSnapshotDeployAuthorized({ actorId, workflowId, packId: metadata.packId, candidateCommit: commit });
          let projection: Projection;
          try { projection = candidateProjection(workflowId, exactSnapshot ? metadata.content : content, metadata.manifestContent, preserveTree ? 'strict' : 'legacy-autoheal'); }
          catch (error) {
            // A content-only database wrapper with an invalid embedded YAML is
            // an invalid stored workflow, not a caller-facing identity conflict.
            // Keep the direct projection helper's precise mismatch contract.
            if (error instanceof PackOperationError && error.code === 'WORKFLOW_ID_MISMATCH') {
              throw new PackOperationError(400, 'INVALID_WORKFLOW', operationRequestId);
            }
            throw error;
          }
          await checkFacades(this.db, workflowId, projection.facadeCommands);
          const active = missingWfActive ?? await this.db.query('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1 ORDER BY id', [workflowId]);
          const version = Number(await this.deployHistoryRepo.getLatestVersion(workflowId)) + 1;
          const maxRows = await this.db.query<{ n: number | null }>('SELECT MAX(deploy_number) AS n FROM workflow_deploy_history WHERE workflow_id = ?', [workflowId]);
          const deployNumber = Math.max(Number(maxRows[0]?.n ?? 0), await getMaxDeployNumberFromGitTags(dir, workflowId)) + 1;
          prepared = await prepareDeployCandidate(dir, workflowId, commit, activeCommit, deployNumber, version, this.author);
          if (preserveTree) await this.assertEditorDraftCurrent(row, expectedDraftToken!, operationRequestId);
          if (exactSnapshot) {
            const latest = await readPreparedRemoteRefs(dir, [savedRef, activeRef]);
            if (latest.get(savedRef) !== source) throw new PackOperationError(409, 'CANDIDATE_NOT_SAVED', operationRequestId);
            if ((latest.get(activeRef) ?? '') !== activeCommit) throw new PackOperationError(409, 'DEPLOY_BASELINE_STALE', operationRequestId);
          }
          if (exactSnapshot && !activeCommit) {
            const latestActive = await this.db.query('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1 ORDER BY id', [workflowId]);
            if (latestActive.length) throw new PackOperationError(409, 'DEPLOY_BASELINE_MISSING', operationRequestId);
          }
          // Recheck edit and any configured host candidate policy immediately
          // before publication. The host policy is mandatory for non-v2 only.
          if (preserveTree) {
            if (await this.botPermRepo!.hasEditPermission(workflowId, actorId) !== true) throw new PackOperationError(403, 'FORBIDDEN', operationRequestId);
            if (!exactSnapshot || this.authorizeSnapshotDeploy) await this.assertSnapshotDeployAuthorized({ actorId, workflowId, packId: metadata.packId, candidateCommit: commit });
          }
          pushStarted = true;
          await pushPreparedDeployment(dir, prepared);
          pushConfirmed = true;
          await this.complete(row, active, projection, prepared, actorId, input.note ?? 'auto-heal deploy');
          dbConfirmed = true;
          return { ok: true, deployed: true, workflowId, packId: metadata.packId, deployNumber, version,
            tagName: prepared.tagName, requestId: operationRequestId, commit: prepared.commit, candidateCommit: source,
            ...(exactSnapshot ? { snapshotProtocol: 'saved-tree-v2' as const, expectedActiveCommit } : {}) };
        } finally { await removeTempRepo(dir); }
      });
    } catch (error) {
      if (!pushStarted) throw error;
      const failure = new PackOperationError(503, dbConfirmed ? 'DEPLOY_COMPLETION_UNKNOWN' : pushConfirmed ? 'DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN' : 'DEPLOY_OUTCOME_UNKNOWN',
        operationRequestId, prepared?.branch, prepared?.commit);
      throw Object.assign(failure, { cause: error, tagName: prepared?.tagName });
    }
    if (input.autoRun !== false && this.triggerRun) {
      try { result.messageId = await this.triggerRun(workflowId, actorId, input.sourceFlowId ?? null); }
      catch { result.messageId = null; }
    }
    return result;
  }

  private async assertSnapshotDeployAuthorized(candidate: SnapshotDeployAuthorization): Promise<void> {
    if (!this.authorizeSnapshotDeploy) throw new PackOperationError(403, 'SNAPSHOT_DEPLOY_FORBIDDEN');
    let allowed: boolean;
    try { allowed = await this.authorizeSnapshotDeploy(Object.freeze({ ...candidate })); }
    catch { throw new PackOperationError(503, 'SNAPSHOT_DEPLOY_AUTHORIZATION_UNAVAILABLE'); }
    if (allowed !== true) throw new PackOperationError(403, 'SNAPSHOT_DEPLOY_FORBIDDEN');
  }

  private async assertEditorDraftCurrent(row: WorkflowSpecRow, token: string, requestId: string): Promise<void> {
    const current = await this.specRepo.findByWorkflowId(row.workflow_id);
    if (!current || current.workflow_id !== row.workflow_id || editorDraftToken(current) !== token) {
      throw new PackOperationError(409, 'DRAFT_CONFLICT', requestId);
    }
  }

  private async complete(row: WorkflowSpecRow, active: unknown[], projection: Projection, release: PreparedDeployment, actorId: string, note: string) {
    await this.db.transaction(async tx => {
      const lock = tx.dbType === 'mysql' || tx.dbType === 'zdas' ? ' FOR UPDATE' : '';
      const current = (await tx.query<WorkflowSpecRow>(`SELECT id, workflow_id, pack_id, version, spec_json, gmt_create, gmt_modified, title, owner_id FROM workflow_specs WHERE workflow_id = ?${lock}`, [row.workflow_id]))[0];
      const nowActive = await tx.query(`SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1 ORDER BY id${lock}`, [row.workflow_id]);
      if (!current || JSON.stringify(current) !== JSON.stringify(row) || JSON.stringify(nowActive) !== JSON.stringify(active)) throw new PackOperationError(409, 'BASELINE_STALE');
      const collisions = await tx.query('SELECT id FROM workflow_deploy_history WHERE workflow_id = ? AND (version = ? OR deploy_number = ?)', [row.workflow_id, release.version, release.deployNumber]);
      if (collisions.length) throw new PackOperationError(409, 'DEPLOYMENT_NUMBER_CONFLICT');
      await checkFacades(tx, row.workflow_id, projection.facadeCommands, lock);
      const inserted = await tx.exec(`INSERT INTO workflow_deploy_history
        (pack_id,workflow_id,deploy_number,version,tag_name,action,from_deploy_number,spec_json,note,bot_id,owner_id,is_active,gmt_create,gmt_modified)
        VALUES (?,?,?,?,?,'deploy',NULL,?,?,?,?,0,?,?)`,
      [release.packId, row.workflow_id, release.deployNumber, release.version, release.tagName, projection.specJson, note, actorId, actorId, tx.dialect.now(), tx.dialect.now()]);
      if (inserted.affectedRows !== 1) throw new Error('Release history write failed');
      await tx.exec('UPDATE workflow_deploy_history SET is_active = 0 WHERE workflow_id = ? AND is_active = 1', [row.workflow_id]);
      const remaining = await tx.query<{ n: number }>('SELECT COUNT(*) AS n FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', [row.workflow_id]);
      if (Number(remaining[0]?.n) !== 0) throw new Error('Release deactivation failed');
      const activation = await tx.exec("UPDATE workflow_deploy_history SET is_active = 1 WHERE workflow_id = ? AND pack_id = ? AND deploy_number = ? AND version = ? AND action = 'deploy'", [row.workflow_id, release.packId, release.deployNumber, release.version]);
      if (activation.affectedRows !== 1) throw new Error('Release activation failed');
      const updated = await tx.exec('UPDATE workflow_specs SET version = ?, gmt_modified = ? WHERE workflow_id = ?', [release.version, tx.dialect.now(), row.workflow_id]);
      if (updated.affectedRows !== 1) throw new Error('Workflow version write failed');
      if (projection.facadeCommands !== undefined) {
        await tx.exec('DELETE FROM facade_bindings WHERE workflow_id = ?', [row.workflow_id]);
        const remainingBindings = await tx.query('SELECT command FROM facade_bindings WHERE workflow_id = ?', [row.workflow_id]);
        if (remainingBindings.length) throw new Error('Facade deletion failed');
        for (const command of projection.facadeCommands) {
          const binding = await tx.exec('INSERT INTO facade_bindings (command,workflow_id,pack_id,remark,gmt_create,gmt_modified) VALUES (?,?,?,NULL,?,?)', [command, row.workflow_id, release.packId, tx.dialect.now(), tx.dialect.now()]);
          if (binding.affectedRows !== 1) throw new Error('Facade write failed');
        }
      }
    });
  }
}
