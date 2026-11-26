import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import RunArchivePanel from '../RunArchivePanel'
import { ARCHIVE_FLOW_ID, createArchiveFixture } from './RunArchivePanel.fixture'

const { useRunArchive } = vi.hoisted(() => ({ useRunArchive: vi.fn() }))
vi.mock('@avernet/workflow/web/api/hooks', () => ({ useRunArchive }))
const NativeURL = URL

beforeEach(() => {
  vi.stubGlobal('URL', class extends NativeURL {
    static createObjectURL = vi.fn(() => 'blob:synthetic-archive-download')
    static revokeObjectURL = vi.fn()
  })
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

async function openSection(name: RegExp, data = createArchiveFixture()) {
  const user = userEvent.setup()
  useRunArchive.mockReturnValue({ data, isLoading: false, isError: false, error: null })
  render(<RunArchivePanel flowId={ARCHIVE_FLOW_ID} runStatus="failed" />)
  await user.click(within(screen.getByRole('navigation', { name: /运行档案分区/ })).getByRole('button', { name }))
  return user
}

function yamlCode(content: string) {
  return screen.getByText((_text, element) => element?.tagName === 'PRE' && element.textContent === content)
}

describe('RunArchivePanel evidence and diagnosis', () => {
  it('renders non-JSON input and output as original text rather than throwing', async () => {
    const data = createArchiveFixture()
    const failed = data.nodeExecutions.find(node => node.node_id === 'node-failed')!
    failed.input_json = 'plain input: not JSON { broken'
    failed.output_json = '<plain output>not JSON</plain output>'
    const user = await openSection(/错误详情/, data)
    await user.click(screen.queryByRole('button', { name: /^Input$/i }) ?? screen.getByText(/^Input$/i, { selector: 'summary' }))
    expect(screen.getByLabelText(/node-failed Input/)).toBeVisible()
    expect(screen.getByLabelText(/node-failed Input/)).toHaveTextContent(String(failed.input_json))
    await user.click(screen.queryByRole('button', { name: /^Output$/i }) ?? screen.getByText(/^Output$/i, { selector: 'summary' }))
    expect(screen.getByLabelText(/node-failed Output/)).toBeVisible()
    expect(screen.getByLabelText(/node-failed Output/)).toHaveTextContent(String(failed.output_json))
    expect(screen.getByText('Synthetic node failure')).toBeVisible()
  })

  it.each([
    ['failed', 'failed', /失败|不可用/],
    ['skipped', 'reused', /跳过|未执行|未生成/],
    ['completed', 'reused', /暂无.*诊断|未.*诊断|没有.*诊断/],
  ] as const)('explains analysis status %s with no diagnoses and keeps a real next step', async (status, source, state) => {
    const data = createArchiveFixture()
    data.analysis = { ...data.analysis!, status, source, diagnoses: [], error: status === 'failed' ? 'Synthetic analysis unavailable' : null }
    const user = await openSection(/AI\s*诊断/, data)
    expect(screen.getAllByText(state).length).toBeGreaterThan(0)
    if (status === 'failed') expect(screen.getByText(/Synthetic analysis unavailable/)).toBeVisible()
    expect(screen.queryByRole('button', { name: /生成诊断|重新分析|开始分析/ })).not.toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: /查看.*(?:运行过程|节点|执行|事件|错误)/ })[0])
    expect(screen.getByRole('heading', { level: 2, name: /运行过程|错误详情/ })).toBeVisible()
  })

  it('preserves existing diagnosis and YAML evidence even when analysis is marked failed', async () => {
    const data = createArchiveFixture()
    data.analysis = { ...data.analysis!, status: 'failed', source: 'failed', error: 'Synthetic later analysis failed' }
    const user = await openSection(/AI\s*诊断/, data)
    expect(screen.getByText('Synthetic diagnosis reasoning')).toBeVisible()
    expect(screen.getByText('Synthetic fix: update the input mapping')).toBeVisible()
    expect(screen.getByText(/Synthetic later analysis failed/)).toBeVisible()
    await user.click(screen.getByRole('button', { name: /^建议\s*YAML$/i }))
    expect(yamlCode(data.suggestedYaml!.yamlContent)).toBeVisible()
  })
})

describe('RunArchivePanel suggested YAML actions', () => {
  it('shows an explicit patch absence instead of an empty comparison pane', async () => {
    const data = createArchiveFixture()
    data.suggestedYaml = { ...data.suggestedYaml!, patchProposal: null }
    const user = await openSection(/建议\s*YAML/i, data)
    expect(yamlCode(data.suggestedYaml.yamlContent)).toBeVisible()
    await user.click(screen.getByRole('button', { name: /Patch.*对比|Diff|补丁.*对比/i }))
    expect(screen.getByRole('heading', { name: /暂无.*(?:Patch|补丁|操作)|未.*(?:Patch|补丁)|没有.*(?:Patch|补丁)/i })).toBeVisible()
    expect(screen.queryByText(/原始\s*YAML.*(?:id:|nodes:)/i)).not.toBeInTheDocument()
  })

  it('shows the actual patch operations and can return to the real suggested YAML', async () => {
    const data = createArchiveFixture()
    const user = await openSection(/建议\s*YAML/i, data)
    await user.click(screen.getByRole('button', { name: /Patch.*对比|Diff|补丁.*对比/i }))
    const table = screen.getByRole('table', { name: /Patch/i })
    expect(within(table).getByRole('cell', { name: /replace.*\/nodes\/node-failed\/input/ })).toBeVisible()
    await user.click(screen.getByRole('button', { name: /建议\s*YAML\s*内容/i }))
    expect(yamlCode(data.suggestedYaml!.yamlContent)).toBeVisible()
  })

  it.each([
    { value: null, label: 'null' },
    { value: '', label: 'an empty string' },
    { value: false, label: 'false' },
    { value: 0, label: 'zero' },
  ])('preserves a recorded Patch value of $label instead of calling it missing', async ({ value }) => {
    const data = createArchiveFixture()
    data.suggestedYaml!.patchProposal!.operations[0].value = value
    const user = await openSection(/建议\s*YAML/i, data)
    await user.click(screen.getByRole('button', { name: /Patch.*对比|Diff|补丁.*对比/i }))
    const suggestedValue = screen.getByLabelText(/Patch 1 操作 1 建议值/)
    expect(suggestedValue).toBeVisible()
    expect(suggestedValue.textContent).toBe(JSON.stringify(value))
  })

  it('copies the actual YAML with a visible success acknowledgement', async () => {
    const data = createArchiveFixture()
    const user = await openSection(/建议\s*YAML/i, data)
    const writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue()
    await user.click(screen.getByRole('button', { name: /复制.*YAML/i }))
    expect(writeText).toHaveBeenCalledWith(data.suggestedYaml!.yamlContent)
    expect(await screen.findByRole('status')).toHaveTextContent(/复制|copied/i)
  })

  it('reports a rejected clipboard operation without an unhandled rejection', async () => {
    const user = await openSection(/建议\s*YAML/i)
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new Error('Synthetic clipboard denied'))
    await user.click(screen.getByRole('button', { name: /复制.*YAML/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/复制.*(?:失败|不可用)|无法.*复制|copy.*fail/i)
    expect(screen.queryByText(/^(?:复制成功|已复制)[。！!]?$/)).not.toBeInTheDocument()
  })

  it('reports an unavailable Clipboard API instead of silently failing', async () => {
    const user = await openSection(/建议\s*YAML/i)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
    await user.click(screen.getByRole('button', { name: /复制.*YAML/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/复制.*(?:失败|不可用)|无法.*复制|copy.*fail/i)
  })

  it('downloads a YAML file, acknowledges success, and releases its object URL', async () => {
    const user = await openSection(/建议\s*YAML/i)
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    await user.click(screen.getByRole('button', { name: /下载.*YAML/i }))
    expect(URL.createObjectURL).toHaveBeenCalledWith(expect.any(Blob))
    expect(click).toHaveBeenCalledOnce()
    const anchor = click.mock.contexts[0] as HTMLAnchorElement
    expect(anchor.download).toMatch(/\.ya?ml$/i)
    expect(anchor.href).toBe('blob:synthetic-archive-download')
    await waitFor(() => expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:synthetic-archive-download'))
    expect(await screen.findByRole('status')).toHaveTextContent(/下载|download/i)
  })

  it.each(['create', 'click'] as const)('reports download failure at %s and cleans up an already allocated object URL', async (failure) => {
    const user = await openSection(/建议\s*YAML/i)
    if (failure === 'create') vi.mocked(URL.createObjectURL).mockImplementation(() => { throw new Error('Synthetic object URL unavailable') })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      if (failure === 'click') throw new Error('Synthetic download blocked')
    })
    await user.click(screen.getByRole('button', { name: /下载.*YAML/i }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/下载.*失败|无法.*下载|download.*fail/i)
    if (failure === 'click') expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:synthetic-archive-download')
    else expect(click).not.toHaveBeenCalled()
  })
})
