import { randomUUID } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { CallbackCompensationSource } from '../../repositories/callback-compensation-source.js';
import type { RunSnapshot } from '../../repositories/callback-compensation-source.js';
import { CallbackCompensationRepository } from '../../repositories/callback-compensation-repository.js';
import { postAuthorityWithinBudget } from '../../repositories/callback-compensation-authority.js';
import type { BatchRow, ItemRow } from '../../repositories/callback-compensation-repository.js';
import type { HttpCallbackConfigRow } from '../../repositories/http-callback-config-repository.js';
import type { BatchDto, CallbackCompensationServiceApi, CapabilitiesDto, CompensationEvidence, CompensationOptions,
  CreateInput, EvidenceQuery, PreviewDto, PreviewInput, PreviewItemDto } from './types.js';
import { CompensationError } from './types.js';
import { bodyHash, hash, protect, unprotect } from './protection.js';
import { buildPayload, configHash, configReasons, evidenceReasons, receiverDto, redactedPayload, runReasons } from './policy.js';
import { callbackHeaders } from './transport.js';
import { repairAudit } from './audit.js';
export { CompensationError } from './types.js';
export type * from './types.js';

interface FrozenItem {
  itemId: string; workflowId: string; flowId: string; environment: string; actorId: string;
  configId: string; configName: string; configHash: string; snapshotHash: string;
  evidence: CompensationEvidence; deliveryKey: string; rawBody: string; rawBodyHash: string;
}
interface PreviewFrozen { environment: string; workflowId: string; actorId: string; items: FrozenItem[] }
interface BatchMetadata { environment: string; workflowId: string; actorId: string; previewId: string; reason: string }
const TTL = 15 * 60_000;
function nonblank(value: unknown, max = 255): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= max;
}
function duplicate(error: unknown): boolean {
  const e = error as { code?: string; errno?: number };
  return e?.code === 'SQLITE_CONSTRAINT_UNIQUE' || e?.code === 'SQLITE_CONSTRAINT_PRIMARYKEY'
    || e?.code === 'ER_DUP_ENTRY' || e?.errno === 1062;
}
function iso(ms: number): string { return new Date(ms).toISOString(); }

/** No Bot/ClawMind/retry/status writers. Missing external proofs are a deny, never an approval. */
export class CallbackCompensationService implements CallbackCompensationServiceApi {
  private readonly repo: CallbackCompensationRepository;
  private readonly source: CallbackCompensationSource;
  private readonly now: () => number;
  private readonly key?: Buffer;
  private readonly environmentKey: string;
  private activeTick: Promise<void> | null = null;
  private closed = false;
  constructor(private readonly db: IDatabase, private readonly options: CompensationOptions) {
    if (!nonblank(options.environment, 128)) throw new CompensationError('ENVIRONMENT_REQUIRED', 400);
    if (options.protectionKey && options.protectionKey.length !== 32) throw new CompensationError('INVALID_PROTECTION_KEY', 500);
    this.key = options.protectionKey ? Buffer.from(options.protectionKey) : undefined;
    this.now = options.now ?? Date.now;
    this.environmentKey = hash(options.environment);
    this.repo = new CallbackCompensationRepository(db);
    this.source = new CallbackCompensationSource(db);
  }
  private identity(workflowId: string, actorId: string): string {
    if (this.closed) throw new CompensationError('SERVICE_CLOSED', 503);
    if (this.db.dbType === 'noop') throw new CompensationError('DATABASE_UNAVAILABLE', 503);
    if (!nonblank(workflowId) || !nonblank(actorId)) throw new CompensationError('IDENTITY_REQUIRED', 403);
    return hash([this.options.environment, workflowId, actorId]);
  }
  private blockReasons(): string[] {
    return [...(!this.key ? ['PROTECTION_KEY_MISSING'] : []), ...(!this.options.evidence ? ['TRUSTED_EVIDENCE_PROVIDER_MISSING'] : [])];
  }
  async capabilities(workflowId: string): Promise<CapabilitiesDto> {
    this.identity(workflowId, 'capabilities');
    const configs = await this.source.configs(workflowId);
    const blockedReasons = this.blockReasons();
    return { workflowId, environment: this.options.environment, canPreview: true,
      executionEnabled: blockedReasons.length === 0, blockedReasons,
      configs: configs.filter(c => c.workflow_id === workflowId).map(receiverDto) };
  }
  private validateRange(input: PreviewInput): void {
    if (!input || !nonblank(input.configId)) throw new CompensationError('CONFIG_REQUIRED', 400);
    if (input.flowIds !== undefined) {
      if (!Array.isArray(input.flowIds) || input.flowIds.length < 1 || input.flowIds.length > 100
        || !input.flowIds.every(id => nonblank(id)) || new Set(input.flowIds).size !== input.flowIds.length)
        throw new CompensationError('INVALID_FLOW_SELECTION', 400);
      if (input.from !== undefined || input.to !== undefined) throw new CompensationError('AMBIGUOUS_RANGE', 400);
      return;
    }
    const valid = (v: unknown): v is string => typeof v === 'string'
      && /^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(v) && Number.isFinite(Date.parse(v));
    if (!valid(input.from) || !valid(input.to) || Date.parse(input.from) > Date.parse(input.to)
      || Date.parse(input.to) - Date.parse(input.from) > 31 * 86_400_000)
      throw new CompensationError('INVALID_COMPLETION_RANGE', 400);
  }
  private query(snapshot: RunSnapshot, configId: string): EvidenceQuery {
    const completed = this.source.completedAt(snapshot);
    return { environment: this.options.environment, workflowId: snapshot.run.workflow_id, flowId: snapshot.run.flow_id,
      configId, snapshotHash: snapshot.snapshotHash, retryEpoch: snapshot.run.retry_epoch,
      completedAt: completed === null ? '' : iso(completed * 1000), now: iso(this.now()) };
  }
  private async inspect(snapshot: RunSnapshot, config: HttpCallbackConfigRow): Promise<{
    status: PreviewItemDto['status']; reasons: string[]; evidence: CompensationEvidence | null;
  }> {
    const runErrors = runReasons(snapshot, this.options.environment, this.source.completedAt(snapshot));
    if (runErrors.includes('NOT_SUCCEEDED') || runErrors.includes('TRIAL_RUN'))
      return { status: 'ineligible', reasons: runErrors, evidence: null };
    const query = this.query(snapshot, config.config_id);
    const evidence = await this.options.evidence?.resolve(query) ?? null;
    const reasons = [...runErrors, ...configReasons(config), ...this.blockReasons(),
      ...evidenceReasons(evidence, { ...query, now: iso(this.now()) }, config, snapshot.run.current_phase)];
    if (reasons.length) return { status: 'review', reasons, evidence };
    if (!postAuthorityWithinBudget(this.db, snapshot, evidence!))
      return { status: 'review', reasons: ['AUTHORITY_SIZE_REQUIRES_REVIEW'], evidence };
    const attempts = await this.source.attempts(snapshot.run.workflow_id, snapshot.run.flow_id, evidence!);
    if (attempts === 'delivered') return { status: 'delivered', reasons: ['RECEIVER_ALREADY_DELIVERED'], evidence };
    if (attempts === 'review') return { status: 'review', reasons: ['EXISTING_RECEIVER_ATTEMPT'], evidence };
    const deliveryKey = this.deliveryKey(snapshot.run.workflow_id, snapshot.run.flow_id, evidence!);
    if (await this.repo.existingDelivery(deliveryKey)) return { status: 'review', reasons: ['DELIVERY_ALREADY_RESERVED'], evidence };
    return { status: 'eligible', reasons: ['NO_DELIVERY_EVIDENCE_NOT_PROOF_OF_NONDELIVERY'], evidence };
  }
  private deliveryKey(workflowId: string, flowId: string, evidence: CompensationEvidence): string {
    return hash([this.options.environment, workflowId, flowId, evidence.generation, 'workflow_succeeded', evidence.receiverId]);
  }
  async preview(workflowId: string, actorId: string, input: PreviewInput): Promise<PreviewDto> {
    const scope = this.identity(workflowId, actorId); this.validateRange(input);
    const config = await this.source.config(workflowId, input.configId);
    if (!config) throw new CompensationError('CONFIG_NOT_FOUND', 404);
    const flowIds = await this.source.candidates(workflowId, input);
    const previewId = randomUUID(); const frozen: PreviewFrozen = { environment: this.options.environment, workflowId, actorId, items: [] };
    const items: PreviewItemDto[] = [];
    for (const flowId of flowIds) {
      const snapshot = await this.source.snapshot(workflowId, flowId);
      if (!snapshot) throw new CompensationError('RUN_NOT_FOUND_IN_WORKFLOW', 404);
      const verdict = await this.inspect(snapshot, config);
      const itemId = randomUUID();
      items.push({ itemId, flowId, status: verdict.status, reasons: verdict.reasons,
        payload: redactedPayload(snapshot, config.include_node_output === 1) });
      if (verdict.status === 'eligible') {
        const rawBody = JSON.stringify(buildPayload(snapshot, config.include_node_output === 1));
        frozen.items.push({ itemId, workflowId, flowId, environment: this.options.environment, actorId,
          configId: config.config_id, configName: config.name, configHash: configHash(config), snapshotHash: snapshot.snapshotHash,
          evidence: verdict.evidence!, deliveryKey: this.deliveryKey(workflowId, flowId, verdict.evidence!), rawBody, rawBodyHash: bodyHash(rawBody) });
      }
    }
    const dto: PreviewDto = { previewId, workflowId, environment: this.options.environment, receiver: receiverDto(config),
      expiresAt: iso(this.now() + TTL), items, summary: { eligible: 0, delivered: 0, review: 0, ineligible: 0 } };
    for (const item of items) dto.summary[item.status]++;
    await this.repo.insertPreview({ preview_id: previewId, scope_key: scope, expires_ms: Date.parse(dto.expiresAt),
      dto_json: JSON.stringify(dto), frozen_cipher: this.key ? protect(frozen, this.key, `preview:${scope}:${previewId}`) : null });
    return dto;
  }
  private async checkFrozen(f: FrozenItem, source = this.source): Promise<{ config?: HttpCallbackConfigRow; snapshot?: RunSnapshot; reason?: string }> {
    const snapshot = await source.snapshot(f.workflowId, f.flowId);
    if (!snapshot) return { reason: 'RUN_MISSING' };
    if (snapshot.snapshotHash !== f.snapshotHash || runReasons(snapshot, this.options.environment, source.completedAt(snapshot)).length)
      return { reason: 'RUN_SNAPSHOT_DRIFT' };
    const config = await source.config(f.workflowId, f.configId);
    if (!config || configHash(config) !== f.configHash || configReasons(config).length) return { reason: 'CONFIG_DRIFT' };
    const query = this.query(snapshot, config.config_id);
    const evidence = await this.options.evidence?.resolve(query) ?? null;
    if (evidenceReasons(evidence, { ...query, now: iso(this.now()) }, config, snapshot.run.current_phase).length || hash(evidence) !== hash(f.evidence))
      return { reason: 'EVIDENCE_DRIFT_OR_EXPIRED' };
    const attempts = await source.attempts(f.workflowId, f.flowId, evidence!);
    if (attempts) return { reason: attempts === 'delivered' ? 'RECEIVER_ALREADY_DELIVERED' : 'EXISTING_RECEIVER_ATTEMPT' };
    // A trusted provider may still be slow. Reread authority AFTER all external evidence
    // and history awaits; never let a stale preflight authorize a changed current run/config.
    const latest = await source.snapshot(f.workflowId, f.flowId);
    if (!latest || latest.snapshotHash !== f.snapshotHash) return { reason: 'RUN_SNAPSHOT_DRIFT' };
    const latestConfig = await source.config(f.workflowId, f.configId);
    if (!latestConfig || configHash(latestConfig) !== f.configHash) return { reason: 'CONFIG_DRIFT' };
    if (f.environment !== this.options.environment || bodyHash(f.rawBody) !== f.rawBodyHash
      || this.deliveryKey(f.workflowId, f.flowId, evidence!) !== f.deliveryKey) return { reason: 'FROZEN_SNAPSHOT_INVALID' };
    if (!postAuthorityWithinBudget(this.db, latest, evidence!)) return { reason: 'AUTHORITY_SIZE_REQUIRES_REVIEW' };
    return { config: latestConfig, snapshot: latest };
  }
  async create(workflowId: string, actorId: string, input: CreateInput): Promise<BatchDto> {
    const scope = this.identity(workflowId, actorId);
    if (!input || !nonblank(input.reason, 500) || input.reason.trim().length < 10) throw new CompensationError('REASON_REQUIRED_10_500', 400);
    if (typeof input.requestKey !== 'string' || input.requestKey.length < 8 || input.requestKey.length > 128)
      throw new CompensationError('INVALID_REQUEST_KEY', 400);
    if (!nonblank(input.previewId, 64) || !Array.isArray(input.itemIds) || input.itemIds.length < 1 || input.itemIds.length > 100
      || !input.itemIds.every(id => nonblank(id, 64)) || new Set(input.itemIds).size !== input.itemIds.length)
      throw new CompensationError('INVALID_ITEM_SELECTION', 400);
    const requestKey = hash([scope, input.requestKey]);
    const requestHash = hash({ previewId: input.previewId, itemIds: [...input.itemIds].sort(), reason: input.reason.trim() });
    const replay = async (): Promise<BatchDto | null> => {
      const old = await this.repo.request(requestKey);
      if (!old) return null;
      if (old.scope_key !== scope || old.request_hash !== requestHash) throw new CompensationError('REQUEST_KEY_CONFLICT', 409);
      return this.get(workflowId, actorId, old.batch_id);
    };
    const old = await replay(); if (old) return old;
    if (this.blockReasons().length) throw new CompensationError('EXECUTION_DISABLED', 409);
    const preview = await this.repo.preview(input.previewId);
    if (!preview || preview.scope_key !== scope) throw new CompensationError('PREVIEW_NOT_FOUND', 404);
    if (Number(preview.expires_ms) <= this.now()) throw new CompensationError('PREVIEW_EXPIRED', 409);
    if (!preview.frozen_cipher) throw new CompensationError('EXECUTION_DISABLED', 409);
    const frozen = unprotect<PreviewFrozen>(preview.frozen_cipher, this.key!, `preview:${scope}:${input.previewId}`);
    if (frozen.environment !== this.options.environment || frozen.workflowId !== workflowId || frozen.actorId !== actorId)
      throw new CompensationError('PREVIEW_NOT_FOUND', 404);
    const selected = input.itemIds.map(id => frozen.items.find(i => i.itemId === id));
    if (selected.some(i => !i)) throw new CompensationError('SELECTION_NOT_ELIGIBLE', 409);
    const batchId = randomUUID();
    try {
      await this.db.transaction(async tx => {
        const repo = new CallbackCompensationRepository(tx); const source = new CallbackCompensationSource(tx);
        for (const f of selected) {
          const checked = await this.checkFrozen(f!, source);
          if (checked.reason) throw new CompensationError(checked.reason, 409);
          if (await repo.existingDelivery(f!.deliveryKey)) throw new CompensationError('DELIVERY_ALREADY_RESERVED', 409);
        }
        await repo.insertBatch({ batch_id: batchId, environment_key: this.environmentKey, scope_key: scope,
          request_key: requestKey, request_hash: requestHash, stopped: 0,
          metadata_json: JSON.stringify({ environment: this.options.environment, workflowId, actorId,
            previewId: input.previewId, reason: input.reason.trim() } satisfies BatchMetadata) });
        for (const f of selected) await repo.insertItem({ item_id: f!.itemId, batch_id: batchId,
          environment_key: this.environmentKey, delivery_key: f!.deliveryKey, flow_id: f!.flowId,
          frozen_cipher: protect(f, this.key!, `item:${scope}:${batchId}:${f!.itemId}`) });
      });
    } catch (error) {
      // A competing request can win between the pre-read and transaction/unique insert.
      if (duplicate(error) || (error instanceof CompensationError && error.code === 'DELIVERY_ALREADY_RESERVED')) {
        const winner = await replay(); if (winner) return winner;
        if (duplicate(error)) throw new CompensationError('DELIVERY_ALREADY_RESERVED', 409);
      }
      throw error;
    }
    return this.get(workflowId, actorId, batchId);
  }
  private async owned(workflowId: string, actorId: string, batchId: string): Promise<BatchRow> {
    const scope = this.identity(workflowId, actorId);
    const row = await this.repo.batch(batchId);
    if (!row || row.batch_id !== batchId || row.scope_key !== scope || row.environment_key !== this.environmentKey)
      throw new CompensationError('BATCH_NOT_FOUND', 404);
    const metadata: BatchMetadata = JSON.parse(row.metadata_json);
    if (metadata.workflowId !== workflowId || metadata.actorId !== actorId || metadata.environment !== this.options.environment)
      throw new CompensationError('BATCH_NOT_FOUND', 404);
    return row;
  }
  async get(workflowId: string, actorId: string, batchId: string): Promise<BatchDto> {
    const row = await this.owned(workflowId, actorId, batchId);
    const items = (await this.repo.items(batchId)).map(i => ({ itemId: i.item_id, flowId: i.flow_id, state: i.state,
      ...(i.reason ? { reason: i.reason } : {}), ...(i.http_status != null ? { httpStatus: i.http_status } : {}), auditPending: i.audit_pending === 1 }));
    const counts: Record<string, number> = { queued: 0, sending: 0, received: 0, failed: 0, unknown: 0, skipped: 0, auditPending: 0 };
    for (const item of items) { counts[item.state]++; if (item.auditPending) counts.auditPending++; }
    const status = row.stopped ? 'stopped' : counts.sending ? 'running' : counts.queued ? 'queued' : 'completed';
    return { batchId, workflowId, environment: this.options.environment, status, items, counts };
  }
  async stop(workflowId: string, actorId: string, batchId: string): Promise<BatchDto> {
    await this.owned(workflowId, actorId, batchId);
    await this.db.transaction(tx => new CallbackCompensationRepository(tx).stop(batchId));
    return this.get(workflowId, actorId, batchId);
  }
  tick(): Promise<void> {
    if (this.closed) return Promise.resolve();
    if (this.activeTick) return this.activeTick;
    this.activeTick = this.runTick().finally(() => { this.activeTick = null; });
    return this.activeTick;
  }
  private async runTick(): Promise<void> {
    if (this.db.dbType === 'noop') throw new CompensationError('DATABASE_UNAVAILABLE', 503);
    await this.repo.recover(this.environmentKey, this.now());
    const rows = await this.repo.work(this.environmentKey);
    for (const row of rows) {
      if (this.closed) break;
      const batch = await this.repo.batch(row.batch_id);
      if (!batch || batch.environment_key !== this.environmentKey) throw new Error('Compensation batch ownership missing');
      if (!this.key) continue;
      const f = unprotect<FrozenItem>(row.frozen_cipher, this.key, `item:${batch.scope_key}:${batch.batch_id}:${row.item_id}`);
      if (hash([f.environment, f.workflowId, f.actorId]) !== batch.scope_key || f.environment !== this.options.environment
        || f.itemId !== row.item_id || f.flowId !== row.flow_id || f.deliveryKey !== row.delivery_key)
        throw new CompensationError('FROZEN_OWNERSHIP_INVALID', 409);
      if (row.audit_pending === 1) { await this.audit(row, f); continue; }
      const claimUntil = this.now() + 60_000;
      if (!await this.repo.claim(row.item_id, claimUntil)) continue;
      const checked = await this.checkFrozen(f);
      const timeoutMs = Math.min(30_000, Math.max(1, Number.isFinite(checked.config?.timeout_ms) ? checked.config!.timeout_ms : 5000));
      const checkedAt = this.now();
      const postUntil = Math.max(claimUntil + 1, checkedAt + timeoutMs + 30_000);
      // Provider/DB preflight can outlive the claim. Never POST after another instance
      // recovered unknown, after expiry, or after stop while preflight was pending.
      const authorized = checkedAt < claimUntil && await this.repo.authorizePost(row.item_id, claimUntil, checkedAt, postUntil,
        checked.snapshot && checked.config && !checked.reason ? { snapshot: checked.snapshot, config: checked.config, evidence: f.evidence } : null);
      if (!authorized || this.now() >= claimUntil) {
        await this.repo.abandonBeforePost(row.item_id, claimUntil, this.now(), checked.reason);
        continue;
      }
      const sendNow = this.now();
      if (Date.parse(f.evidence.validFrom) > sendNow || Date.parse(f.evidence.validUntil) <= sendNow
        || Date.parse(f.evidence.historicalRoute.validUntil) < sendNow) {
        await this.repo.settle(row.item_id, 'skipped', 'EVIDENCE_DRIFT_OR_EXPIRED', null, false);
        continue;
      }
      if (checked.reason) { await this.repo.settle(row.item_id, 'skipped', checked.reason, null, false); continue; }
      const config = checked.config!;
      const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
      let status: number | null = null;
      try {
        const response = await Promise.race([
          this.options.transport({ url: f.evidence.url, rawBody: f.rawBody,
            headers: callbackHeaders(config.config_id, config.secret, f.rawBody, this.now()),
            timeoutMs, redirect: 'error', signal: controller.signal }),
          new Promise<never>((_, reject) => { timer = setTimeout(() => { controller.abort(); reject(new Error('Timeout')); }, timeoutMs); }),
        ]);
        status = Number.isInteger(response.status) && response.status >= 100 && response.status <= 599 ? response.status : null;
      } catch { /* Sent or possibly sent: persist unknown, never capture raw transport errors. */ }
      finally { clearTimeout(timer!); }
      const received = status !== null && status >= 200 && status < 300;
      // Do not catch persistence failure and then POST again. Lease recovery handles unknown.
      await this.repo.settle(row.item_id, received ? 'received' : 'unknown',
        received ? 'HTTP_RECEIVED_NOT_BUSINESS_CONFIRMATION' : 'HTTP_OUTCOME_UNKNOWN', status, true);
      await this.audit(row, f);
    }
  }
  private async audit(row: ItemRow, f: FrozenItem): Promise<void> {
    // Failure deliberately propagates to bootstrap; auditPending remains durable for next tick.
    await repairAudit(this.db, row.item_id, { workflowId: f.workflowId, flowId: f.flowId,
      configId: f.configId, configName: f.configName, url: f.evidence.url });
  }
  async close(): Promise<void> {
    this.closed = true;
    try { await this.activeTick; } finally { this.key?.fill(0); }
  }
  shutdown(): Promise<void> { return this.close(); }
}
