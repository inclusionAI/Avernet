import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { workflowAiApi, type WorkflowAiPrepared, type WorkflowAiTask } from '../api/workflow-ai'
import {
  assertWorkflowAiSpec, describeWorkflowAiChanges, parseWorkflowAiYaml,
  restoreLayout, workflowAiYaml, workflowSpecSignature, type WorkflowAiChange,
} from './workflow-ai-spec'
import {
  abortable, cancelWorkflowAiAttempt, createWorkflowAiAttempt, delay, readWorkflowAiCapabilities,
  describeWorkflowAiError, describeWorkflowAiUnavailable, workflowAiInputDigest, WorkflowAiRecoveryError, type WorkflowAiAttempt,
} from './workflow-ai-lifecycle'

export { workflowSpecSignature } from './workflow-ai-spec'
export type { WorkflowAiChange } from './workflow-ai-spec'
export type { WorkflowAiMode } from '../api/workflow-ai'
import type { WorkflowAiMode } from '../api/workflow-ai'
export type WorkflowAiDraft = {
  workflowId: string
  baseSignature: string
  summary: string
  spec: WorkflowSpec
  yaml: string
  changes: WorkflowAiChange[]
  metadataChanges: string[]
  removedNodeIds: string[]
  warnings: string[]
}

/** Explicit candidate/terminal rejection, never an operational HTTP failure. */
export class WorkflowAiCandidateError extends Error {}
function candidateCheck<T>(check: () => T): T {
  try { return check() }
  catch (cause) { throw new WorkflowAiCandidateError(cause instanceof Error ? cause.message : 'AI 草稿无效') }
}

function validContextValue(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0 && value === value.trim()
}

function assertPrepared(prepared: WorkflowAiPrepared, digest: string | null) {
  if (!validContextValue(prepared?.draftId) || !validContextValue(prepared?.receipt)) {
    throw new WorkflowAiCandidateError('服务器未返回有效 AI 草稿任务 ID 或恢复凭据；未派发生成请求')
  }
  if (typeof prepared.inputSpecDigest !== 'string' || !/^[a-f0-9]{64}$/.test(prepared.inputSpecDigest) || digest !== null && prepared.inputSpecDigest !== digest) {
    throw new WorkflowAiCandidateError('AI 草稿准备响应的输入 YAML 摘要不匹配；未派发生成请求')
  }
}

function assertTask(task: WorkflowAiTask, workflowId: string, attempt: WorkflowAiAttempt, digest: string | null) {
  if (task?.draftId !== attempt.draftId || task?.workflowId !== workflowId) {
    throw new WorkflowAiCandidateError('AI 草稿任务或工作流不匹配；未接受草稿')
  }
  if (typeof task.inputSpecDigest !== 'string' || !/^[a-f0-9]{64}$/.test(task.inputSpecDigest) || digest !== null && task.inputSpecDigest !== digest) {
    throw new WorkflowAiCandidateError('AI 草稿的输入 YAML 摘要不匹配；未接受草稿')
  }
}

/** No client-authored instructions, local templates, or save/deploy/run side effects. */
export async function generateWorkflowAiDraft(
  mode: WorkflowAiMode, prompt: string, source: WorkflowSpec, signal?: AbortSignal,
  attempt: WorkflowAiAttempt = createWorkflowAiAttempt(),
): Promise<WorkflowAiDraft> {
  if (!prompt.trim()) throw new WorkflowAiCandidateError('请输入想增加或修改的工作流能力')
  if ([...prompt].length > 4000) throw new WorkflowAiCandidateError('需求描述不能超过 4000 字符')
  const baseline = structuredClone(source)
  const currentYaml = workflowAiYaml(baseline)
  if (new TextEncoder().encode(currentYaml).byteLength > 100_000) throw new WorkflowAiCandidateError('当前 YAML 超过生成服务的 100000 UTF-8 字节限制，请先缩小工作流范围')
  const controller = new AbortController()
  let cancelSent = false
  const recovering = Boolean(attempt.draftId || attempt.receipt || attempt.submitted)
  let submissionStarted = recovering
  const cancelRemote = () => {
    if (!attempt.draftId || !attempt.receipt || cancelSent) return
    cancelSent = true
    void cancelWorkflowAiAttempt(baseline.id, attempt)
  }
  const cancel = () => controller.abort(signal?.reason)
  signal?.addEventListener('abort', cancel, { once: true })
  if (signal?.aborted) cancel()
  const timeout = setTimeout(() => controller.abort(new Error(`生成等待已超过 15 分钟：已停止本地等待并阻止应用；${submissionStarted ? '取消仅限本地，BaaS 无支持的停止接口，远端任务可能继续，未修改原工作流' : '尚未派发生成请求，可显式重试准备，未修改原工作流'}`)), 15 * 60_000)
  const requestSignal = controller.signal
  requestSignal.addEventListener('abort', cancelRemote, { once: true })
  const wait = <T,>(promise: Promise<T>) => abortable(promise, requestSignal)
  try {
    requestSignal.throwIfAborted()
    const digest = await wait(workflowAiInputDigest(currentYaml))
    requestSignal.throwIfAborted()
    if (recovering) {
      if (!validContextValue(attempt.draftId) || !validContextValue(attempt.receipt)) {
        throw new WorkflowAiRecoveryError('旧 AI 草稿请求上下文缺少有效恢复凭据或任务 ID，无法恢复；不会准备或派发替代请求。请先核对原 BaaS 消息状态。', attempt)
      }
      // Known receipts recover by polling only, even after capabilities change.
    } else {
      const capabilities = await wait(readWorkflowAiCapabilities(baseline.id, requestSignal))
      requestSignal.throwIfAborted()
      if (!capabilities.available) throw new Error(describeWorkflowAiUnavailable(capabilities.reason))
      const input = { requestId: attempt.requestId, mode, prompt, currentYaml }
      try {
        const preparation = workflowAiApi.prepare(baseline.id, input, requestSignal).then(prepared => {
          assertPrepared(prepared, digest)
          if (requestSignal.aborted) {
            // A late prepare did not send anything. Do not overwrite a newer
            // explicit prepare retry sharing this attempt. Never dispatch here.
            void cancelWorkflowAiAttempt(baseline.id, { requestId: attempt.requestId, draftId: prepared.draftId, receipt: prepared.receipt })
            return prepared
          }
          // Preserve the receipt in this same in-memory attempt BEFORE any create.
          if (!attempt.draftId && !attempt.receipt && !attempt.submitted) {
            attempt.draftId = prepared.draftId
            attempt.receipt = prepared.receipt
          }
          return prepared
        })
        await wait(preparation)
      } catch (cause) {
        requestSignal.throwIfAborted()
        if (cause instanceof WorkflowAiCandidateError) throw cause
        throw new Error(`AI 草稿准备失败：尚未派发生成请求，可显式重试准备。${cause instanceof Error ? ` ${describeWorkflowAiError(cause.message)}` : ''}`)
      }
      requestSignal.throwIfAborted()
      if (!attempt.submitted) {
        // This is the only dispatch site. Recovery never calls create again.
        submissionStarted = true
        attempt.submitted = true
        try {
          const submitted = await wait(workflowAiApi.generate(baseline.id, { ...input, receipt: attempt.receipt! }, requestSignal))
          requestSignal.throwIfAborted()
          if (submitted?.draftId !== attempt.draftId) throw new Error('服务器返回的 AI 草稿任务 ID 不匹配')
          if (submitted.status === 'failed') throw new WorkflowAiCandidateError(describeWorkflowAiError(submitted.error, submitted.draftId) || 'BaaS 明确拒绝本次请求；未接受草稿')
          if (!['pending', 'running', 'dispatch_uncertain'].includes(submitted.status)) throw new Error('AI 草稿服务返回未知提交状态')
        } catch (cause) {
          requestSignal.throwIfAborted()
          if (cause instanceof WorkflowAiCandidateError) throw cause
          throw new WorkflowAiRecoveryError(`AI 草稿请求未确认：BaaS 可能已接收消息，远端任务可能继续。已保留恢复凭据和任务 ID；请恢复查询同一任务，不会重复派发。${cause instanceof Error ? ` ${cause.message}` : ''}`, attempt)
        }
      }
    }
    for (;;) {
      requestSignal.throwIfAborted()
      let task: WorkflowAiTask
      try { task = await wait(workflowAiApi.poll(baseline.id, attempt.draftId!, { receipt: attempt.receipt!, currentYaml }, requestSignal)) }
      catch (cause) {
        requestSignal.throwIfAborted()
        throw new WorkflowAiRecoveryError(`AI 草稿任务状态读取失败；请恢复查询同一任务，不会重新生成。${cause instanceof Error ? ` ${cause.message}` : ''}`, attempt)
      }
      requestSignal.throwIfAborted()
      assertTask(task, baseline.id, attempt, digest)
      if (task.status === 'running' && task.error) {
        throw new WorkflowAiRecoveryError(`原任务查询暂不可用；请恢复查询同一任务，不会重新生成。${describeWorkflowAiError(task.error, task.draftId)}`, attempt)
      }
      if (task.status === 'failed') throw new WorkflowAiCandidateError(describeWorkflowAiError(task.error, task.draftId) || 'AI 草稿生成失败，请调整需求后重试')
      if (task.status === 'cancelled') throw new WorkflowAiCandidateError('AI 草稿已取消本地等待并阻止应用；取消不会持久记录，BaaS 无支持的停止接口，远端任务可能继续，原工作流未修改')
      if (task.status === 'completed') {
        const parsed = candidateCheck(() => parseWorkflowAiYaml(task.result?.yaml ?? '', baseline))
        let validation: Awaited<ReturnType<typeof workflowAiApi.validate>>
        try { validation = await wait(workflowAiApi.validate(parsed, requestSignal)) }
        catch (cause) {
          requestSignal.throwIfAborted()
          // HTTP auth/rate limits (including 4xx) do not reject candidate content.
          // Generation is already complete. Retry validation of this same draft,
          // not the draft task; deterministic candidate checks stay outside this catch.
          throw new WorkflowAiRecoveryError(`AI 草稿已生成，但校验请求失败；请恢复查询同一草稿并重试校验，不会重新生成。${cause instanceof Error ? ` ${cause.message}` : ''}`, attempt)
        }
        requestSignal.throwIfAborted()
        if (!validation.valid || !validation.normalizedSpec) {
          const details = validation.issues?.map(issue => `${issue.path}: ${issue.message}`).join('；')
          throw new WorkflowAiCandidateError(`草稿校验失败：${details || '服务端未返回有效工作流'}`)
        }
        candidateCheck(() => assertWorkflowAiSpec(validation.normalizedSpec, baseline))
        const spec = restoreLayout(validation.normalizedSpec, baseline)
        return {
          workflowId: baseline.id, baseSignature: workflowSpecSignature(baseline),
          summary: task.result?.summary || '已生成工作流草稿，请审阅差异后应用', spec,
          yaml: workflowAiYaml(spec), ...describeWorkflowAiChanges(baseline, spec),
          warnings: (validation.issues ?? []).filter(issue => issue.severity === 'warning').map(issue => `${issue.path}: ${issue.message}`),
        }
      }
      if (task.status !== 'pending' && task.status !== 'running' && task.status !== 'dispatch_uncertain') throw new WorkflowAiCandidateError('AI 草稿服务返回未知状态')
      await delay(requestSignal)
    }
  } catch (cause) {
    if (requestSignal.aborted) {
      cancelRemote()
      if (!signal?.aborted && submissionStarted) {
        throw new WorkflowAiRecoveryError(`${requestSignal.reason?.message || '等待超时'}。请恢复本次请求，不会重新创建任务。`, attempt)
      }
      throw requestSignal.reason
    }
    if (submissionStarted && !(cause instanceof WorkflowAiRecoveryError) && !(cause instanceof WorkflowAiCandidateError)) {
      throw new WorkflowAiRecoveryError(`本次 AI 草稿请求尚未确认；请保留原请求恢复，不会重新派发。${cause instanceof Error ? ` ${cause.message}` : ''}`, attempt)
    }
    throw cause
  } finally {
    clearTimeout(timeout)
    requestSignal.removeEventListener('abort', cancelRemote)
    signal?.removeEventListener('abort', cancel)
  }
}
