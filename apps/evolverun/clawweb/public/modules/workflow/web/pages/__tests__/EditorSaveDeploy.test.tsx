import type { ReactNode } from 'react'
import { render as renderView, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  // Canvas spec (zustand store snapshot) and the persisted DB spec the editor compares against.
  spec: { id: 'wf-1', version: 3, title: '测试工作流', nodes: [] },
  dbWorkflow: { id: 'wf-1', version: 3, title: '测试工作流', nodes: [] },
  isDirty: false,
  sessionId: 1,
  pendingEdits: {} as Record<string, string>,
  saveDb: vi.fn(),
  gitSave: vi.fn(),
  deploy: vi.fn(),
  getHistory: vi.fn(),
  getDeploySnapshot: vi.fn(),
  loadSpec: vi.fn(),
  completeSave: vi.fn(),
  updateSpecField: vi.fn(),
}))

vi.mock('../../api/hooks', () => ({
  useDbWorkflow: () => ({ data: mocks.dbWorkflow, isLoading: false }),
  useSaveWorkflowToDb: () => ({ mutateAsync: mocks.saveDb, isPending: false }),
  useFacadeBindings: () => ({ data: [] }),
  useAutoHealSave: () => ({ mutateAsync: mocks.gitSave, isPending: false }),
  useAutoHealDeploy: () => ({ mutateAsync: mocks.deploy, isPending: false }),
}))

vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({
  getClientUser: () => ({ userId: 'user-1' }),
}))

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({
  api: {
    workflows: {
      getHistory: mocks.getHistory,
      getDeploySnapshot: mocks.getDeploySnapshot,
    },
  },
}))

vi.mock('../../editor/store', () => {
  const useEditorStore = Object.assign(
    () => ({
      spec: mocks.spec,
      isDirty: mocks.isDirty,
      selectedNodeId: null,
      validationErrors: [],
      pendingEdits: mocks.pendingEdits,
      canUndo: false, canRedo: false, undo: vi.fn(), redo: vi.fn(), setActionError: vi.fn(),
      loadSpec: mocks.loadSpec,
      createNew: vi.fn(),
      importYaml: vi.fn(),
      selectNode: vi.fn(),
      completeSave: mocks.completeSave,
    }),
    { getState: () => ({ sessionId: mocks.sessionId, spec: mocks.spec, completeSave: mocks.completeSave, updateSpecField: mocks.updateSpecField, pendingEdits: mocks.pendingEdits }) },
  )
  return { useEditorStore }
})

vi.mock('@xyflow/react', () => ({
  ReactFlowProvider: (props: { children?: ReactNode }) => props.children ?? null,
}))

vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))

import Editor from '../Editor'

function renderEditor() {
  return renderView(<Editor embedded initialWorkflowId="wf-1" />)
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.sessionId = 1
  mocks.isDirty = false
  mocks.pendingEdits = {}
  mocks.getHistory.mockResolvedValue({ active: null, history: [] })
  mocks.getDeploySnapshot.mockResolvedValue(null)
  mocks.saveDb.mockResolvedValue(mocks.spec)
  mocks.gitSave.mockResolvedValue({
    ok: true, saved: true, workflowId: 'wf-1', packId: 'wf-1',
    branch: 'saved/wf-1', commit: 'abcd1234ef56', version: 4,
  })
  mocks.completeSave.mockReturnValue(true)
  mocks.deploy.mockResolvedValue({
    ok: true, deployed: true, workflowId: 'wf-1', packId: 'wf-1',
    deployNumber: 5, version: 9, tagName: 'deploy/wf-1/#5', messageId: null,
  })
})

describe('editor save (DB + git snapshot) and server-side deploy', () => {
  it('blocks save and deploy while configuration or YAML has an unapplied draft', async () => {
    mocks.pendingEdits = { yaml: '未应用' }
    renderEditor()
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(screen.getByText('请先应用或放弃未完成的配置草稿，再保存。')).toBeInTheDocument()
    expect(mocks.saveDb).not.toHaveBeenCalled()
    await userEvent.click(screen.getByRole('button', { name: '部署' }))
    expect(screen.getByText('存在未应用的配置草稿，请先应用并保存再部署。')).toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
  })

  it('saves to the DB first, then snapshots to git and reports the saved branch and commit', async () => {
    renderEditor()
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await userEvent.click(screen.getByRole('button', { name: '保存工作流' }))
    await waitFor(() => expect(mocks.gitSave).toHaveBeenCalledTimes(1))
    expect(mocks.saveDb).toHaveBeenCalledWith(
      expect.objectContaining({ workflowId: 'wf-1', skipDeployHistory: true, botOwnerId: 'user-1' }),
    )
    expect(mocks.gitSave).toHaveBeenCalledWith(
      expect.objectContaining({ workflowId: 'wf-1', spec: expect.anything(), note: 'editor save' }),
    )
    expect(mocks.completeSave).toHaveBeenCalled()
    expect(await screen.findByText(/工作流已保存（git saved\/wf-1 · abcd1234）/)).toBeInTheDocument()
  })

  it('gives save completion the original document snapshot and requested rename identity', async () => {
    mocks.gitSave.mockImplementationOnce(({ workflowId }: { workflowId: string }) => ({
      ok: true, saved: true, workflowId, packId: workflowId,
      branch: `saved/${workflowId}`, commit: 'abcd1234ef56', version: 4,
    }))
    renderEditor()
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await userEvent.clear(screen.getByLabelText('标识'))
    await userEvent.type(screen.getByLabelText('标识'), 'wf-2')
    await userEvent.click(screen.getByRole('button', { name: '保存工作流' }))
    await waitFor(() => expect(mocks.completeSave).toHaveBeenCalledTimes(1))
    const [savedSpec, requestSpec, sessionId] = mocks.completeSave.mock.calls[0]
    expect(savedSpec).toMatchObject({ id: 'wf-2', title: '测试工作流' })
    expect(requestSpec).toMatchObject({ id: 'wf-1', title: '测试工作流' })
    expect(sessionId).toBe(1)
    expect(mocks.saveDb).toHaveBeenCalledWith(expect.objectContaining({ workflowId: 'wf-2', originalWorkflowId: 'wf-1' }))
    expect(await screen.findByText(/工作流已保存（git saved\/wf-2/)).toBeInTheDocument()
  })

  it('routes a stale save result through the rejecting session boundary', async () => {
    mocks.completeSave.mockImplementationOnce(() => false)
    let resolveDb!: (value: unknown) => void
    mocks.saveDb.mockImplementationOnce(() => new Promise(resolve => { resolveDb = resolve }))
    renderEditor()
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await userEvent.click(screen.getByRole('button', { name: '保存工作流' }))
    expect(mocks.saveDb).toHaveBeenCalledTimes(1)
    mocks.sessionId += 1
    resolveDb(mocks.spec)
    await waitFor(() => expect(mocks.gitSave).toHaveBeenCalledTimes(1))
    expect(mocks.completeSave).toHaveBeenCalledTimes(1)
    expect(mocks.completeSave).toHaveLastReturnedWith(false)
    expect(mocks.updateSpecField).not.toHaveBeenCalled()
    expect(screen.queryByText(/工作流已保存（git/)).not.toBeInTheDocument()
  })

  it('keeps the DB save and shows a degraded warning when the git snapshot fails', async () => {
    mocks.gitSave.mockRejectedValue(new Error('git push failed'))
    renderEditor()
    await userEvent.click(screen.getByRole('button', { name: '保存' }))
    await userEvent.click(screen.getByRole('button', { name: '保存工作流' }))
    expect(await screen.findByText(/数据库已保存，但 git 快照失败：git push failed/)).toBeInTheDocument()
    expect(mocks.saveDb).toHaveBeenCalledTimes(1)
    expect(mocks.completeSave).toHaveBeenCalled()
  })

  it('blocks deploy while the canvas has unsaved edits', async () => {
    mocks.isDirty = true
    renderEditor()
    await userEvent.click(screen.getByRole('button', { name: '部署' }))
    expect(screen.getByText('画布有未保存改动，请先保存再部署。')).toBeInTheDocument()
    expect(mocks.deploy).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '确认部署' })).not.toBeInTheDocument()
  })

  it('deploys server-side from the lightweight confirm dialog and reports deploy number and tag', async () => {
    renderEditor()
    await userEvent.click(screen.getByRole('button', { name: '部署' }))
    expect(screen.getByText(/该工作流尚无线上生效版本/)).toBeInTheDocument()
    // The fallback hint splits across <p> and <code> elements — assert both parts.
    expect(screen.getByText(/Bot 命令兜底：在 Bot 对话执行/)).toBeInTheDocument()
    expect(screen.getByText('/workflow deploy wf-1')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '确认部署' }))
    await waitFor(() =>
      expect(mocks.deploy).toHaveBeenCalledWith({ workflowId: 'wf-1', note: 'editor deploy', autoRun: false }),
    )
    expect(await screen.findByText(/部署成功：deploy #5 · v9 · deploy\/wf-1\/#5/)).toBeInTheDocument()
  })

  it('suggests the Bot command fallback when the direct deploy fails', async () => {
    mocks.deploy.mockRejectedValue(new Error('tag push failed'))
    renderEditor()
    await userEvent.click(screen.getByRole('button', { name: '部署' }))
    await userEvent.click(screen.getByRole('button', { name: '确认部署' }))
    expect(await screen.findByText(/部署失败：tag push failed。兜底：在 Bot 对话执行 \/workflow deploy wf-1/)).toBeInTheDocument()
  })

  it('routes deploy through the diff confirmation dialog when the saved spec diverges from the live version', async () => {
    mocks.getHistory.mockResolvedValue({
      active: { workflowId: 'wf-1', deployNumber: 3, version: 2, action: 'deploy' },
      history: [],
    })
    mocks.getDeploySnapshot.mockResolvedValue({
      deployNumber: 3,
      version: 2,
      specJson: JSON.stringify({ content: 'id: wf-1\nversion: 2\ntitle: 旧标题\nnodes: []\n' }),
    })
    renderEditor()
    await screen.findByText('与生效 v2 有差异 · 待部署')
    await userEvent.click(screen.getByRole('button', { name: '部署' }))
    expect(screen.getByText('部署前差异')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '确认部署' }))
    await waitFor(() =>
      expect(mocks.deploy).toHaveBeenCalledWith({ workflowId: 'wf-1', note: 'editor deploy', autoRun: false }),
    )
  })

  it('warns about a missing snapshot when a deploy record exists and still deploys after confirm', async () => {
    mocks.getHistory.mockResolvedValue({
      active: { workflowId: 'wf-1', deployNumber: 3, version: 2, action: 'deploy' },
      history: [],
    })
    mocks.getDeploySnapshot.mockRejectedValue(new Error('boom'))
    renderEditor()
    // Wait for the baseline chip so latestDeploy is set before opening the confirm dialog.
    await screen.findByText(/生效 v2 · deploy #3/)
    await userEvent.click(screen.getByRole('button', { name: '部署' }))
    expect(await screen.findByText(/无法获取线上生效版本的快照/)).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '确认部署' }))
    await waitFor(() =>
      expect(mocks.deploy).toHaveBeenCalledWith({ workflowId: 'wf-1', note: 'editor deploy', autoRun: false }),
    )
  })
})
