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

export type AutoHealDeployInput = { workflowId: string; actorId: string; candidateCommit?: string; requestId?: string;
  packId?: string | null; diagnosisId?: string | null; sourceFlowId?: string | null; note?: string | null; autoRun?: boolean };
export type AutoHealDeployResult = { ok: boolean; deployed: boolean; workflowId: string; packId: string; deployNumber: number;
  version: number; tagName: string; requestId?: string; commit?: string; candidateCommit?: string; messageId?: string | null };

type Projection = ReturnType<typeof candidateProjection>;
function workflowYaml(row: WorkflowSpecRow): string {
  try {
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

async function checkFacades(db: IDatabase, workflowId: string, commands: string[] | undefined, lock = '') {
  for (const command of commands ?? []) {
    const owners = await db.query<{ workflow_id: string }>(`SELECT workflow_id FROM facade_bindings WHERE command = ?${lock}`, [command]);
    if (owners.some(owner => owner.workflow_id !== workflowId)) throw new PackOperationError(409, 'FACADE_CONFLICT');
  }
}

export class AutoHealDeployService {
  constructor(private db: IDatabase, private specRepo: WorkflowSpecRepository, private deployHistoryRepo: WorkflowDeployHistoryRepository,
    private botPermRepo: BotWorkflowPermissionRepository | null, private gitConfig: ClawFlowsGitConfig, private author: GitAuthor,
    private triggerRun?: (workflowId: string, actorId: string, sourceFlowId?: string | null) => Promise<string | null>) {}

  async deploy(input: AutoHealDeployInput): Promise<AutoHealDeployResult> {
    const { workflowId, actorId } = input;
    if (!validPackGitId(workflowId) || /^__.+__$/.test(workflowId)
      || (input.candidateCommit !== undefined && !gitOidPattern.test(input.candidateCommit))) throw new PackOperationError(400, 'INVALID_REQUEST');
    if (!actorId?.trim() || !this.botPermRepo) throw new PackOperationError(403, 'FORBIDDEN');
    const [canExecute, canEdit] = await Promise.all([this.botPermRepo.hasExecutePermission(workflowId, actorId), this.botPermRepo.hasEditPermission(workflowId, actorId)]);
    if (!canExecute && !canEdit) throw new PackOperationError(403, 'FORBIDDEN');
    const row = await this.specRepo.findByWorkflowId(workflowId);
    if (!row || row.workflow_id !== workflowId) throw new PackOperationError(404, 'WORKFLOW_NOT_FOUND');
    await assertNoUnresolvedLegacyPackOperation(this.db, workflowId);
    const requestId = requestIdentity(input.requestId, randomUUID);
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
          const source = refs.get(savedRef) ?? activeCommit;
          if (!source) throw new PackOperationError(409, 'SAVE_REQUIRED', requestId);
          if (input.candidateCommit !== undefined && input.candidateCommit !== source) throw new PackOperationError(409, 'CANDIDATE_NOT_SAVED', requestId);
          const { commit, metadata } = await combineSourceAndYaml(dir, workflowId, source, content, this.author);
          if ((input.packId && input.packId !== metadata.packId) || (row.pack_id ?? row.workflow_id) !== metadata.packId) throw new PackOperationError(409, 'PACK_ID_MISMATCH', requestId);
          const projection = candidateProjection(workflowId, content, metadata.manifestContent);
          await checkFacades(this.db, workflowId, projection.facadeCommands);
          const active = await this.db.query('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1 ORDER BY id', [workflowId]);
          const version = Number(await this.deployHistoryRepo.getLatestVersion(workflowId)) + 1;
          const maxRows = await this.db.query<{ n: number | null }>('SELECT MAX(deploy_number) AS n FROM workflow_deploy_history WHERE workflow_id = ?', [workflowId]);
          const deployNumber = Math.max(Number(maxRows[0]?.n ?? 0), await getMaxDeployNumberFromGitTags(dir, workflowId)) + 1;
          prepared = await prepareDeployCandidate(dir, workflowId, commit, activeCommit, deployNumber, version, this.author);
          pushStarted = true;
          await pushPreparedDeployment(dir, prepared);
          pushConfirmed = true;
          await this.complete(row, active, projection, prepared, actorId, input.note ?? 'auto-heal deploy');
          dbConfirmed = true;
          return { ok: true, deployed: true, workflowId, packId: metadata.packId, deployNumber, version,
            tagName: prepared.tagName, requestId, commit: prepared.commit, candidateCommit: source };
        } finally { await removeTempRepo(dir); }
      });
    } catch (error) {
      if (!pushStarted) throw error;
      const failure = new PackOperationError(503, dbConfirmed ? 'DEPLOY_COMPLETION_UNKNOWN' : pushConfirmed ? 'DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN' : 'DEPLOY_OUTCOME_UNKNOWN',
        requestId, prepared?.branch, prepared?.commit);
      throw Object.assign(failure, { cause: error, tagName: prepared?.tagName });
    }
    if (input.autoRun !== false && this.triggerRun) {
      try { result.messageId = await this.triggerRun(workflowId, actorId, input.sourceFlowId ?? null); }
      catch { result.messageId = null; }
    }
    return result;
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
