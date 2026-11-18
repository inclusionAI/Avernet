import { authoringRecord, authoringSpec, workflowAuthoringSignature } from '@/domain/taskEscortParity/workflowAuthoring';
import { parseLosslessAuthoringText } from '@/domain/taskEscortParity/workflowAuthoringNumbers';
import { authoringCommit, authoringFileChanges, authoringSnapshot, authoringToken, sameAuthoringExecution, workflowFromAuthoringPack } from '@/domain/taskEscortParity/workflowAuthoringPack';
import { WorkflowAuthoringOutcomeError, type WorkflowAuthoringBaseline, type WorkflowAuthoringIdentity,
  type WorkflowAuthoringSnapshot, type WorkflowAuthoringSpec, type WorkflowCreateFromSpecInput,
  type WorkflowPublishInput, type WorkflowPublishResult, type WorkflowPublishReview, type WorkflowSaveInput,
  type WorkflowSaveResult } from '@/domain/taskEscortParity/workflowAuthoringTypes';
import type { WorkflowAuthoringController } from '@/services/backendApi/harnessFlow/workflowAuthoringController';

interface BaselineEvidence { snapshot: WorkflowAuthoringSnapshot; serialized: string; epoch: number }
export function createWorkflowAuthoringPersistence(api: WorkflowAuthoringController,
  checkIdentity: (params: WorkflowAuthoringIdentity) => string,
  validate: (spec: WorkflowAuthoringSpec, params: WorkflowAuthoringIdentity, signal?: AbortSignal) => Promise<WorkflowAuthoringSpec>,
  editAccess: (id: string, params: WorkflowAuthoringIdentity, signal?: AbortSignal) => Promise<void>) {
  const baselines = new WeakMap<WorkflowAuthoringBaseline, BaselineEvidence>();
  const receipts = new WeakSet<WorkflowSaveResult>();
  const reviews = new WeakMap<WorkflowPublishReview, { receipt: WorkflowSaveResult; serialized: string }>();
  const epochs = new Map<string, number>(), locks = new Set<string>();
  const creations = new Map<string, { requestId: string; pending?: boolean; completed?: boolean; confirmed?: { commit: string; spec: WorkflowAuthoringSpec; packId: string } }>();
  const key = (id: string, user: string) => JSON.stringify([id, user]);
  function assertOpen(id: string, user: string) { if (locks.has(key(id, user))) throw new Error('上次写入结果未确认；请人工核对，勿盲目重试'); }
  function assertBaseline(base: WorkflowAuthoringBaseline, params: WorkflowAuthoringIdentity) {
    const evidence = base && baselines.get(base), user = checkIdentity(params);
    if (!evidence || base.userId !== user || evidence.serialized !== JSON.stringify(base) || evidence.epoch !== (epochs.get(key(base.workflowId, user)) ?? 0)) throw new Error('保存基线缺失、已修改或已过期');
    assertOpen(base.workflowId, user); return evidence;
  }
  function draft(value: unknown, id: string) {
    const row = authoringRecord(value);
    if (row.workflowId !== id || !(row.packId === null || typeof row.packId === 'string') || typeof row.specJson !== 'string') throw new Error('数据库草稿身份或结构无效');
    const revision = authoringToken(row.draftToken);
    parseLosslessAuthoringText(row.specJson);
    const spec = authoringSpec(JSON.parse(row.specJson), id);
    return { packId: row.packId, specJson: row.specJson, revision, spec };
  }
  function bind(id: string, user: string, row: ReturnType<typeof draft>, snapshot: WorkflowAuthoringSnapshot, version: 'draft' | 'current'): WorkflowAuthoringBaseline {
    if (snapshot.packId !== (row.packId ?? id)) throw new Error('数据库与 Git Pack 身份不一致');
    const baseline: WorkflowAuthoringBaseline = Object.freeze({ workflowId: id, userId: user, packId: snapshot.packId,
      rawPackId: row.packId, draftToken: row.revision, spec: row.spec, specJson: row.specJson, sourceVersion: version,
      sourceCommit: snapshot.baseCommit, expectedSavedHead: snapshot.draftHead });
    baselines.set(baseline, { snapshot, serialized: JSON.stringify(baseline), epoch: epochs.get(key(id, user)) ?? 0 });
    return baseline;
  }
  async function readBaseline(id: string, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowAuthoringBaseline> {
    const user = checkIdentity(params); assertOpen(id, user); signal?.throwIfAborted();
    await editAccess(id, params, signal);
    const row = draft(await api.draft(id, params, signal), id); signal?.throwIfAborted();
    let snapshot: WorkflowAuthoringSnapshot, version: 'draft' | 'current' = 'draft';
    try { snapshot = authoringSnapshot(await api.packVersion(id, 'draft', params, signal), id, 'draft'); }
    catch (error) {
      const e = error as { status?: number; data?: { code?: string; error?: string } };
      if (e.status !== 404 || e.data?.code !== 'SNAPSHOT_NOT_FOUND' || e.data.error !== 'Snapshot not found') throw error;
      version = 'current'; snapshot = authoringSnapshot(await api.packVersion(id, 'current', params, signal), id, 'current');
      if (snapshot.draftHead !== null) throw new Error('首次草稿 Git 基线不一致');
    }
    signal?.throwIfAborted();
    if (!sameAuthoringExecution(workflowFromAuthoringPack(snapshot).spec, row.spec)) throw new Error('数据库与 Git 工作流内容不一致，请先核对');
    return bind(id, user, row, snapshot, version);
  }
  async function postflight(id: string, user: string, params: WorkflowAuthoringIdentity, requested: WorkflowAuthoringSpec,
    packId: string, commit: string, revision?: string, signal?: AbortSignal, rawPackId: string | null = packId): Promise<WorkflowAuthoringBaseline> {
    const [rawDraft, rawPack] = await Promise.all([api.draft(id, params, signal), api.packVersion(id, 'draft', params, signal)]);
    signal?.throwIfAborted();
    const row = draft(rawDraft, id), snapshot = authoringSnapshot(rawPack, id, 'draft');
    if (row.packId !== rawPackId || (row.packId ?? id) !== packId || snapshot.packId !== packId || snapshot.baseCommit !== commit || snapshot.draftHead !== commit ||
      revision !== undefined && row.revision !== revision || workflowAuthoringSignature(requested) !== workflowAuthoringSignature(row.spec) ||
      !sameAuthoringExecution(workflowFromAuthoringPack(snapshot).spec, requested)) throw new Error('写入后数据库或 Git 快照与提交不一致');
    return bind(id, user, row, snapshot, 'draft');
  }
  function recognizedRejection(cause: unknown): boolean {
    const e = cause as { status?: number; data?: Record<string, unknown> };
    const code = e.data?.code;
    return e.status === 409 && ['WORKFLOW_ALREADY_EXISTS', 'TARGET_WORKFLOW_ALREADY_EXISTS', 'FACADE_COMMAND_CONFLICT'].includes(String(code)) ||
      e.status === 403 && ['FORBIDDEN', 'CSRF_REJECTED', 'ORIGIN_NOT_ALLOWED'].includes(String(code)) ||
      e.status === 400 && ['INVALID_REQUEST', 'INVALID_WORKFLOW', 'WORKFLOW_ID_MISMATCH', 'UNSUPPORTED_GIT_PRECONDITION'].includes(String(code));
  }
  return {
    readBaseline,
    async create(input: WorkflowCreateFromSpecInput, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowAuthoringSpec> {
      const user = checkIdentity(params), id = input.workflowId, scope = key(id, user); signal?.throwIfAborted();
      const known = creations.get(scope);
      if (known?.completed) throw new Error('工作流已创建，请从列表打开，不要重复创建');
      if (known?.pending) throw new Error('新建正在进行，请等待');
      if (locks.has(scope) && !known?.confirmed) throw new WorkflowAuthoringOutcomeError('create', id, known?.requestId ?? '', new Error('上次新建结果未知'));
      const spec = await validate(authoringSpec({ ...input.spec, ...(input.title === undefined ? {} : { title: input.title }),
        ...(input.facade ? { facade: input.facade } : {}) }, id), params, signal);
      const attempt = known ?? { requestId: crypto.randomUUID() };
      if (attempt.confirmed && workflowAuthoringSignature(attempt.confirmed.spec) !== workflowAuthoringSignature(spec)) throw new Error('已确认的新建请求不能替换文档；只能重读原快照');
      attempt.pending = true; creations.set(scope, attempt); locks.add(scope);
      try {
        if (!attempt.confirmed) {
          const packId = input.packId ?? id;
          const row = authoringRecord(await api.create({ workflowId: id, packId, spec, createOnly: true,
            expectedSavedHead: null, requestId: attempt.requestId }, params, signal));
          signal?.throwIfAborted();
          if (row.ok !== true || row.saved !== true || row.workflowId !== id || row.packId !== packId || row.branch !== `wf/${id}`) throw new Error('新建回执身份无效');
          attempt.confirmed = { commit: authoringCommit(row.commit), spec, packId };
        }
        const ack = attempt.confirmed;
        const baseline = await postflight(id, user, params, ack.spec, ack.packId, ack.commit, undefined, signal);
        epochs.set(scope, (epochs.get(scope) ?? 0) + 1); locks.delete(scope); attempt.completed = true; return baseline.spec;
      } catch (cause) {
        if (!attempt.confirmed && recognizedRejection(cause)) { locks.delete(scope); creations.delete(scope); throw cause; }
        throw new WorkflowAuthoringOutcomeError('create', id, attempt.requestId, cause);
      } finally { attempt.pending = false; }
    },
    async save(input: WorkflowSaveInput, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowSaveResult> {
      const user = checkIdentity(params), id = input.workflowId, base = input.baseline;
      const evidence = assertBaseline(base, params);
      if (base.workflowId !== id || input.packId !== undefined && input.packId !== base.packId) throw new Error('保存目标不属于原始基线');
      if (input.facade) throw new Error('此保存协议不修改命令绑定，请在管理页配置');
      const spec = await validate(authoringSpec(input.spec, id), params, signal);
      const latest = await readBaseline(id, params, signal); assertBaseline(base, params);
      if (latest.draftToken !== base.draftToken || latest.specJson !== base.specJson || latest.rawPackId !== base.rawPackId ||
        latest.sourceCommit !== base.sourceCommit || latest.expectedSavedHead !== base.expectedSavedHead) throw new Error('保存基线已变化，未发送写入');
      const capability = authoringRecord(await api.unifiedCapability(id, params, signal)); signal?.throwIfAborted();
      if (capability.protocol !== 'workflow-unified-save-v1' || Object.keys(capability).length !== 1) throw new Error('统一保存协议不可用；未发送保存，不会降级');
      const requestId = crypto.randomUUID(), scope = key(id, user); assertOpen(id, user); locks.add(scope);
      try {
        const receipt = authoringRecord(await api.unifiedSave(id, { requestId, sourceVersion: base.sourceVersion,
          sourceCommit: base.sourceCommit, expectedSavedHead: base.expectedSavedHead,
          expectedDraftToken: base.draftToken, specJson: JSON.stringify(spec), changes: [] }, params, signal));
        signal?.throwIfAborted();
        const proof = authoringRecord(receipt.evidence);
        if (receipt.requestId !== requestId || receipt.outcome !== 'confirmed' || receipt.reconciliationRequired !== false ||
          proof.db !== 'confirmed' || proof.git !== 'confirmed') throw new Error('统一保存结果未确认');
        authoringToken(proof.inputHash);
        const commit = authoringCommit(proof.commit), revision = authoringToken(proof.draftToken);
        const baseline = await postflight(id, user, params, spec, base.packId, commit, revision, signal, base.rawPackId);
        // Verify every unrelated resource and its mode survived the unified save.
        const oldPath = workflowFromAuthoringPack(evidence.snapshot).path;
        const saved = baselines.get(baseline)!.snapshot;
        const changes = authoringFileChanges(evidence.snapshot.versionFiles, saved.versionFiles);
        if (changes.some(change => change.path !== oldPath) || workflowFromAuthoringPack(saved).path !== oldPath) throw new Error('保存修改了未提交的 Pack 资源');
        epochs.set(scope, (epochs.get(scope) ?? 0) + 1);
        baselines.get(baseline)!.epoch = epochs.get(scope)!;
        const result: WorkflowSaveResult = Object.freeze({ ok: true, saved: true, dbSaved: true, gitSaved: true,
          workflowId: id, packId: base.packId, branch: `wf/${id}`, commit, spec: baseline.spec, draftToken: revision, requestId, baseline });
        receipts.add(result); locks.delete(scope); return result;
      } catch (cause) { throw new WorkflowAuthoringOutcomeError('save', id, requestId, cause); }
    },
    async readPublishReview(receipt: WorkflowSaveResult, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowPublishReview> {
      if (!receipts.has(receipt)) throw new Error('缺少本服务验证的保存回执');
      assertBaseline(receipt.baseline, params);
      const id = receipt.workflowId;
      const latest = await readBaseline(id, params, signal);
      if (latest.draftToken !== receipt.draftToken || latest.sourceCommit !== receipt.commit || latest.packId !== receipt.packId) throw new Error('保存后的数据库或 Git 基线已变化');
      const saved = baselines.get(latest)!.snapshot;
      const state = authoringRecord(await api.deploymentState(id, params, signal)); signal?.throwIfAborted();
      if (state.protocol !== 'editor-deployment-state-v1' || state.workflowId !== id || state.packId !== receipt.packId ||
        state.canDeploy !== true || state.savedCommit !== receipt.commit) throw new Error('发布能力或候选基线不匹配');
      let current: WorkflowAuthoringSnapshot | null = null;
      if (state.activeCommit !== null) {
        const activeCommit = authoringCommit(state.activeCommit);
        current = authoringSnapshot(await api.packVersion(id, 'current', params, signal), id, 'current'); signal?.throwIfAborted();
        if (current.baseCommit !== activeCommit || current.packId !== saved.packId || current.packPath !== saved.packPath) throw new Error('生效版基线或 Pack 范围不匹配');
      }
      const changes = authoringFileChanges(current?.versionFiles ?? [], saved.versionFiles);
      if (changes.reduce((size, change) => size + (change.before?.length ?? 0) + (change.after?.length ?? 0), 0) > 512 * 1024) throw new Error('发布差异过大，无法完整审阅');
      const review: WorkflowPublishReview = Object.freeze({ workflowId: id, packId: receipt.packId, candidateCommit: receipt.commit,
        draftToken: receipt.draftToken, expectedActiveCommit: current?.baseCommit ?? '', changes });
      reviews.set(review, { receipt, serialized: JSON.stringify(review) }); return review;
    },
    async publish(input: WorkflowPublishInput, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowPublishResult> {
      const user = checkIdentity(params), { receipt, review } = input;
      const evidence = reviews.get(review);
      if (!evidence || evidence.receipt !== receipt || evidence.serialized !== JSON.stringify(review) || input.confirmedFileDiff !== true ||
        input.workflowId !== receipt.workflowId || input.packId !== undefined && input.packId !== receipt.packId) throw new Error('发布快照未经审阅确认或已变化');
      assertBaseline(receipt.baseline, params);
      const current = await this.readPublishReview(receipt, params, signal);
      if (JSON.stringify(current) !== JSON.stringify(review)) throw new Error('审阅后发布差异已变化；未发送发布');
      const id = receipt.workflowId, requestId = crypto.randomUUID(), scope = key(id, user); locks.add(scope);
      try {
        const row = authoringRecord(await api.publish({ workflowId: id, packId: receipt.packId, requestId,
          candidateCommit: receipt.commit, expectedDraftToken: receipt.draftToken, expectedActiveCommit: review.expectedActiveCommit,
          snapshotProtocol: 'saved-tree-v2', confirmedFileDiff: true, note: input.note ?? 'editor deploy', autoRun: false }, params, signal));
        signal?.throwIfAborted();
        if (row.ok !== true || row.deployed !== true || row.workflowId !== id || row.packId !== receipt.packId ||
          row.requestId !== undefined && row.requestId !== requestId || !Number.isSafeInteger(row.deployNumber) || Number(row.deployNumber) < 1 ||
          !Number.isSafeInteger(row.version) || Number(row.version) < 1 || row.tagName !== `deploy/${id}/#${row.deployNumber}`) throw new Error('发布回执身份无效');
        epochs.set(scope, (epochs.get(scope) ?? 0) + 1); locks.delete(scope);
        return { ok: true, deployed: true, workflowId: id, packId: receipt.packId, deployNumber: Number(row.deployNumber),
          version: Number(row.version), tagName: String(row.tagName), ...(typeof row.messageId === 'string' || row.messageId === null ? { messageId: row.messageId } : {}) };
      } catch (cause) { throw new WorkflowAuthoringOutcomeError('publish', id, requestId, cause); }
    },
  };
}
