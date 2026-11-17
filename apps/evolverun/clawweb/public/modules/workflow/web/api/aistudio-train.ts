import { replaceEqualDeep, useIsMutating, useMutation, useMutationState, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'

/** Backend-owned watch for one node attempt; the browser never supplies a recordId target. */
export interface AIStudioTrainWatch {
  watchId: string
  version: number // Persisted server snapshot version, not a browser observation counter.
  recordId: string
  detailUrl: string | null
  flowId: string
  nodeId: string
  attempt: number
  mode: 'submit' | 'watch'
  phase: 'waiting' | 'polling' | 'resolving' | 'resolved'
  lastStatus: string | null
  submittedAt: string
  firstPollAt: string
  nextPollAt: string | null
  timeoutAt: string
  pollInterval: number // seconds, not milliseconds
  lastPolledAt: string | null
  lastPollSource: 'auto' | 'manual' | null
  errorCount: number
  lastError: string | null
  manualPollAllowed: boolean
  manualPollRetryAt?: string | null
  manualPollPending?: boolean
}
export interface AIStudioTrainWatchResponse { watch: AIStudioTrainWatch }
export interface AIStudioTrainManualPollResponse extends AIStudioTrainWatchResponse {
  ok: true
  resolved: boolean
  requestId?: string
}

const base = (flowId: string, nodeId: string) =>
  `/api/runs/${encodeURIComponent(flowId)}/nodes/${encodeURIComponent(nodeId)}/aistudio-train`
export const aiStudioTrainWatchKey = (flowId: string, nodeId: string, attempt: number) =>
  ['aistudio-train-watch', flowId, nodeId, attempt] as const
const manualKey = (flowId: string, nodeId: string, attempt: number) =>
  ['aistudio-train-manual', flowId, nodeId, attempt] as const

export function getAIStudioTrainWatch(flowId: string, nodeId: string, attempt: number, signal?: AbortSignal) {
  return fetchJson<AIStudioTrainWatchResponse>(`${base(flowId, nodeId)}?attempt=${attempt}`, {
    credentials: 'same-origin', signal,
  })
}
export function pollAIStudioTrainManually(flowId: string, nodeId: string, attempt: number) {
  return fetchJson<AIStudioTrainManualPollResponse>(`${base(flowId, nodeId)}/poll-manual`, {
    method: 'POST', credentials: 'same-origin', body: JSON.stringify({ attempt }),
  })
}

export function aiStudioTrainRefetchInterval(watch: AIStudioTrainWatch | undefined): number | false {
  return watch && (watch.manualPollPending || ['waiting', 'polling', 'resolving'].includes(watch.phase)) ? 5000 : false
}

/** Never regress persisted state; equal versions can refresh server time/permission projections. */
function newestWatch(current: AIStudioTrainWatchResponse | undefined, incoming: AIStudioTrainWatchResponse): AIStudioTrainWatchResponse {
  return current && current.watch.version > incoming.watch.version ? current : replaceEqualDeep(current, incoming)
}

/** Keep error provenance separate from current observations, without losing server cooldowns. */
class ManualPollFailure extends Error {
  constructor(cause: unknown, readonly dataUpdates: number, readonly errorUpdates: number) {
    super(cause instanceof Error ? cause.message : String(cause), { cause })
  }
}

/** Preserve shared ApiError's status/body while giving actionable, query-only messages. */
export function aiStudioTrainError(error: unknown): { message: string; retryAt: string | null } {
  const original = error instanceof ManualPollFailure ? error.cause : error
  const apiError = original as { status?: number; body?: string; message?: string } | null
  let body: Record<string, unknown> = {}
  try {
    const parsed: unknown = JSON.parse(apiError?.body ?? '{}')
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) body = parsed as Record<string, unknown>
  } catch { /* Non-JSON gateway responses retain the status below. */ }
  const labels: Record<number, string> = {
    404: '未找到该节点本次尝试的训练观测记录，请刷新运行详情核对。',
    409: '当前观测状态不允许手工查询，或已有查询正在处理，请刷新后核对。',
    429: '手工查询过于频繁，请等待服务端冷却结束后重试。',
    502: 'AIStudio 上游查询失败，可稍后重试；这不表示训练失败。',
    503: '训练观测功能、认证或 owner 校验尚未就绪，或查询服务暂不可用；请联系管理员或稍后重试。这不表示训练任务失败。',
  }
  const detail = typeof body.error === 'string' ? body.error :
    typeof body.message === 'string' ? body.message :
    body.error && typeof body.error === 'object' && 'message' in body.error ? String(body.error.message) : ''
  const message = apiError?.status && labels[apiError.status]
    ? `${labels[apiError.status]}${detail ? `（${detail}）` : ''}`
    : detail || apiError?.message || '查询未完成，请稍后重试；节点状态不变。'
  const retryAt = body.manualPollRetryAt ?? body.retryAt
  return { message, retryAt: typeof retryAt === 'string' ? retryAt : null }
}

/** Both the latest command and the watch may carry cooldowns; never shorten either one. */
export function aiStudioTrainRetryAt(watch: AIStudioTrainWatch | undefined, ...errors: unknown[]): string | null {
  const deadlines = [watch?.manualPollRetryAt, ...errors.map(error => aiStudioTrainError(error).retryAt)]
    .filter((value): value is string => typeof value === 'string' && Number.isFinite(Date.parse(value)))
  return deadlines.sort((a, b) => Date.parse(b) - Date.parse(a))[0] ?? null
}

export function useAIStudioTrainWatch(flowId: string, nodeId: string, attempt: number) {
  const client = useQueryClient()
  const queryKey = aiStudioTrainWatchKey(flowId, nodeId, attempt)
  const mutationKey = manualKey(flowId, nodeId, attempt)
  const refreshRuns = () => {
    void client.invalidateQueries({ queryKey: ['run', flowId] })
    void client.invalidateQueries({ queryKey: ['runs'] })
  }
  const query = useQuery({
    queryKey,
    queryFn: async ({ signal }) => {
      const result = await getAIStudioTrainWatch(flowId, nodeId, attempt, signal)
      const previous = client.getQueryData<AIStudioTrainWatchResponse>(queryKey)?.watch
      if ((!previous || result.watch.version > previous.version) &&
        result.watch.phase === 'resolved' && !result.watch.manualPollPending &&
        (!previous || aiStudioTrainRefetchInterval(previous))) refreshRuns()
      return result
    },
    // Fence at the actual cache write too: a newer POST/GET may win after queryFn resolves.
    structuralSharing: (current, incoming) => newestWatch(
      current as AIStudioTrainWatchResponse | undefined, incoming as AIStudioTrainWatchResponse),
    retry: false,
    // Observation counters also retire recovered command feedback, even for identical GET data.
    notifyOnChangeProps: 'all',
    staleTime: q => q.state.data && !aiStudioTrainRefetchInterval(q.state.data.watch) ? Infinity : 5000,
    refetchInterval: q => client.isMutating({ mutationKey, exact: true }) ? false : aiStudioTrainRefetchInterval(q.state.data?.watch),
    refetchOnReconnect: q => aiStudioTrainRefetchInterval(q.state.data?.watch) !== false,
    refetchOnWindowFocus: false,
  })
  const mutation = useMutation({
    mutationKey,
    retry: false, // A manual command is never automatically resubmitted.
    onMutate: async () => {
      // A slower pre-command GET must not overwrite the manual response in the cache.
      await client.cancelQueries({ queryKey, exact: true })
    },
    mutationFn: async () => {
      try {
        return await pollAIStudioTrainManually(flowId, nodeId, attempt)
      } catch (cause) {
        const state = client.getQueryState(queryKey)
        throw new ManualPollFailure(cause, state?.dataUpdateCount ?? 0, state?.errorUpdateCount ?? 0)
      }
    },
    onSuccess: async result => {
      // Also cancel GETs started while the command was in flight (e.g. an explicit retry).
      await client.cancelQueries({ queryKey, exact: true })
      const snapshot = client.setQueryData<AIStudioTrainWatchResponse>(queryKey,
        current => newestWatch(current, { watch: result.watch }))
      if (snapshot?.watch.phase === 'resolved' && !snapshot.watch.manualPollPending) refreshRuns()
    },
  })
  const isPending = useIsMutating({ mutationKey, exact: true }) > 0
  const operations = useMutationState({
    filters: { mutationKey, exact: true },
    select: m => ({ error: m.state.error, status: m.state.status }),
  })
  const latestError = operations.at(-1)?.error ?? null
  const observation = client.getQueryState(queryKey)
  const recovered = latestError instanceof ManualPollFailure &&
    ((observation?.dataUpdateCount ?? 0) > latestError.dataUpdates ||
      (observation?.errorUpdateCount ?? 0) > latestError.errorUpdates)
  const operationError = recovered ? null : latestError
  const retryAt = aiStudioTrainRetryAt(query.data?.watch, latestError, query.error)
  const pollNow = () => {
    const watch = client.getQueryData<AIStudioTrainWatchResponse>(queryKey)?.watch
    const lastError = client.getMutationCache().findAll({ mutationKey, exact: true }).at(-1)?.state.error
    const retryAt = aiStudioTrainRetryAt(watch, lastError, client.getQueryState(queryKey)?.error)
    if (!watch?.manualPollAllowed || watch.manualPollPending || watch.phase === 'resolved' ||
      (retryAt && Date.parse(retryAt) > Date.now()) || client.isMutating({ mutationKey, exact: true }) > 0) return Promise.resolve()
    return mutation.mutateAsync()
  }
  return { query, pollNow, isPending, operationError, retryAt }
}
