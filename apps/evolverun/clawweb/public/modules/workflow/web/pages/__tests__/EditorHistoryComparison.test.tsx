import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

const mocks = vi.hoisted(() => ({ history: vi.fn(), snapshot: vi.fn(), fetchJson: vi.fn(), spec: null as WorkflowSpec | null }))
vi.mock('../../api/hooks', () => ({
  useEditorWorkflowDocument: () => ({ data: mocks.spec, databaseSpec: mocks.spec, isLoading: false }),
  useAutoHealDeploy: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))
vi.mock('@avernet/clawweb-shared/web/api/client', () => ({
  fetchJson: mocks.fetchJson,
  api: { workflows: { getHistory: mocks.history, getDeploySnapshot: mocks.snapshot } },
}))
vi.mock('../../components/WorkflowPackSidebar', () => ({ default: () => null }))
vi.mock('../../components/EditorCanvas', () => ({ default: () => null }))
vi.mock('../../components/NodePalette', () => ({ default: () => null }))
vi.mock('../../components/NodePropertyPanel', () => ({ default: () => null }))
vi.mock('../../components/editor/WorkflowSettingsPanel', () => ({ default: () => null }))
vi.mock('../../components/YamlEditor', () => ({ default: () => null }))
vi.mock('../../components/WorkflowHistoryPanel', () => ({ default: () => null }))
import Editor from '../Editor'
import { useEditorStore } from '../../editor/store'

beforeEach(() => {
  vi.resetAllMocks()
  useEditorStore.getState().reset({ force: true })
  mocks.spec = { id: 'comparison-test', title: 'database', version: '1', nodes: [] }
  useEditorStore.getState().loadSpec(mocks.spec, mocks.spec)
  mocks.history.mockResolvedValue({ history: [], active: { deployNumber: 7, version: 3 } })
  mocks.fetchJson.mockRejectedValue(new Error('not needed'))
})
afterEach(() => { cleanup(); useEditorStore.getState().reset({ force: true }) })

async function openWithHistory(raw: string) {
  mocks.snapshot.mockResolvedValue({ deployNumber: 7, version: 3, specJson: raw })
  render(<Editor embedded initialWorkflowId="comparison-test" />)
  await waitFor(() => expect(mocks.snapshot).toHaveBeenCalledWith('comparison-test', 7))
  await act(async () => {})
}

it.each([
  ['top-level marker', '{"id":"comparison-test","__clawweb_snapshot_v1":false,"nodes":[]}'],
  ['string content', '{"id":"comparison-test","content":"nodes: []","nodes":[]}'],
  ['duplicate keys', '{"id":"comparison-test","nodes":[],"nodes":[]}'],
  ['unsafe integer', '{"id":"comparison-test","nodes":[],"count":9007199254740993}'],
  ['malformed document', '{"id":'],
  ['non-object document', '"nodes: []"'],
])('does not assert a difference for ambiguous active history: %s', async (_label, raw) => {
  await openWithHistory(raw)
  expect(await screen.findByText(/无法判断数据库内容与生效 v3 是否有差异/)).toBeInTheDocument()
  expect(screen.queryByText('数据库内容与生效 v3 有差异')).not.toBeInTheDocument()
})

it('reports uncertainty even when ambiguous raw content matches the database', async () => {
  mocks.spec = { ...mocks.spec!, __clawweb_snapshot_v1: true } as WorkflowSpec
  useEditorStore.getState().loadSpec(mocks.spec, mocks.spec)
  await openWithHistory(JSON.stringify(mocks.spec))
  expect(await screen.findByText(/无法判断数据库内容与生效 v3 是否有差异/)).toBeInTheDocument()
  expect(screen.queryByText('数据库内容与生效 v3 有差异')).not.toBeInTheDocument()
})

it('still reports an unambiguous difference', async () => {
  await openWithHistory(JSON.stringify({ ...mocks.spec, title: 'deployed' }))
  expect(await screen.findByText('数据库内容与生效 v3 有差异')).toBeInTheDocument()
  expect(screen.queryByText(/无法判断数据库内容/)).not.toBeInTheDocument()
})

it('does not report a difference for an equal unambiguous snapshot', async () => {
  await openWithHistory(JSON.stringify(mocks.spec))
  await waitFor(() => expect(screen.getByText(/生效 v3 · deploy #7/)).toBeInTheDocument())
  expect(screen.queryByText(/无法判断数据库内容/)).not.toBeInTheDocument()
  expect(screen.queryByText('数据库内容与生效 v3 有差异')).not.toBeInTheDocument()
})

it('does not treat nested markers or non-string content as ambiguous', async () => {
  await openWithHistory(JSON.stringify({ ...mocks.spec, content: { __clawweb_snapshot_v1: true } }))
  expect(await screen.findByText('数据库内容与生效 v3 有差异')).toBeInTheDocument()
  expect(screen.queryByText(/无法判断数据库内容/)).not.toBeInTheDocument()
})
