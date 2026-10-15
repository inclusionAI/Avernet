import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { RepairBatchError, boundedRepairJson, MAX_RESULT_BYTES } from '../contracts/repair-batch.js';
import type { RepairDeployment, PublicationReceipt } from '../contracts/repair-release.js';
import { RepairBatchRepository } from '../repositories/repair-batch-repository.js';

export type PublicationRecipe = { packId: string; specPath: string; specDigest: string; deployNumber: number; releaseVersion: number; tagName: string };
type ClaimRequest = { deploymentId: string; claimToken: string; botId: string; ownerId: string; workerId: string };
type ClaimRow = { ticket_hash: string; recipe_json: string; worker_id: string | null; expires_at_ms: number; reported_status: string | null; receipt_json: string | null };
function fail(message: string): never { throw new RepairBatchError('STATE_CONFLICT', message); }
function hash(token: string): string { return createHash('sha256').update(token).digest('hex'); }
function ticketMatches(row: ClaimRow, token: unknown): boolean {
  if (typeof token !== 'string' || token.length !== 64 || !/^[a-f0-9]{64}$/.test(row.ticket_hash)) return false;
  return timingSafeEqual(Buffer.from(row.ticket_hash, 'hex'), Buffer.from(hash(token), 'hex'));
}

/** Trusted Host issues a capability after portal authorization; signed runtime
 * endpoints additionally bind it to one frozen target and one worker. */
export function createRepairPublicationClaimService(db: IDatabase) {
  async function read(deploymentId: string, connection = db) {
    const record = (await connection.query<{ body_json: string }>(
      "SELECT body_json FROM workflow_repair_release_records WHERE record_id = ? AND record_kind = 'deployment'", [deploymentId]))[0];
    if (!record) fail('发布请求不存在');
    const deployment = JSON.parse(record.body_json) as RepairDeployment;
    const row = (await connection.query<ClaimRow>('SELECT * FROM workflow_repair_publication_claims WHERE deployment_id = ?', [deploymentId]))[0];
    return { deployment, row, recipe: row ? JSON.parse(row.recipe_json) as PublicationRecipe : null };
  }
  return {
    async prepare(deployment: RepairDeployment, recipe: PublicationRecipe): Promise<string> {
      if (!Number.isSafeInteger(recipe.deployNumber) || recipe.deployNumber <= deployment.baselineDeployNumber
        || !Number.isSafeInteger(recipe.releaseVersion) || recipe.releaseVersion < 1
        || recipe.tagName !== `deploy/${deployment.workflowId}/#${recipe.deployNumber}` || !/^[a-f0-9]{64}$/.test(recipe.specDigest)
        || recipe.packId !== deployment.baseline.packId) fail('冻结发布编号或候选 Pack 不一致');
      const nonce = randomBytes(32).toString('hex');
      await db.transaction(async tx => {
        await new RepairBatchRepository(tx).lockWorkflow(tx, deployment.workflowId);
        const current = await read(deployment.id, tx);
        if (current.deployment.candidateCommit !== deployment.candidateCommit || current.deployment.target.id !== deployment.target.id
          || !['dispatching', 'running', 'uncertain'].includes(current.deployment.status)) fail('发布请求身份已变化');
        if (current.row) fail('发布执行凭证已签发，请核对同一请求，不可重新派发');
        await tx.exec(`INSERT INTO workflow_repair_publication_claims (deployment_id, ticket_hash, recipe_json, expires_at_ms)
          VALUES (?, ?, ?, ?)`, [deployment.id, hash(nonce), boundedRepairJson(recipe, MAX_RESULT_BYTES), Date.now() + 15 * 60_000]);
      });
      return nonce;
    },
    async reissue(deployment: RepairDeployment): Promise<string> {
      return db.transaction(async tx => {
        await new RepairBatchRepository(tx).lockWorkflow(tx, deployment.workflowId);
        const state = await read(deployment.id, tx);
        if (!state.row || !state.recipe || state.row.reported_status === 'cancelled' || !['dispatching', 'running', 'uncertain'].includes(state.deployment.status)
          || state.deployment.candidateCommit !== deployment.candidateCommit
          || !(state.row.reported_status === 'uncertain' || state.row.expires_at_ms <= Date.now())) fail('当前工作进程尚未结束或授权未到期，请先核对，不重复发布');
        const nonce = randomBytes(32).toString('hex');
        await tx.exec(`UPDATE workflow_repair_publication_claims SET ticket_hash = ?, worker_id = NULL,
          expires_at_ms = ?, reported_status = NULL, receipt_json = NULL WHERE deployment_id = ?`, [hash(nonce), Date.now() + 15 * 60_000, deployment.id]);
        return nonce;
      });
    },
    async stop(deployment: RepairDeployment): Promise<void> {
      await db.transaction(async tx => {
        await new RepairBatchRepository(tx).lockWorkflow(tx, deployment.workflowId);
        const state = await read(deployment.id, tx);
        if (state.deployment.status === 'succeeded') fail('此候选已部署，不能停止或隐式回滚');
        if ((await tx.query('SELECT 1 FROM workflow_deploy_history WHERE workflow_id = ? AND note = ? LIMIT 1',
          [deployment.workflowId, `workflow-repair-deployment:${deployment.id}`])).length) fail('真实发布记录已存在，请核对状态，不能停止或回滚');
        if (state.row) await tx.exec("UPDATE workflow_repair_publication_claims SET expires_at_ms = 0, reported_status = 'cancelled' WHERE deployment_id = ?", [deployment.id]);
        else await tx.exec(`INSERT INTO workflow_repair_publication_claims (deployment_id, ticket_hash, recipe_json, expires_at_ms, reported_status)
          VALUES (?, ?, 'null', 0, 'cancelled')`, [deployment.id, hash(randomBytes(32).toString('hex'))]);
        // The activation guard and capability rotation use the same workflow
        // lock. A live/late worker cannot activate after this fenced stop.
      });
    },
    async claim(request: ClaimRequest) {
      return db.transaction(async tx => {
        const { deployment, row, recipe } = await read(request.deploymentId, tx);
        if (!row || !recipe || !ticketMatches(row, request.claimToken) || row.expires_at_ms <= Date.now()
          || !['dispatching', 'running', 'uncertain'].includes(deployment.status)
          || request.botId !== deployment.target.botId || request.ownerId !== deployment.target.ownerId
          || typeof request.workerId !== 'string' || !request.workerId.trim() || request.workerId.length > 128) fail('发布凭证、目标 Bot 或执行身份无效');
        const claimed = await tx.exec(`UPDATE workflow_repair_publication_claims SET worker_id = ?
          WHERE deployment_id = ? AND worker_id IS NULL AND ticket_hash = ? AND expires_at_ms > ?`,
        [request.workerId, request.deploymentId, row.ticket_hash, Date.now()]);
        if (claimed.affectedRows !== 1) fail('此发布请求已被工作进程领取；请核对状态，不重复发布');
        return { schemaVersion: 'workflow-repair-publication/v1' as const, deploymentId: deployment.id, requestId: deployment.requestId,
          workflowId: deployment.workflowId, packId: recipe.packId, specPath: recipe.specPath, specDigest: recipe.specDigest,
          candidateCommit: deployment.candidateCommit, packDigest: deployment.packDigest,
          baselineDeployNumber: deployment.baselineDeployNumber, target: deployment.target,
          recipe: { deployNumber: recipe.deployNumber, releaseVersion: recipe.releaseVersion, tagName: recipe.tagName } };
      });
    },
    async authorizeHistory(body: Record<string, unknown>): Promise<boolean> {
      const id = typeof body.note === 'string' ? /^workflow-repair-deployment:([A-Za-z0-9_-]+)$/.exec(body.note)?.[1] : null;
      if (!id) return false;
      const { deployment, row, recipe } = await read(id);
      return !!row && !!recipe && !!row.worker_id && row.expires_at_ms > Date.now() && ticketMatches(row, body.publicationTicket)
        && ['dispatching', 'running', 'uncertain'].includes(deployment.status)
        && body.workflowId === deployment.workflowId && body.packId === recipe.packId
        && body.expectedActiveDeployNumber === deployment.baselineDeployNumber && body.botId === deployment.target.botId
        && body.ownerId === deployment.target.ownerId && body.deployNumber === recipe.deployNumber
        && body.version === recipe.releaseVersion && body.tagName === recipe.tagName;
    },
    async report(request: ClaimRequest & { status: 'succeeded' | 'uncertain'; receipt?: PublicationReceipt }) {
      const { row, deployment } = await read(request.deploymentId);
      if (!row || !ticketMatches(row, request.claimToken) || row.worker_id !== request.workerId
        || request.botId !== deployment.target.botId || request.ownerId !== deployment.target.ownerId
        || !['succeeded', 'uncertain'].includes(request.status)) fail('发布回执身份无效');
      await db.exec('UPDATE workflow_repair_publication_claims SET reported_status = ?, receipt_json = ? WHERE deployment_id = ? AND worker_id = ?',
        [request.status, request.receipt ? boundedRepairJson(request.receipt, MAX_RESULT_BYTES) : null, request.deploymentId, request.workerId]);
    },
    async inspect(deploymentId: string) {
      const { deployment, row, recipe } = await read(deploymentId);
      return { deployment, recipe, claimed: !!row?.worker_id, expired: !!row && row.expires_at_ms <= Date.now(), reportedStatus: row?.reported_status ?? null };
    },
    async revokeUnclaimed(deploymentId: string): Promise<boolean> {
      const result = await db.exec('UPDATE workflow_repair_publication_claims SET expires_at_ms = ? WHERE deployment_id = ? AND worker_id IS NULL', [Date.now() - 1, deploymentId]);
      return result.affectedRows === 1;
    },
  };
}
