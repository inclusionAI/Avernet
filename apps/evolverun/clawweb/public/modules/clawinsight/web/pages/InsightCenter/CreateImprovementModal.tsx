import { useMemo, useState } from 'react'
import { insightApi } from '../../api/insight'
import type { FailureTaskIndex, ImprovementDetail } from '../../types/insight'
import { InsightIcon } from './InsightUi'
import { createRequestId, failureClassText } from './utils'

type Props = {
  tasks: FailureTaskIndex[]
  adminMode?: boolean
  onClose: () => void
  onCreated: (improvements: ImprovementDetail[]) => void
}

type TaskGroup = {
  key: string
  sourceUserId: string
  botId: string
  botName: string
  tasks: FailureTaskIndex[]
}

type AssignmentMode = 'BOT_OWNER' | 'SPECIFIC_USER'

const MAX_IMPROVEMENT_TITLE_LENGTH = 256

function buildImprovementTitle(botName: string, subject: string): string {
  const separator = ' · '
  const normalizedBotName = botName.trim() || 'Bot'
  const normalizedSubject = subject.trim()
  const botPrefix = normalizedBotName.slice(0, 120)
  return `${botPrefix}${separator}${normalizedSubject}`.slice(0, MAX_IMPROVEMENT_TITLE_LENGTH)
}

export default function CreateImprovementModal({ tasks, adminMode = false, onClose, onCreated }: Props) {
  const groups = useMemo<TaskGroup[]>(() => {
    const result = new Map<string, TaskGroup>()
    for (const task of tasks) {
      const key = `${task.ownerUserId}:${task.botId}`
      const current = result.get(key)
      if (current) current.tasks.push(task)
      else result.set(key, { key, sourceUserId: task.ownerUserId, botId: task.botId, botName: task.botName || task.botId, tasks: [task] })
    }
    return [...result.values()]
  }, [tasks])

  const primaryFailure = useMemo(() => {
    const counts = new Map<string, number>()
    tasks.forEach((task) => counts.set(task.failureClass, (counts.get(task.failureClass) ?? 0) + 1))
    return [...counts.entries()].sort((left, right) => right[1] - left[1])[0]?.[0]
  }, [tasks])

  const [title, setTitle] = useState(() => `${failureClassText[primaryFailure] ?? '失败任务'}改进`)
  const [guidance, setGuidance] = useState('')
  const [assignmentMode, setAssignmentMode] = useState<AssignmentMode>('BOT_OWNER')
  const [specificUser, setSpecificUser] = useState('')
  const [batchRequestId] = useState(() => createRequestId('insight-improvement-batch'))
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  if (!groups.length) return null
  const totalSessions = new Set(tasks.map((task) => `${task.ownerUserId}:${task.sessionId}`)).size
  const invalidAssignment = adminMode && assignmentMode === 'SPECIFIC_USER' && !specificUser.trim()

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-gray-950/35 p-4 backdrop-blur-sm" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}>
      <div role="dialog" aria-modal="true" aria-label="创建改进项" className="flex max-h-[calc(100vh-2rem)] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl">
        <div className="flex shrink-0 items-start justify-between border-b border-gray-100 px-6 py-5">
          <div><div className={`flex items-center gap-2 text-sm font-medium ${adminMode ? 'text-amber-700' : 'text-blue-600'}`}><InsightIcon name="plus" />{adminMode ? '管理员批量创建改进项' : '创建改进项'}</div><h2 className="mt-1 text-xl font-semibold text-gray-950">{groups.length > 1 ? `按 ${groups.length} 个 Bot 分别创建改进项` : '冻结失败任务，交给进化室继续诊断'}</h2><p className="mt-1 text-xs leading-5 text-gray-500">一次可以选择多个 Bot；系统按 Bot 拆分，每个 Bot 生成一个独立改进项。</p></div>
          <button onClick={onClose} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-600" aria-label="关闭"><InsightIcon name="close" /></button>
        </div>
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5">
          <div className="grid gap-3 rounded-xl border border-blue-100 bg-blue-50/50 p-4 sm:grid-cols-3">
            <div><p className="text-[11px] text-gray-400">目标 Bot</p><p className="mt-1 text-sm font-medium text-gray-900">{groups.length} 个</p></div>
            <div><p className="text-[11px] text-gray-400">失败 Task</p><p className="mt-1 text-sm font-medium text-gray-900">{tasks.length} 个</p></div>
            <div><p className="text-[11px] text-gray-400">Session</p><p className="mt-1 text-sm font-medium text-gray-900">{totalSessions} 个</p></div>
          </div>

          <label className="block"><span className="mb-1.5 block text-xs font-medium text-gray-600">改进主题 <span className="text-red-500">*</span></span><input value={title} maxLength={200} onChange={(event) => setTitle(event.target.value)} className="w-full rounded-lg border border-gray-200 px-3 py-2.5 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10" /><p className="mt-1 text-[11px] text-gray-400">最终标题按“Bot 名称 · 改进主题”生成。</p></label>

          {adminMode && <section className="rounded-xl border border-gray-200 bg-gray-50/50 p-4">
            <span className="mb-2 block text-xs font-medium text-gray-600">改进项归属</span>
            <div className="flex flex-col gap-2 sm:flex-row">
              <select value={assignmentMode} onChange={(event) => setAssignmentMode(event.target.value as AssignmentMode)} className="rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-amber-500 sm:w-40">
                <option value="BOT_OWNER">Bot Owner</option>
                <option value="SPECIFIC_USER">指定用户</option>
              </select>
              {assignmentMode === 'SPECIFIC_USER' && <input value={specificUser} onChange={(event) => setSpecificUser(event.target.value)} placeholder="输入 user_id" className="min-w-0 flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-amber-500" />}
            </div>
            <p className="mt-2 text-[11px] leading-5 text-gray-500">{assignmentMode === 'BOT_OWNER' ? `系统仍按 ${groups.length} 个 Bot 创建独立改进项，并分别归属各自 Bot Owner。` : `系统仍按 ${groups.length} 个 Bot 创建独立改进项，并全部归属指定用户。`}</p>
          </section>}

          <label className="block">
            <span className="mb-1.5 block text-xs font-medium text-gray-600">用户判断与改进方向 <span className="font-normal text-gray-400">（可选，应用到本批次全部改进项）</span></span>
            <textarea value={guidance} maxLength={5000} onChange={(event) => setGuidance(event.target.value)} placeholder="例如：我判断主要问题是生产环境缺少必要配置；优先支持环境变量注入，并提供降级方案。" className="min-h-28 w-full resize-y rounded-lg border border-gray-200 px-3 py-2.5 text-sm leading-6 outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10" />
            <div className="mt-1 flex items-center justify-between text-[11px] text-gray-400"><span>此内容会作为进化室 Agent 的重要证据。</span><span>{guidance.length}/5000</span></div>
          </label>
          {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>}
        </div>
        <div className="flex shrink-0 items-center justify-end gap-2 border-t border-gray-100 bg-gray-50/70 px-6 py-4">
          <button onClick={onClose} disabled={submitting} className="rounded-lg border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50">取消</button>
          <button disabled={submitting || !title.trim() || invalidAssignment} onClick={async () => {
            setSubmitting(true); setError('')
            try {
              const result = await insightApi.createImprovementsBatch(groups.map((group) => ({
                title: buildImprovementTitle(group.botName, title),
                botId: group.botId,
                selectedTasks: group.tasks.map((task) => ({ sessionId: task.sessionId, taskIndex: task.taskIndex })),
                userGuidance: guidance.trim() || undefined,
                ownerUserId: adminMode ? assignmentMode === 'BOT_OWNER' ? group.sourceUserId : specificUser.trim() : undefined,
                sourceOwnerUserId: adminMode ? group.sourceUserId : undefined,
              })), batchRequestId)
              onCreated(result.items)
            } catch (reason) {
              setError(reason instanceof Error ? reason.message : '改进项创建失败')
            } finally { setSubmitting(false) }
          }} className={`inline-flex items-center gap-2 rounded-lg px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50 ${adminMode ? 'bg-amber-600 hover:bg-amber-700' : 'bg-blue-600 hover:bg-blue-700'}`}><InsightIcon name="clipboard" />{submitting ? '正在创建…' : groups.length > 1 ? `创建 ${groups.length} 个改进项` : adminMode ? '创建并发布给用户' : '创建改进项'}</button>
        </div>
      </div>
    </div>
  )
}
