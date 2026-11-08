import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, useLocation, useNavigate } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'

const queryHooks = vi.hoisted(() => ({
  diagnoses: vi.fn(() => ({ data: { diagnoses: [] } })),
  lessons: vi.fn(() => ({ data: { lessons: [] } })),
}))

vi.mock('../../api/hooks', () => ({
  useWorkflowTypes: () => ({
    data: [
      { workflow_id: 'wf-1', workflow_title: '示例工作流', run_count: 4, last_status: 'succeeded', last_run_at: 1, updated_at: 1 },
      { workflow_id: 'wf-2', workflow_title: '备用工作流', run_count: 2, last_status: 'failed', last_run_at: 2, updated_at: 2 },
    ],
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  useCreateWorkflow: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useEvolveDiagnoses: queryHooks.diagnoses,
  useEvolveSuggestions: () => ({ data: { suggestions: [] } }),
  useEvolveLessons: queryHooks.lessons,
}))
vi.mock('@avernet/clawweb-shared/web/hooks/useClientUser', () => ({ getClientUser: () => ({ userId: 'admin-1', isAdmin: true }) }))
vi.mock('../../components/workflow-workspace/OverviewTab', () => ({ default: () => <div>运行概览内容</div> }))
vi.mock('../../components/workflow-workspace/EditorTab', () => ({ default: () => <div>编辑器内容</div> }))
vi.mock('../../components/workflow-workspace/ManagementTab', () => ({ default: () => <div>管理内容</div> }))
vi.mock('../../components/workflow-workspace/EvolutionTab', () => ({ default: () => <div>护航内容</div> }))
vi.mock('../Dashboard', () => ({ default: () => <div>管理员大盘内容</div> }))

import WorkflowWorkspace from '../WorkflowWorkspace'

function LocationProbe() {
  const location = useLocation()
  const navigate = useNavigate()
  return <><button onClick={() => navigate(-1)}>浏览器后退</button><output aria-label="workspace-location">{location.search}</output></>
}

function renderWorkspace(path = '/workflows/workspace?workflowId=wf-1&tab=evolution&evoTab=suggestions') {
  render(<MemoryRouter initialEntries={[path]}><WorkflowWorkspace /><LocationProbe /></MemoryRouter>)
}

describe('unified task escort navigation', () => {
  it('opens the run overview when no tab is specified', () => {
    renderWorkspace('/workflows/workspace?workflowId=wf-1')

    expect(screen.getByRole('button', { name: '运行概览' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('运行概览内容')).toBeInTheDocument()
    expect(queryHooks.diagnoses).not.toHaveBeenCalled()
    expect(queryHooks.lessons).not.toHaveBeenCalled()
  })

  it('does not preload full evolution lists for sidebar counts', () => {
    renderWorkspace('/workflows/workspace?workflowId=wf-1&tab=evolution&evoTab=diagnosis')
    expect(queryHooks.diagnoses).not.toHaveBeenCalled()
    expect(queryHooks.lessons).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '问题与优化' })).toHaveAttribute('aria-current', 'page')
  })

  it('switches workflows from the page header instead of the sidebar', async () => {
    renderWorkspace('/workflows/workspace?workflowId=wf-1&view=version-comparison&baseline=159&current=160')

    const header = screen.getByRole('banner')
    await userEvent.click(within(header).getByRole('button', { name: /切换工作流.*示例工作流/ }))
    await userEvent.click(screen.getByRole('button', { name: /备用工作流.*wf-2/ }))

    expect(screen.getByLabelText('workspace-location')).toHaveTextContent('workflowId=wf-2')
    expect(screen.getByLabelText('workspace-location')).not.toHaveTextContent('baseline=')
    expect(screen.getByLabelText('workspace-location')).not.toHaveTextContent('current=')
    expect(within(header).getByRole('button', { name: /切换工作流.*备用工作流/ })).toBeInTheDocument()
    expect(screen.queryByText('当前工作流')).not.toBeInTheDocument()
  })

  it('keeps workflow creation as a separate header action', async () => {
    renderWorkspace('/workflows/workspace?workflowId=wf-1')

    const header = screen.getByRole('banner')
    expect(within(header).getByRole('button', { name: '新建工作流' })).toBeInTheDocument()

    await userEvent.click(within(header).getByRole('button', { name: /切换工作流.*示例工作流/ }))
    expect(within(screen.getByRole('dialog', { name: '选择工作流' })).queryByRole('button', { name: '新建' })).not.toBeInTheDocument()
  })

  it('maps the legacy suggestion deep link to the combined issue and optimization view', () => {
    renderWorkspace()

    expect(screen.getByRole('button', { name: '问题与优化' })).toHaveAttribute('aria-current', 'page')
    expect(screen.queryByRole('button', { name: /优化建议/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '任务护航' })).not.toBeInTheDocument()
  })

  it('keeps bare legacy evolution links on the issue and optimization view', () => {
    renderWorkspace('/workflows/workspace?workflowId=wf-1&tab=evolution')

    expect(screen.getByRole('button', { name: '问题与优化' })).toHaveAttribute('aria-current', 'page')
    expect(screen.getByText('护航内容')).toBeInTheDocument()
  })

  it('opens the administrator dashboard inside the same workspace shell', async () => {
    renderWorkspace()

    await userEvent.click(screen.getByRole('button', { name: '数据大盘' }))

    expect(screen.getByLabelText('workspace-location')).toHaveTextContent('tab=dashboard')
    expect(screen.getByText('管理员大盘内容')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '数据大盘' })).toHaveAttribute('aria-current', 'page')
  })
})

it('clears the displayed workflow versions after browser Back leaves a different local selection', async () => {
  renderWorkspace('/workflows/workspace?workflowId=wf-1&view=version-comparison&baseline=159&current=160')
  const chooseSecond = async () => {
    await userEvent.click(screen.getByRole('button', { name: /切换工作流.*示例工作流/ }))
    await userEvent.click(screen.getByRole('button', { name: /备用工作流.*wf-2/ }))
  }
  await chooseSecond()
  await userEvent.click(screen.getByRole('button', { name: '浏览器后退' }))
  expect(screen.getByLabelText('workspace-location')).toHaveTextContent('workflowId=wf-1')
  expect(screen.getByLabelText('workspace-location')).toHaveTextContent('baseline=159')
  await chooseSecond()
  expect(screen.getByLabelText('workspace-location')).toHaveTextContent('workflowId=wf-2')
  expect(screen.getByLabelText('workspace-location')).not.toHaveTextContent('baseline=')
  expect(screen.getByLabelText('workspace-location')).not.toHaveTextContent('current=')
})

it('drops workflow-specific deep links when switching and preserves the original link on browser Back', async () => {
  renderWorkspace('/workflows/workspace?workflowId=wf-1&tab=evolution&repairTaskId=FIX-A&runId=RUN-A&analysisId=AN-A&issueSignature=timeout&baseline=1&current=2')
  await userEvent.click(screen.getByRole('button', { name: /切换工作流.*示例工作流/ }))
  await userEvent.click(screen.getByRole('button', { name: /备用工作流.*wf-2/ }))
  const location = screen.getByLabelText('workspace-location')
  expect(location).toHaveTextContent('workflowId=wf-2')
  expect(location).toHaveTextContent('tab=evolution')
  for (const name of ['repairTaskId', 'runId', 'analysisId', 'issueSignature', 'baseline', 'current']) {
    expect(location).not.toHaveTextContent(`${name}=`)
  }
  await userEvent.click(screen.getByRole('button', { name: '浏览器后退' }))
  expect(location).toHaveTextContent('workflowId=wf-1')
  expect(location).toHaveTextContent('repairTaskId=FIX-A')
})
