import {
  assertWorkflowAuthoringBaseline, authoringRecord, authoringSpec, authoringText, authoringWorkflowId,
  authoringYamlText, buildBlankWorkflowAuthoringSpec, parseWorkflowAuthoringYaml,
  restoreWorkflowAuthoringLayout, workflowAuthoringSignature, workflowAuthoringYaml,
} from '@/domain/taskEscortParity/workflowAuthoring';
import {
  WorkflowAuthoringPartialSaveError, WorkflowAiRecoveryError, WorkflowAuthoringOutcomeError,
  type WorkflowAiDraftJob, type WorkflowAiGenerationInput, type WorkflowAiGenerationJob,
  type WorkflowAiJobStatus, type WorkflowAiModificationInput, type WorkflowAiResult, type WorkflowAiWaitOptions,
  type WorkflowAuthoringIdentity, type WorkflowAuthoringIssue, type WorkflowAuthoringSpec,
  type WorkflowAuthoringValidation, type WorkflowCloneCandidate, type WorkflowClonePreview,
  type WorkflowCloneResult, type WorkflowCreateFromExistingInput, type WorkflowCreateFromSpecInput,
  type WorkflowCreationInput,
} from '@/domain/taskEscortParity/workflowAuthoringTypes';
import {
  workflowAuthoringController,
  type WorkflowAuthoringController,
} from '@/services/backendApi/harnessFlow/workflowAuthoringController';
import { createWorkflowAuthoringPersistence } from './workflowAuthoringPersistence';
import { isEnvelopeSuccessAnyDialect } from '@/services/backendApi/types';

export { workflowAuthoringSignature, workflowAuthoringYaml, WorkflowAuthoringPartialSaveError, WorkflowAiRecoveryError, WorkflowAuthoringOutcomeError };
const statuses = new Set<WorkflowAiJobStatus>(['pending', 'running', 'dispatch_uncertain', 'completed', 'failed', 'cancelled']);
const terminal = (status: WorkflowAiJobStatus) => status === 'completed' || status === 'failed' || status === 'cancelled';
const commitPattern = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
function payload(value: unknown): Record<string, unknown> {
  const row = authoringRecord(value);
  // Open specs can legitimately contain code/success/data/error extension keys.
  if (typeof row.id === 'string' && Array.isArray(row.nodes)) return row;
  if ('success' in row || 'code' in row) {
    if (!isEnvelopeSuccessAnyDialect(row)) throw new Error('工作流请求失败');
    return authoringRecord(row.data);
  }
  if (row.ok === false || typeof row.error === 'string' && !('status' in row)) throw new Error('工作流请求失败');
  return row;
}
function identity(params: WorkflowAuthoringIdentity): string {
  const user = authoringText(params.user_id, '用户身份');
  if (user.trim() !== user || /[\r\n]/.test(user)) throw new Error('用户身份无效');
  return user;
}
function checkAbort(signal?: AbortSignal): void { signal?.throwIfAborted(); }
function match(actual: unknown, expected: string, label: string): void {
  if (actual !== expected) throw new Error(`${label}不匹配，已拒绝不可信响应`);
}
function optionalIdentity(row: Record<string, unknown>, userId: string): void {
  for (const key of ['userId', 'user_id', 'actorId', 'ownerId']) if (row[key] !== undefined) match(row[key], userId, '回包身份');
}
function integer(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error(`${label}无效`);
  return value;
}
function commit(value: unknown): string {
  const text = authoringText(value, 'Git commit');
  if (!commitPattern.test(text)) throw new Error('Git commit 无效');
  return text;
}
function status(value: unknown): WorkflowAiJobStatus {
  if (!statuses.has(value as WorkflowAiJobStatus)) throw new Error('AI 任务返回未知状态');
  return value as WorkflowAiJobStatus;
}
function result(row: Record<string, unknown>, state: WorkflowAiJobStatus): WorkflowAiResult | undefined {
  if (state !== 'completed') return undefined;
  const value = authoringRecord(row.result);
  const yaml = authoringYamlText(value.yaml);
  if (value.summary !== undefined && typeof value.summary !== 'string') throw new Error('AI 摘要格式无效');
  return Object.freeze({ yaml, ...(typeof value.summary === 'string' ? { summary: value.summary } : {}) });
}
function jobError(row: Record<string, unknown>): string | undefined {
  if (row.error === undefined) return undefined;
  return authoringText(row.error, 'AI 错误');
}
/** Detect normalization loss instead of silently deleting opaque spec extensions. */
function noFieldLoss(before: unknown, after: unknown, path = ''): void {
  if (Array.isArray(before)) {
    if (!Array.isArray(after) || before.length !== after.length) throw new Error('服务端规范化丢失了工作流字段');
    before.forEach((value, index) => noFieldLoss(value, after[index], `${path}[${index}]`));
  } else if (before && typeof before === 'object') {
    const row = authoringRecord(after);
    for (const [key, value] of Object.entries(before)) {
      if (!Object.prototype.hasOwnProperty.call(row, key)) throw new Error('服务端规范化丢失了工作流字段');
      noFieldLoss(value, row[key], path ? `${path}.${key}` : key);
    }
  } else if (before !== after && !(path === 'version' && String(before) === String(after)) &&
    !(typeof before === 'string' && typeof after === 'string' && /^(?:id|title|nodes\[\d+\]\.(?:id|title))$/.test(path) && before.trim() === after)) {
    throw new Error('服务端规范化修改了工作流扩展字段');
  }
}
function validation(value: unknown, expected?: WorkflowAuthoringSpec): WorkflowAuthoringValidation {
  const row = payload(value);
  if (typeof row.valid !== 'boolean' || !Array.isArray(row.issues)) throw new Error('校验响应格式无效');
  const issues = row.issues.map((value): WorkflowAuthoringIssue => {
    const item = authoringRecord(value);
    if (typeof item.path !== 'string' || !['error', 'warning'].includes(String(item.severity))) throw new Error('校验问题格式无效');
    return { path: item.path, message: authoringText(item.message, '校验消息'), severity: item.severity as 'error' | 'warning' };
  });
  const errors = issues.filter(issue => issue.severity === 'error');
  const spec = row.valid ? authoringSpec(row.normalizedSpec, expected?.id) : null;
  if (row.valid && errors.length || !row.valid && row.normalizedSpec !== null) throw new Error('校验结果互相矛盾');
  if (spec && expected) noFieldLoss(expected, spec);
  return { valid: row.valid, issues, errors, warnings: issues.filter(issue => issue.severity === 'warning'), spec, normalizedSpec: spec };
}
function requireValid(value: WorkflowAuthoringValidation): WorkflowAuthoringSpec {
  if (!value.valid || !value.spec) throw new Error(`草稿校验失败：${value.errors.map(issue => `${issue.path}: ${issue.message}`).join('；') || '未返回有效工作流'}`);
  return value.spec;
}
async function sha256(text: string): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new Error('当前环境不支持安全的草稿身份校验');
  const bytes = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, '0')).join('');
}
function delay(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    let timer: ReturnType<typeof setTimeout>;
    const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(signal.reason); };
    timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
    if (signal.aborted) abort(); else signal.addEventListener('abort', abort, { once: true });
  });
}
function preview(value: unknown, sourceWorkflowId: string): WorkflowClonePreview {
  const row = payload(value);
  match(row.sourceWorkflowId, sourceWorkflowId, '来源工作流');
  return { sourceWorkflowId, sourcePackId: authoringWorkflowId(row.sourcePackId),
    sourceTitle: authoringText(row.sourceTitle, '来源名称'), baseCommit: commit(row.baseCommit), scriptCount: integer(row.scriptCount, '脚本数量') };
}
interface Session { userId: string; cancelled: boolean; cancellation: AbortController }
interface ModificationSession extends Session { workflowId: string; requestId: string; currentYaml: string; digest: string; base: WorkflowAuthoringSpec; draftId: string; receipt: string }

/** No global identity/store/toast. Jobs are capabilities bound to this service instance and request scope. */
export function createWorkflowAuthoringService(api: WorkflowAuthoringController = workflowAuthoringController) {
  const modifications = new WeakMap<WorkflowAiDraftJob, ModificationSession>();
  const generations = new WeakMap<WorkflowAiGenerationJob, Session>();
  const generationDrafts = new WeakMap<WorkflowAiGenerationJob, WorkflowAiDraftJob>();
  const attempts = new Map<string, { input: string; job: WorkflowAiDraftJob }>();
  const cloneUncertain = new Map<string, string>();
  function bindModification(job: WorkflowAiDraftJob, session: ModificationSession): WorkflowAiDraftJob {
    const frozen = Object.freeze(job); modifications.set(frozen, session); return frozen;
  }
  function bindGeneration(job: WorkflowAiGenerationJob, session: Session): WorkflowAiGenerationJob {
    const frozen = Object.freeze(job); generations.set(frozen, session); return frozen;
  }
  function modificationSession(job: WorkflowAiDraftJob, params: WorkflowAuthoringIdentity, allowCancelled = false) {
    const session = modifications.get(job);
    if (!session || identity(params) !== session.userId || job.userId !== session.userId || job.workflowId !== session.workflowId ||
      job.draftId !== session.draftId || job.receipt !== session.receipt || job.requestId !== session.requestId || job.inputSpecDigest !== session.digest) throw new Error('AI 草稿身份或归属不匹配');
    if (session.cancelled && !allowCancelled) throw new Error('AI 草稿已取消，不能读取或应用');
    return session;
  }
  function generationSession(job: WorkflowAiGenerationJob, params: WorkflowAuthoringIdentity, allowCancelled = false) {
    const session = generations.get(job);
    if (!session || identity(params) !== session.userId || job.userId !== session.userId) throw new Error('AI 生成身份或归属不匹配');
    if (session.cancelled && !allowCancelled) throw new Error('AI 生成已取消，不能读取或应用');
    return session;
  }
  function draftDetail(value: unknown, previous: WorkflowAiDraftJob, session: ModificationSession): WorkflowAiDraftJob {
    const row = payload(value);
    match(row.draftId, session.draftId, '草稿 ID');
    match(row.workflowId, session.workflowId, '工作流');
    match(row.inputSpecDigest, session.digest, '草稿基线摘要');
    if (row.requestId !== undefined) match(row.requestId, session.requestId, '请求 ID');
    optionalIdentity(row, session.userId);
    const state = status(row.status);
    return bindModification({ ...previous, status: state, result: result(row, state), error: jobError(row) }, session);
  }
  async function editAccess(workflowId: string, params: WorkflowAuthoringIdentity, signal?: AbortSignal) {
    const row = payload(await api.access(workflowId, params, signal));
    checkAbort(signal); optionalIdentity(row, identity(params)); match(row.workflowId, workflowId, '工作流');
    if (typeof row.canView !== 'boolean' || typeof row.canEdit !== 'boolean') throw new Error('编辑权限响应不完整');
    if (!row.canEdit) throw Object.assign(new Error('403：没有工作流编辑权限'), { status: 403 });
  }
  async function wait<T extends WorkflowAiDraftJob | WorkflowAiGenerationJob>(initial: T,
    params: WorkflowAuthoringIdentity, options: WorkflowAiWaitOptions<T>, poll: (job: T, params: WorkflowAuthoringIdentity, signal?: AbortSignal) => Promise<T>, session: Session): Promise<T> {
    const interval = options.intervalMs ?? 3000, timeout = options.timeoutMs ?? 15 * 60_000;
    if (!Number.isSafeInteger(interval) || interval <= 0 || !Number.isSafeInteger(timeout) || timeout <= 0 || timeout > 15 * 60_000) throw new Error('轮询时间参数无效');
    const controller = new AbortController();
    const abort = () => controller.abort(options.signal?.reason);
    const cancelled = () => controller.abort(new DOMException('AI 任务已取消', 'AbortError'));
    session.cancellation.signal.addEventListener('abort', cancelled, { once: true });
    options.signal?.addEventListener('abort', abort, { once: true });
    if (options.signal?.aborted) abort();
    const timer = setTimeout(() => controller.abort(new DOMException('AI 生成等待超时；上游任务可能仍在运行', 'TimeoutError')), timeout);
    try {
      let job = initial;
      while (true) {
        checkAbort(controller.signal);
        if (session.cancelled) throw new Error('AI 任务已取消');
        if (!terminal(job.status)) job = await poll(job, params, controller.signal);
        checkAbort(controller.signal);
        if (session.cancelled) throw new Error('AI 任务已取消');
        options.onUpdate?.(job);
        if (terminal(job.status)) return job;
        await delay(interval, controller.signal);
      }
    } catch (error) {
      if (controller.signal.aborted) { session.cancelled = true; session.cancellation.abort(); }
      throw error;
    } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', abort); session.cancellation.signal.removeEventListener('abort', cancelled); }
  }
  const persistence = createWorkflowAuthoringPersistence(api, identity, async (spec, params, signal) => {
    const response = await api.validate({ spec }, params, signal); checkAbort(signal);
    requireValid(validation(response, spec)); return authoringSpec(spec);
  }, editAccess);
  const service = {
    readBaseline: persistence.readBaseline,
    readPublishReview: persistence.readPublishReview,
    save: persistence.save,
    publish: persistence.publish,
    async validateYaml(yaml: string, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowAuthoringValidation> {
      identity(params); checkAbort(signal);
      // Local parsing is bounded and lossless; server remains authoritative.
      let parsed: WorkflowAuthoringSpec;
      try { parsed = parseWorkflowAuthoringYaml(yaml); }
      catch (error) {
        const issue: WorkflowAuthoringIssue = { path: '', message: error instanceof Error ? error.message : 'YAML 无效', severity: 'error' };
        return { valid: false, issues: [issue], errors: [issue], warnings: [], spec: null, normalizedSpec: null };
      }
      const response = await api.validate({ spec: parsed }, params, signal); checkAbort(signal);
      return validation(response, parsed);
    },
    async createBlank(input: WorkflowCreationInput, params: WorkflowAuthoringIdentity, signal?: AbortSignal) {
      return service.createFromSpec({ ...input, spec: buildBlankWorkflowAuthoringSpec(input.workflowId, input.title) }, params, signal);
    },
    async createFromSpec(input: WorkflowCreateFromSpecInput, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowAuthoringSpec> {
      const user = identity(params); authoringWorkflowId(input.workflowId); checkAbort(signal);
      if (input.botOwnerId !== undefined && input.botOwnerId !== user) throw new Error('创建者身份不匹配');
      if (input.botId) throw new Error('原子创建接口不支持 Bot 绑定；请创建后在管理页配置');
      return persistence.create(input, params, signal);
    },
    async listCloneCandidates(params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowCloneCandidate[]> {
      const user = identity(params); checkAbort(signal);
      const row = payload(await api.cloneCandidates(params, signal)); checkAbort(signal); optionalIdentity(row, user);
      if (!Array.isArray(row.data)) throw new Error('来源列表响应格式无效');
      const seen = new Set<string>();
      return row.data.map(raw => {
        const item = authoringRecord(raw), workflowId = authoringWorkflowId(item.workflowId);
        if (seen.has(workflowId)) throw new Error('来源列表包含重复工作流');
        seen.add(workflowId);
        return { workflowId, title: authoringText(item.title, '来源名称'), packId: item.packId === null ? null : authoringWorkflowId(item.packId), updatedAt: integer(item.updatedAt, '更新时间') };
      });
    },
    async readClonePreview(sourceWorkflowId: string, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowClonePreview> {
      identity(params); const source = authoringWorkflowId(sourceWorkflowId); checkAbort(signal);
      const response = await api.clonePreview(source, params, signal); checkAbort(signal);
      return preview(response, source);
    },
    async createFromExisting(input: WorkflowCreateFromExistingInput, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowCloneResult> {
      const user = identity(params), id = authoringWorkflowId(input.workflowId), source = authoringWorkflowId(input.sourceWorkflowId);
      checkAbort(signal); authoringText(input.title, '工作流名称');
      if (id === source) throw new Error('新工作流 ID 不能与来源相同');
      if (input.botOwnerId !== undefined && input.botOwnerId !== user) throw new Error('创建者身份不匹配');
      if (input.packId !== undefined && input.packId !== id || input.botId) throw new Error('克隆只创建独立 Pack，不支持覆盖来源 Pack 或 Bot');
      const scope = JSON.stringify([id, user]);
      if (cloneUncertain.has(scope)) throw new WorkflowAuthoringOutcomeError('create', id, cloneUncertain.get(scope)!, new Error('上次克隆结果尚未核对'));
      const requestId = crypto.randomUUID(); cloneUncertain.set(scope, requestId);
      try {
      const response = payload(await api.clone({ sourceWorkflowId: source, targetWorkflowId: id, targetTitle: input.title,
        ...(input.facade ? { facade: input.facade } : {}) }, params, signal));
      checkAbort(signal); optionalIdentity(response, user);
      const sourcePreview = preview(response, source);
      match(response.targetWorkflowId, id, '克隆目标'); match(response.targetPackId, id, '克隆 Pack');
      if (response.branch !== `wf/${id}`) throw new Error('克隆分支不属于新工作流');
      const clonedCommit = commit(response.commit);
      const baseline = await persistence.readBaseline(id, params, signal);
      if (baseline.packId !== id || baseline.sourceCommit !== clonedCommit) throw new Error('克隆快照未核对');
      cloneUncertain.delete(scope);
      return { ...sourcePreview, targetWorkflowId: id, targetPackId: id, branch: response.branch, commit: clonedCommit };
      } catch (cause) {
        const e = cause as { status?: number; data?: { code?: string } };
        if ([400, 403, 409].includes(e.status ?? 0) && typeof e.data?.code === 'string' && !/UNKNOWN/.test(e.data.code)) { cloneUncertain.delete(scope); throw cause; }
        throw new WorkflowAuthoringOutcomeError('create', id, requestId, cause);
      }
    },
    async capabilities(workflowId: string, params: WorkflowAuthoringIdentity, signal?: AbortSignal) {
      const user = identity(params), id = authoringWorkflowId(workflowId); checkAbort(signal);
      const row = payload(await api.capabilities(id, params, signal)); checkAbort(signal); optionalIdentity(row, user);
      if (row.workflowId !== undefined) match(row.workflowId, id, '工作流');
      if (typeof row.available !== 'boolean' || row.reason !== undefined && typeof row.reason !== 'string') throw new Error('AI 能力响应无效');
      return { available: row.available, ...(typeof row.reason === 'string' ? { reason: row.reason } : {}) };
    },
    async startModification(workflowId: string, input: WorkflowAiModificationInput, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowAiDraftJob> {
      const userId = identity(params), id = authoringWorkflowId(workflowId); checkAbort(signal);
      if (!/^[a-f0-9]{8}-[a-f0-9]{4}-[1-5][a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/i.test(input.requestId)) throw new Error('AI 请求 ID 必须是 UUID');
      const prompt = authoringText(input.prompt, 'AI 需求');
      if ([...prompt].length > 4000) throw new Error('AI 需求超过 4000 字符');
      const currentYaml = authoringYamlText(input.currentYaml), base = authoringSpec(parseWorkflowAuthoringYaml(currentYaml), id);
      const digest = await sha256(currentYaml); checkAbort(signal);
      const request = { requestId: input.requestId, mode: input.mode ?? 'modify', prompt, currentYaml };
      const attemptKey = JSON.stringify([userId, id, input.requestId]);
      const known = attempts.get(attemptKey);
      if (known) {
        if (known.input !== JSON.stringify(request)) throw new Error('同一请求 ID 不能替换输入');
        return service.pollModification(known.job, params, signal);
      }
      const capabilities = await service.capabilities(id, params, signal);
      if (!capabilities.available) throw new Error(`AI 草稿服务不可用：${capabilities.reason ?? '未启用'}`);
      const prepared = payload(await api.prepareModification(id, request, params, signal));
      const draftId = authoringText(prepared.draftId, '草稿 ID'), receipt = authoringText(prepared.receipt, '恢复凭据');
      match(prepared.inputSpecDigest, digest, '草稿基线摘要'); optionalIdentity(prepared, userId);
      const session: ModificationSession = { userId, workflowId: id, requestId: input.requestId, currentYaml, digest, base,
        draftId, receipt, cancelled: false, cancellation: new AbortController() };
      const pending = bindModification({ kind: 'modification', userId, workflowId: id, requestId: input.requestId, currentYaml,
        inputSpecDigest: digest, draftId, receipt, status: 'dispatch_uncertain' }, session);
      // Persist the signed recovery capability BEFORE the only dispatch site.
      attempts.set(attemptKey, { input: JSON.stringify(request), job: pending });
      if (signal?.aborted) {
        session.cancelled = true;
        void api.cancelModification(id, draftId, receipt, params).catch(() => undefined);
        signal.throwIfAborted();
      }
      try {
        const row = payload(await api.startModification(id, { ...request, receipt }, params, signal));
        checkAbort(signal); optionalIdentity(row, userId); match(row.draftId, draftId, '草稿 ID');
        const state = status(row.status);
        if (!['pending', 'running', 'dispatch_uncertain', 'failed'].includes(state)) throw new Error('AI 提交状态无效');
        const job = bindModification({ ...pending, status: state, error: jobError(row) }, session);
        attempts.set(attemptKey, { input: JSON.stringify(request), job }); return job;
      } catch (cause) {
        if (signal?.aborted) { session.cancelled = true; void api.cancelModification(id, draftId, receipt, params).catch(() => undefined); checkAbort(signal); }
        throw new WorkflowAiRecoveryError(pending, cause);
      }
    },
    async pollModification(job: WorkflowAiDraftJob, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowAiDraftJob> {
      const session = modificationSession(job, params); checkAbort(signal);
      if (terminal(job.status)) return job;
      let response: unknown;
      try { response = await api.pollModification(session.workflowId, session.draftId, { receipt: session.receipt, currentYaml: session.currentYaml }, params, signal); }
      catch (cause) { checkAbort(signal); throw new WorkflowAiRecoveryError(job, cause); }
      checkAbort(signal); modificationSession(job, params);
      return draftDetail(response, job, session);
    },
    async cancelModification(job: WorkflowAiDraftJob, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowAiDraftJob> {
      const session = modificationSession(job, params, true); session.cancelled = true; session.cancellation.abort(); checkAbort(signal);
      const response = await api.cancelModification(session.workflowId, session.draftId, session.receipt, params, signal); checkAbort(signal);
      const cancelled = draftDetail(response, job, session);
      if (cancelled.status !== 'cancelled') throw new Error('服务端未确认草稿取消；本地已停止接收结果');
      return cancelled;
    },
    waitForModification(job: WorkflowAiDraftJob, params: WorkflowAuthoringIdentity, options: WorkflowAiWaitOptions<WorkflowAiDraftJob> = {}) {
      return wait(job, params, options, service.pollModification, modificationSession(job, params));
    },
    async applyModification(job: WorkflowAiDraftJob, currentSpec: WorkflowAuthoringSpec, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowAuthoringSpec> {
      const session = modificationSession(job, params); checkAbort(signal);
      if (job.status !== 'completed' || !job.result) throw new Error('AI 草稿尚未完成，不能应用');
      const current = authoringSpec(currentSpec, session.workflowId);
      if (workflowAuthoringSignature(current) !== workflowAuthoringSignature(session.base)) throw new Error('草稿基线已变化，请重新生成');
      const candidate = parseWorkflowAuthoringYaml(job.result.yaml); assertWorkflowAuthoringBaseline(candidate, session.base);
      const next = requireValid(validation(await api.validate({ spec: candidate }, params, signal), candidate));
      checkAbort(signal); modificationSession(job, params); assertWorkflowAuthoringBaseline(next, session.base);
      return restoreWorkflowAuthoringLayout(next, current);
    },
    async startGeneration(input: WorkflowAiGenerationInput, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowAiGenerationJob> {
      const currentYaml = input.currentYaml ?? input.context?.existingYaml;
      if (!currentYaml) throw new Error('AI 生成需要已有可编辑工作流上下文；请先选择来源工作流');
      if (input.context?.packId !== undefined || input.baasSessionId !== undefined) throw new Error('AI 草稿协议不接受客户端 provider 或旧会话绑定');
      const source = parseWorkflowAuthoringYaml(currentYaml);
      const workflowId = input.workflowId ?? source.id;
      const draft = await service.startModification(workflowId, { requestId: input.requestId ?? crypto.randomUUID(),
        mode: 'create', prompt: input.prompt, currentYaml }, params, signal);
      const session = modificationSession(draft, params);
      const job = bindGeneration({ kind: 'generation', generationId: draft.draftId, workflowId, userId: session.userId,
        status: draft.status, result: draft.result, error: draft.error }, session);
      generationDrafts.set(job, draft); return job;
    },
    async pollGeneration(job: WorkflowAiGenerationJob, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowAiGenerationJob> {
      const session = generationSession(job, params), draft = generationDrafts.get(job);
      if (!draft || draft.draftId !== job.generationId) throw new Error('AI 生成任务归属无效');
      const updated = await service.pollModification(draft, params, signal);
      const result = bindGeneration({ ...job, status: updated.status, result: updated.result, error: updated.error }, session);
      generationDrafts.set(result, updated); return result;
    },
    async resumeGeneration(draft: WorkflowAiDraftJob, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowAiGenerationJob> {
      const session = modificationSession(draft, params);
      const updated = await service.pollModification(draft, params, signal);
      const job = bindGeneration({ kind: 'generation', generationId: updated.draftId, workflowId: updated.workflowId,
        userId: updated.userId, status: updated.status, result: updated.result, error: updated.error }, session);
      generationDrafts.set(job, updated); return job;
    },
    async cancelGeneration(job: WorkflowAiGenerationJob, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowAiGenerationJob> {
      const session = generationSession(job, params, true), draft = generationDrafts.get(job);
      if (!draft) throw new Error('AI 生成任务归属无效');
      session.cancelled = true; session.cancellation.abort();
      const cancelled = await service.cancelModification(draft, params, signal);
      const result = bindGeneration({ ...job, status: 'cancelled', result: undefined, cancellation: 'local-only' }, session);
      generationDrafts.set(result, cancelled); return result;
    },
    waitForGeneration(job: WorkflowAiGenerationJob, params: WorkflowAuthoringIdentity, options: WorkflowAiWaitOptions<WorkflowAiGenerationJob> = {}) {
      return wait(job, params, options, service.pollGeneration, generationSession(job, params));
    },
    async applyGeneration(job: WorkflowAiGenerationJob, target: { workflowId: string; title?: string }, params: WorkflowAuthoringIdentity, signal?: AbortSignal): Promise<WorkflowAuthoringSpec> {
      generationSession(job, params); checkAbort(signal);
      const draft = generationDrafts.get(job);
      if (!draft) throw new Error('AI 生成任务归属无效');
      const session = modificationSession(draft, params);
      // Validate the source identity/version before explicitly allocating a new target identity.
      const generated = await service.applyModification(draft, session.base, params, signal);
      const id = authoringWorkflowId(target.workflowId);
      const candidate = authoringSpec({ ...generated, id, ...(target.title === undefined ? {} : { title: target.title }) }, id);
      checkAbort(signal); generationSession(job, params); return candidate;
    },
  };
  return service;
}
export type WorkflowAuthoringService = ReturnType<typeof createWorkflowAuthoringService>;
export const workflowAuthoringService = createWorkflowAuthoringService();
