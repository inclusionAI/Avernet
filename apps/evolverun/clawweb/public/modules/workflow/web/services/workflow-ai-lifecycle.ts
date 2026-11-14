import { workflowAiApi, type WorkflowAiCapabilities } from '../api/workflow-ai'

export const workflowAiUnavailable = 'AI 草稿服务不可用：请检查服务器 effective autoHeal.botId / BaaS 配置；浏览器不会选择或覆盖 Bot。原工作流未修改。'
export const workflowAiCapabilityUnconfirmed = '无法确认 AI 草稿服务能力：能力检查传输失败；这不证明服务器 autoHeal.botId / BaaS 配置缺失。原工作流未修改。'

const workflowAiErrorDescriptions: Record<string, string> = {
  AUTO_HEAL_BAAS_NOT_CONFIGURED: '服务器 AutoHeal/BaaS 配置缺失或未启用：请检查服务器 effective autoHeal.botId 与 BaaS 传输配置；浏览器不会选择或覆盖 Bot。',
  AUTO_HEAL_BOT_UNAVAILABLE: '服务器配置的 AutoHeal Bot 绑定不可用或无效：请检查 effective autoHeal.botId 对应的 BaaS Bot 是否存在、可用且绑定有效。',
  BAAS_AUTH_FAILED: 'BaaS 认证或权限失败：请检查 BaaS API Key / IAM token 及相关权限；认证失败不能证明任务未被派发，可能在提交或轮询阶段发生。',
  BAAS_REQUEST_REJECTED: 'BaaS 明确拒绝本次请求：请检查 BaaS 请求策略、Bot 状态和输入限制，根据提供方错误信息调整后重试。',
  BAAS_GENERATION_FAILED: 'BaaS 生成失败：请检查上游任务状态或日志，并确认需求、Bot 配置和输入限制后重试。',
  BAAS_PROVIDER_REJECTED: 'BaaS 服务提供方拒绝本次请求或结果：请检查提供方错误信息、配额、访问策略和 Bot 配置。',
  INVALID_GENERATION_RESULT: 'BaaS 返回内容无法解析或未通过工作流结构校验：请检查 Bot 输出格式、工作流 ID、版本和节点配置；未接受草稿。',
  EXECUTION_TIMEOUT: '草稿任务已超过服务端等待期限：BaaS 远端任务可能继续，不能保证已停止。请先核对原消息状态，不要盲目重新派发。',
  DISPATCH_UNCERTAIN_TIMEOUT: '等待 BaaS 派发回执超时：仍无法确认消息是否已接收，远端任务可能继续。请先核对原请求，不要盲目重新派发。',
  EDIT_PERMISSION_REVOKED: '当前工作流的编辑权限已撤销：草稿任务已停止本地处理，请联系管理员核对权限；这不保证 BaaS 远端任务已停止。',
}

/** Known provider codes stay traceable; unknown server diagnostics are preserved verbatim. */
export function describeWorkflowAiError(reason?: string) {
  if (!reason) return ''
  const code = reason.trim()
  const description = Object.hasOwn(workflowAiErrorDescriptions, code) ? workflowAiErrorDescriptions[code] : undefined
  return description ? `${code}：${description}` : reason
}

export function describeWorkflowAiUnavailable(reason?: string) {
  return `${workflowAiUnavailable}${reason ? `服务端原因：${describeWorkflowAiError(reason)}。` : '服务器未返回具体原因。'}`
}
export type WorkflowAiAttempt = { requestId: string; draftId?: string }

/** Recovery always reuses a persisted draft or the same idempotency key. */
export class WorkflowAiRecoveryError extends Error {
  constructor(message: string, public readonly attempt: WorkflowAiAttempt) { super(message) }
}

export function createWorkflowAiAttempt(): WorkflowAiAttempt {
  if (globalThis.crypto?.randomUUID) return { requestId: globalThis.crypto.randomUUID() }
  const bytes = globalThis.crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 15) | 64
  bytes[8] = (bytes[8] & 63) | 128
  const hex = [...bytes].map(value => value.toString(16).padStart(2, '0')).join('')
  return { requestId: `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}` }
}

export async function readWorkflowAiCapabilities(workflowId: string, signal: AbortSignal): Promise<WorkflowAiCapabilities> {
  try {
    const capabilities = await workflowAiApi.capabilities(workflowId, signal)
    if (typeof capabilities?.available !== 'boolean') throw new Error('能力响应无效')
    return capabilities
  } catch (cause) {
    signal.throwIfAborted()
    throw new Error(`${workflowAiCapabilityUnconfirmed}原因：${cause instanceof Error ? cause.message : '请求失败'}。请重试。`)
  }
}

/** Also bounds transports that resolve late or do not cooperate with abort. */
export function abortable<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const abort = () => reject(signal.reason ?? new Error('已停止等待草稿'))
    signal.addEventListener('abort', abort, { once: true })
    promise.then(resolve, reject).finally(() => signal.removeEventListener('abort', abort))
    if (signal.aborted) abort()
  })
}

export function delay(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(signal.reason) }
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve() }, 3000)
    if (signal.aborted) abort()
    else signal.addEventListener('abort', abort, { once: true })
  })
}

/** Best-effort local server cancellation; BaaS has no supported remote stop. */
export async function cancelWorkflowAiAttempt(workflowId: string, attempt: WorkflowAiAttempt): Promise<void> {
  if (!attempt.draftId) return
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 10_000)
  try { await abortable(workflowAiApi.cancel(workflowId, attempt.draftId, controller.signal), controller.signal) }
  catch { /* Best effort only; a failed server record must not promise that BaaS stopped. */ }
  finally { clearTimeout(timeout) }
}

export async function workflowAiInputDigest(yaml: string): Promise<string | null> {
  // Non-secure contexts rely on the server's immutable requestId -> draftId/input binding.
  // Scoped workflow/draft IDs and a nonempty server digest are still checked on every poll.
  if (!globalThis.crypto?.subtle) return null
  const hash = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(yaml))
  return [...new Uint8Array(hash)].map(value => value.toString(16).padStart(2, '0')).join('')
}
