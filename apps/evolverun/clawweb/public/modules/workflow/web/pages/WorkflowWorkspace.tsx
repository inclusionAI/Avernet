import { readEditorWorkflowSource } from '../api/editorWorkflowSource'
import { assertEditorWorkflowReadable } from '../editor/workflowWriteGuard'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useNavigationType, useSearchParams } from 'react-router-dom'
import { useCreateWorkflow, useWorkflowTypes } from '../api/hooks'
import { getClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import ApprovalWorkbench from '../components/workflow-workspace/ApprovalWorkbench'
import Sidebar, { type WorkspaceView } from '../components/workflow-workspace/Sidebar'
import OverviewTab from '../components/workflow-workspace/OverviewTab'
import EditorTab from '../components/workflow-workspace/EditorTab'
import ManagementTab from '../components/workflow-workspace/ManagementTab'
import EvolutionTab from '../components/workflow-workspace/EvolutionTab'
import CreateWorkflowModal from '../components/workflow-workspace/CreateWorkflowModal'
import WorkflowPicker from '../components/workflow-workspace/WorkflowPicker'
import StatusBadge from '../components/StatusBadge'
import EmptyState from '../components/EmptyState'
import ErrorState from '../components/ErrorState'
import Dashboard from './Dashboard'
import { beginWorkflowTransition, commitWorkflowTransition, prepareWorkflowTransition, workflowTransitionIsCurrent, type WorkflowTransition } from '../editor/workflowTransition'
import { WorkspacePopPendingContext, claimWorkflowPop, commitWorkflowRoute, permitCommittedWorkflowPop, registerWorkflowPopHandler, consumeWorkflowPop, releaseWorkflowPop, usePackNavigationGuard } from '../editor/usePackNavigationGuard'
import { checkpointOtherPackSessions, hasAnyPackVersionUpload, packVersionsRevision } from '../editor/packVersionSession'
import { useEditorStore } from '../editor/store'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const VIEW_META: Record<Exclude<WorkspaceView, 'dashboard' | 'workbench'>, { title: string; description: string }> = {
  overview: { title: '运行概览', description: '查看当前工作流的运行状态与近期表现' },
  diagnosis: { title: '问题与优化', description: '从异常证据到建议、应用与效果验证' },
  remedies: { title: '可复用经验', description: '查看经过边界审核、可复用的知识与修法' },
  editor: { title: '编辑器', description: '编辑工作流结构、节点与执行配置' },
  management: { title: '管理设置', description: '管理版本、权限、通知与回调配置' },
}

function viewFromSearch(params: URLSearchParams, isAdmin: boolean): WorkspaceView {
  const tab = params.get('tab')
  if (tab === 'dashboard') return isAdmin ? 'dashboard' : 'diagnosis'
  if (tab === 'overview' || tab === 'editor' || tab === 'management' || tab === 'workbench') return tab
  if (tab === 'evolution') {
    const section = params.get('evoTab')
    if (section === 'remedies') return section
    if (section === 'suggestions' || section === 'diagnosis') return 'diagnosis'
    return 'diagnosis'
  }
  return 'overview'
}

export default function WorkflowWorkspace() {
  usePackNavigationGuard()
  const navigate = useNavigate()
  const location = useLocation()
  const navigationType = useNavigationType()
  const locationRef = useRef(location.key)
  locationRef.current = location.key
  const transitionRequest = useRef(0)
  const [transitionPending, setTransitionPending] = useState(false)
  const [transitionError, setTransitionError] = useState<string | null>(null)
  useEffect(() => () => { transitionRequest.current++ }, [])
  const [searchParams, setSearchParams] = useSearchParams()
  const user = getClientUser()
  const isAdmin = user?.isAdmin === true
  const requestedView = viewFromSearch(searchParams, isAdmin)
  const urlWorkflowId = searchParams.get('workflowId')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [legacyOpen, setLegacyOpen] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)

  const { data: workflows, isLoading, isError, error, refetch } = useWorkflowTypes(isAdmin ? undefined : user?.userId)
  const createMutation = useCreateWorkflow()
  const selectedWorkflow = useMemo(() => {
    if (!workflows) return null
    const urlSelection = urlWorkflowId && workflows.some((workflow) => workflow.workflow_id === urlWorkflowId)
      ? urlWorkflowId
      : null
    return workflows.find((workflow) => workflow.workflow_id === (urlSelection ?? selectedId)) ?? workflows[0] ?? null
  }, [workflows, selectedId, urlWorkflowId])
  const workflowId = selectedWorkflow?.workflow_id ?? ''
  const popContext = useRef({ workflowId, workflows, locationKey: location.key })
  popContext.current = { workflowId, workflows, locationKey: location.key }
  const popOwner = useRef<symbol | null>(null)
  const releasePopOwner = () => { if (popOwner.current) { releaseWorkflowPop(popOwner.current); popOwner.current = null } }
  const popSource = useRef<{ index: number; key: string; pathname: string; workflowId: string } | null>(null)
  // On a mounted workspace React Router can render the POP target before our
  // capture listener runs. Unlike a first mount from another route, a different
  // committed source key proves that this workspace owns the traversal.
  const pendingWorkspacePop = navigationType === 'POP' && popSource.current !== null && popSource.current.key !== location.key
  // Router can render the target before our POP listener runs. Keep the
  // current tab mounted while an upload is active so cleanup cannot abort it
  // before the listener has a chance to reject and restore the traversal.
  const committedView = useRef(requestedView)
  const activeView = pendingWorkspacePop && hasAnyPackVersionUpload() ? committedView.current : requestedView
  useLayoutEffect(() => {
    if (!pendingWorkspacePop) committedView.current = requestedView
  }, [pendingWorkspacePop, requestedView])
  const popTransaction = useRef<{
    source: { index: number; key: string; pathname: string; workflowId: string }
    targetIndex: number
    targetHref: string
    targetId: string
    approval: WorkflowTransition | null
    prepared: WorkflowSpec | null
    databaseSpec?: WorkflowSpec | null
    activeDeployNumber?: number | null
    phase: 'restoring' | 'loading' | 'replaying'
    request: number
    syncRouterOnRestore?: boolean
  } | null>(null)

  const rememberCommittedPop = (event: PopStateEvent, workflowId?: string) => {
    const index = typeof event.state?.idx === 'number' ? event.state.idx as number : null
    if (index === null) return
    popSource.current = {
      index,
      key: typeof event.state?.key === 'string' ? event.state.key : popContext.current.locationKey,
      pathname: window.location.pathname,
      workflowId: workflowId ?? new URLSearchParams(window.location.search).get('workflowId') ?? '',
    }
  }

  // Browser POP has already moved history when it is delivered. Restore the
  // original entry without notifying the router, then load and replay the POP.
  // This keeps the original history entries intact on cancellation/failure.
  useLayoutEffect(() => {
    // React Router may commit a POP before our native listener receives the
    // event. Keep the last committed entry as the source during that window;
    // otherwise the guard mistakes the target entry for the source and cannot
    // restore/cancel a dirty browser traversal.
    if ((!popSource.current || navigationType !== 'POP') && typeof window.history.state?.idx === 'number') {
      popSource.current = {
        index: window.history.state.idx,
        key: location.key,
        pathname: window.location.pathname,
        workflowId: workflowId || new URLSearchParams(location.search).get('workflowId') || '',
      }
    }
    // Workflow list may resolve after the initial POP render.
    if (popSource.current?.key === location.key && !popTransaction.current && workflowId) {
      popSource.current.workflowId = workflowId
    }
  }, [location, navigationType, workflowId])

  // A push/replace can supersede a POP while its target read is pending. Release
  // ownership immediately rather than leaving guards disabled until the read settles.
  useLayoutEffect(() => {
    const pending = popTransaction.current
    if (pending?.phase === 'loading' && location.key !== pending.source.key) {
      transitionRequest.current++
      popTransaction.current = null
      releasePopOwner()
    }
  }, [location.key])

  useLayoutEffect(() => {
    let mounted = true
    const pop = (event: PopStateEvent) => {
      const currentIndex = typeof event.state?.idx === 'number' ? event.state.idx as number : null
      const pending = popTransaction.current
      if (pending) {
        if (pending.phase === 'replaying' && currentIndex === pending.targetIndex && window.location.href === pending.targetHref) {
          if (pending.request === transitionRequest.current && pending.prepared && pending.approval
            && workflowTransitionIsCurrent(pending.approval)
            && commitWorkflowTransition(pending.approval, pending.prepared, pending.targetId, false, pending.databaseSpec, pending.activeDeployNumber)) {
            popTransaction.current = null
            releasePopOwner()
            rememberCommittedPop(event, pending.targetId)
            setSelectedId(pending.targetId)
            permitCommittedWorkflowPop(event)
            return
          }
          setTransitionError('切换未完成，原草稿仍保留：确认后编辑内容已变化')
          pending.approval = null
          pending.phase = 'restoring'
        }
        consumeWorkflowPop(event)
        if (currentIndex === pending.source.index && window.location.pathname === pending.source.pathname) {
          if (pending.phase !== 'restoring') {
            pending.approval = null
            transitionRequest.current++
          }
          if (!pending.approval || pending.request !== transitionRequest.current || popContext.current.locationKey !== pending.source.key) {
            popTransaction.current = null
            releasePopOwner()
            if (pending.syncRouterOnRestore) {
              // Router may have already rendered the rejected target before
              // the native listener. Replay only the restored source event so
              // React catches up without replacing the original history entry.
              const restored = new PopStateEvent('popstate', { state: window.history.state })
              permitCommittedWorkflowPop(restored)
              window.dispatchEvent(restored)
            }
            return
          }
          pending.phase = 'loading'
          void readEditorWorkflowSource(pending.targetId).then(({ spec: target, databaseSpec, activeDeployNumber }) => {
            if (!mounted || popTransaction.current !== pending) return
            if (pending.request !== transitionRequest.current || popContext.current.locationKey !== pending.source.key) {
              popTransaction.current = null
              releasePopOwner()
              return
            }
            const prepared = prepareWorkflowTransition(pending.approval!, target, pending.targetId)
            if (!prepared) throw new Error('目标无效或确认后编辑内容已变化')
            pending.prepared = prepared
            pending.databaseSpec = databaseSpec; pending.activeDeployNumber = activeDeployNumber
            pending.phase = 'replaying'
            window.history.go(pending.targetIndex - pending.source.index)
          }).catch(error => {
            if (!mounted || popTransaction.current !== pending) return
            popTransaction.current = null
            releasePopOwner()
            setTransitionError(`切换未完成，原草稿仍保留：${error instanceof Error ? error.message : '目标不可用'}`)
          })
          return
        }
        // A second history traversal supersedes the in-flight transition.
        pending.approval = null
        pending.phase = 'restoring'
        transitionRequest.current++
        if (currentIndex !== null) window.history.go(pending.source.index - currentIndex)
        return
      }
      const source = popSource.current
      const context = popContext.current
      if (!source || currentIndex === null || currentIndex === source.index || source.pathname !== window.location.pathname) return
      const targetId = new URLSearchParams(window.location.search).get('workflowId')
      const selectedTarget = context.workflows?.find(workflow => workflow.workflow_id === targetId) ?? context.workflows?.[0]
      if (!selectedTarget || selectedTarget.workflow_id === source.workflowId) {
        if (hasAnyPackVersionUpload()) {
          // The target can unmount the editor even when workflow identity stays
          // the same. Restore this exact history entry without aborting upload.
          popOwner.current = claimWorkflowPop()
          consumeWorkflowPop(event)
          window.alert('版本文件正在上传，请等待完成后再离开。')
          popTransaction.current = { source, targetIndex: currentIndex, targetHref: window.location.href,
            targetId: selectedTarget?.workflow_id ?? source.workflowId, approval: null, prepared: null,
            phase: 'restoring', request: ++transitionRequest.current, syncRouterOnRestore: true }
          window.history.go(source.index - currentIndex)
          return
        }
        if (selectedTarget) { rememberCommittedPop(event, selectedTarget.workflow_id); permitCommittedWorkflowPop(event) }
        return
      }
      popOwner.current = claimWorkflowPop()
      consumeWorkflowPop(event)
      const request = ++transitionRequest.current
      const approval = beginWorkflowTransition()
      setTransitionError(null)
      popTransaction.current = {
        source, targetIndex: currentIndex, targetHref: window.location.href, targetId: selectedTarget.workflow_id,
        approval, prepared: null, phase: 'restoring', request,
      }
      window.history.go(source.index - currentIndex)
    }
    const unregisterPop = registerWorkflowPopHandler(pop)
    return () => {
      mounted = false
      popTransaction.current = null
      releasePopOwner()
      unregisterPop()
    }
  }, [])

  useEffect(() => {
    if (!legacyOpen) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setLegacyOpen(false) }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [legacyOpen])

  const changeView = useCallback((view: WorkspaceView) => {
    const next = new URLSearchParams(searchParams)
    if (selectedWorkflow) next.set('workflowId', selectedWorkflow.workflow_id)
    if (view === 'dashboard' || view === 'workbench' || view === 'overview' || view === 'editor' || view === 'management') {
      next.set('tab', view)
      next.delete('evoTab')
    } else {
      next.set('tab', 'evolution')
      next.set('evoTab', view)
    }
    setSearchParams(next)
  }, [searchParams, selectedWorkflow, setSearchParams])

  const selectWorkflow = async (id: string) => {
    const request = ++transitionRequest.current
    if (!id || id === workflowId) return
    const next = new URLSearchParams(searchParams)
    next.set('workflowId', id)
    for (const key of ['baseline', 'current', 'repairTaskId', 'runId', 'analysisId', 'issueSignature']) next.delete(key)
    if (activeView === 'dashboard') { next.set('tab', 'evolution'); next.set('evoTab', 'diagnosis') }
    const sourceLocation = locationRef.current
    const approval = beginWorkflowTransition()
    if (!approval) return
    setTransitionError(null)
    try {
      const { spec: target, databaseSpec, activeDeployNumber } = await readEditorWorkflowSource(id)
      if (request !== transitionRequest.current || sourceLocation !== locationRef.current) return
      const prepared = prepareWorkflowTransition(approval, target, id)
      if (!prepared) throw new Error('目标无效或确认后编辑内容已变化')
      const committed = commitWorkflowRoute(approval, () => setSearchParams(next),
        () => commitWorkflowTransition(approval, prepared, id, false, databaseSpec, activeDeployNumber),
        () => setSearchParams(searchParams, { replace: true }))
      if (!committed) throw new Error('路由或画布状态已变化')
      setSelectedId(id)
    } catch (error) {
      if (request === transitionRequest.current) setTransitionError(`切换未完成，原草稿仍保留：${error instanceof Error ? error.message : '目标不可用'}`)
    }
  }

  const handleDeleted = () => {
    const remaining = workflows?.filter((workflow) => workflow.workflow_id !== selectedId) ?? []
    selectWorkflow(remaining[0]?.workflow_id ?? '')
  }

  const handleCreate = useCallback(async (input: { workflowId: string; spec: WorkflowSpec; facade?: { command?: string; remark?: string } }) => {
    if (!user?.userId) throw new Error('无法获取用户信息，请重新登录')
    assertEditorWorkflowReadable(input.workflowId)
    const request = ++transitionRequest.current
    const sourceLocation = locationRef.current
    const approval = beginWorkflowTransition()
    if (!approval) throw new Error('已取消切换，原草稿仍保留')
    if (!prepareWorkflowTransition(approval, input.spec, input.workflowId)) throw new Error('新工作流内容无效，原草稿仍保留')
    const otherPacksUnchanged = checkpointOtherPackSessions(input.workflowId)
    let created = false
    setTransitionPending(true)
    try {
      await createMutation.mutateAsync({ workflowId: input.workflowId, spec: input.spec, facade: input.facade, botOwnerId: user.userId })
      created = true
      const { spec: target, databaseSpec, activeDeployNumber } = await readEditorWorkflowSource(input.workflowId)
      const refreshed = await refetch()
      if (request !== transitionRequest.current || sourceLocation !== locationRef.current) throw new Error('页面已变化')
      if (!refreshed.data?.some(workflow => workflow.workflow_id === input.workflowId)) throw new Error('新工作流尚未出现在列表中')
      if (useEditorStore.getState() !== approval.state || !otherPacksUnchanged()) throw new Error('确认后编辑内容已变化')
      const creationApproval = { ...approval, packRevision: packVersionsRevision() }
      const prepared = prepareWorkflowTransition(creationApproval, target, input.workflowId)
      if (!prepared) throw new Error('目标无效或确认后编辑内容已变化')
      const committed = commitWorkflowRoute(creationApproval,
        () => setSearchParams({ workflowId: input.workflowId, tab: 'editor' }),
        () => commitWorkflowTransition(creationApproval, prepared, input.workflowId, false, databaseSpec, activeDeployNumber),
        () => setSearchParams(searchParams, { replace: true }))
      if (!committed) throw new Error('路由或画布状态已变化')
      setSelectedId(input.workflowId)
    } catch (error) {
      const message = error instanceof Error ? error.message : '请求失败'
      throw new Error(`${created ? '工作流已创建，但未切换；请勿盲目重试。' : '创建结果未确认，请先检查是否已创建后再重试。'}原草稿仍保留：${message}`)
    } finally {
      setTransitionPending(false)
    }
  }, [createMutation, refetch, searchParams, setSearchParams, user?.userId])


  return <WorkspacePopPendingContext.Provider value={pendingWorkspacePop}><div className="flex h-[calc(100vh-3.5rem)] overflow-hidden bg-slate-50">
    <Sidebar compact={activeView === 'editor'} activeView={activeView} onViewChange={changeView} isAdmin={isAdmin} hasWorkflow={Boolean(selectedWorkflow)} />

    <main className="flex min-w-0 flex-1 flex-col bg-slate-50">
      {transitionError && <p role="alert" className="px-4 py-2 text-sm text-destructive">{transitionError}</p>}
      {activeView === 'workbench' ? (<div className="min-h-0 flex-1 overflow-y-auto"><ApprovalWorkbench isAdmin={isAdmin} userId={user?.userId} /></div>) : isError ? (<div className="p-6"><ErrorState message={error instanceof Error ? error.message : '加载工作流失败'} onRetry={() => void refetch()} /></div>) : activeView === 'dashboard' && isAdmin ? (
        <div className="min-h-0 flex-1 overflow-y-auto"><Dashboard embedded /></div>
      ) : selectedWorkflow ? (
        <>
          <header className="flex items-start justify-between gap-4 border-b border-slate-200 bg-white px-7 py-4">
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <h1 className="truncate text-lg font-semibold tracking-tight text-slate-950">{VIEW_META[activeView as Exclude<WorkspaceView, 'dashboard' | 'workbench'>].title}</h1>
                <span className="text-xs text-slate-300">/</span>
                <WorkflowPicker workflows={workflows ?? []} selectedId={selectedWorkflow.workflow_id} onSelect={selectWorkflow} loading={isLoading} />
                <button
                  type="button"
                  aria-label="新建工作流"
                  onClick={() => setCreateOpen(true)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition hover:bg-blue-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/30"
                >
                  <svg aria-hidden="true" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24">
                    <path d="M12 5v14M5 12h14" strokeLinecap="round" />
                  </svg>
                  <span>新建工作流</span>
                </button>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
                <span>{VIEW_META[activeView as Exclude<WorkspaceView, 'dashboard' | 'workbench'>].description}</span><span>·</span><span className="font-mono">{selectedWorkflow.workflow_id}</span><span>·</span><span>{selectedWorkflow.run_count} 次运行</span><StatusBadge status={(selectedWorkflow.last_status as import('@avernet/clawweb-shared/web/types').NodeStatus) ?? 'pending'} />
              </div>
            </div>
            <div className="relative flex shrink-0 items-center gap-3">
              {selectedWorkflow.owner_id && (
                <span
                  className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-1.5 text-xs text-slate-600"
                  title="负责人"
                >
                  负责人{" "}
                  <span className="font-mono text-slate-700">
                    {selectedWorkflow.owner_id}
                  </span>
                </span>
              )}
              <button type="button" onClick={() => setLegacyOpen((open) => !open)} title="旧版入口" className="rounded-lg px-2 py-1 text-sm text-slate-300 hover:bg-slate-100 hover:text-slate-500">···</button>
              {legacyOpen && <><div className="fixed inset-0 z-10" onClick={() => setLegacyOpen(false)} /><div className="absolute right-0 top-full z-20 mt-1 w-40 overflow-hidden rounded-lg border border-slate-200 bg-white py-1 shadow-lg"><button onClick={() => { setLegacyOpen(false); navigate('/workflows') }} className="block w-full px-3 py-2 text-left text-xs text-slate-600 hover:bg-slate-50">旧版工作流</button><button onClick={() => { setLegacyOpen(false); navigate('/editor') }} className="block w-full px-3 py-2 text-left text-xs text-slate-600 hover:bg-slate-50">旧版编辑器</button>{isAdmin && <button onClick={() => { setLegacyOpen(false); navigate('/workflow-management') }} className="block w-full px-3 py-2 text-left text-xs text-slate-600 hover:bg-slate-50">旧版工作流管理</button>}</div></>}
            </div>
          </header>
          {activeView === 'editor' ? <div className="min-h-0 flex-1 overflow-hidden"><EditorTab workflowId={workflowId} /></div> : <div className="min-h-0 flex-1 overflow-y-auto p-6">
            {activeView === 'overview' && <OverviewTab workflow={selectedWorkflow} />}
            {activeView === 'management' && <ManagementTab workflowId={workflowId} workflowTitle={selectedWorkflow.workflow_title} onDeleted={handleDeleted} />}
            {(activeView === 'diagnosis' || activeView === 'remedies') && <EvolutionTab
              workflowId={workflowId}
              runId={searchParams.get('runId') ?? undefined}
              analysisId={searchParams.get('analysisId') ?? undefined}
              issueSignature={searchParams.get('issueSignature') ?? undefined}
              section={activeView}
              onSectionChange={changeView}
            />}
          </div>}
        </>
      ) : <div className="flex flex-1 items-center justify-center"><EmptyState title="暂无可访问的工作流" description="新建工作流后即可开始任务护航" /></div>}
    </main>

    <CreateWorkflowModal open={createOpen} onClose={() => setCreateOpen(false)} onSubmit={handleCreate} isPending={createMutation.isPending || transitionPending} />
  </div></WorkspacePopPendingContext.Provider>
}
