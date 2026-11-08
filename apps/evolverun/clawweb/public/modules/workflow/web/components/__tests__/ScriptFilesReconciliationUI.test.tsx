import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import ScriptFilesTab from '../ScriptFilesTab'
import { publish, sessionFor, updateVersion, type Snapshot } from '../../editor/packVersionSession'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.resetAllMocks(); vi.unstubAllGlobals() })
let sequence = 0
const SOURCE = 'a'.repeat(40), REMOTE = 'b'.repeat(40)
const REQUEST_ID = '123e4567-e89b-42d3-a456-426614174000'
const SAVE_ERROR = 'Pack 文件未确认保存；数据库草稿已保存。'
function frozenSession() {
  const workflowId = `reconciliation-ui-${++sequence}`
  const session = sessionFor(workflowId)
  const original = { path: 'scripts/start.sh', contentBase64: 'b2xk', mode: '100755' as const }
  const candidate = { ...original, contentBase64: 'ZnJvemVu' }
  const snapshot: Snapshot = { workflowId, packId: 'pack', canWrite: true, baseRef: `refs/tags/deploy/${workflowId}/#1`, baseCommit: SOURCE, draftHead: null, versionFiles: [original] }
  updateVersion(session, 'current', { snapshot, files: [candidate], error: SAVE_ERROR, blocked: true, notice: '本地候选已保留。' })
  publish(session, { ...session.state, version: 'current', reconciliation: {
    attempt: { requestId: REQUEST_ID, expectedPackId: 'pack', snapshot, files: [candidate], input: { requestId: REQUEST_ID, sourceVersion: 'current', sourceCommit: SOURCE, expectedSavedHead: null, changes: [{ ...candidate, operation: 'replace' }] } },
    token: {}, version: 'current', reason: 'no-response', identity: null,
    attemptedFiles: [candidate], sourceCommit: SOURCE, expectedSavedHead: null,
    checking: false, observation: null, checkError: false,
  } })
  return { workflowId, session, snapshot }
}
function readText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = reject
    reader.readAsText(blob)
  })
}

describe('Pack reconciliation presentation', () => {
  it('shows the save error by default and hides only that error when the parent owns its presentation', () => {
    const { workflowId, session } = frozenSession()
    const view = render(<ScriptFilesTab workflowId={workflowId} />)
    expect(screen.getByText(SAVE_ERROR)).toHaveAttribute('role', 'alert')
    view.rerender(<ScriptFilesTab workflowId={workflowId} showSaveError={false} />)
    expect(screen.queryByText(SAVE_ERROR)).not.toBeInTheDocument()
    expect(session.state.versions.current.error).toBe(SAVE_ERROR)
    expect(screen.getByRole('region', { name: '保存结果对账' })).toHaveTextContent('保存结果仍未知')
    expect(screen.getByRole('alert')).toHaveTextContent('系统不保存操作日志')
    expect(screen.getByText('本地候选已保留。')).toHaveAttribute('role', 'status')
    expect(screen.getByText(/未保存更改在本页面会话中保留/)).toBeInTheDocument()
    expect(screen.getByText(/对账期间文件已锁定/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '保存到草稿' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '重新读取' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: '文件内容' })).toHaveValue('frozen')
    expect(fetchJson).not.toHaveBeenCalled()
  })

  it('groups export and HEAD actions, preserves the candidate JSON, and disables observation while pending', async () => {
    const { workflowId, session, snapshot } = frozenSession()
    let downloaded: Blob | undefined
    vi.stubGlobal('URL', { createObjectURL: (blob: Blob) => { downloaded = blob; return 'blob:pack-candidate' }, revokeObjectURL: vi.fn() })
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe('pack-version-candidate.json')
    })
    let resolveHead!: (value: unknown) => void
    vi.mocked(fetchJson).mockImplementationOnce(() => new Promise(resolve => { resolveHead = resolve }))
    render(<ScriptFilesTab workflowId={workflowId} />)
    const actions = screen.getByRole('group', { name: '对账操作' })
    const exportButton = within(actions).getByRole('button', { name: '下载原始尝试候选（JSON）' })
    const headButton = within(actions).getByRole('button', { name: '查看远端草稿 HEAD' })
    expect(exportButton).toHaveClass('focus-visible:ring-2')
    expect(headButton).toHaveClass('disabled:cursor-not-allowed')
    fireEvent.click(exportButton)
    expect(downloaded!.type).toBe('application/json')
    expect(JSON.parse(await readText(downloaded!))).toEqual({
      format: 'pack-version-candidate-v1', workflowId, sourceVersion: 'current', sourceCommit: SOURCE,
      expectedSavedHead: null, requestId: REQUEST_ID, identity: null,
      files: [{ path: 'scripts/start.sh', contentBase64: 'ZnJvemVu', mode: '100755' }],
    })
    fireEvent.click(headButton)
    expect(within(actions).getByRole('button', { name: '正在读取远端 HEAD…' })).toBeDisabled()
    fireEvent.click(headButton)
    expect(exportButton).toBeEnabled()
    expect(fetchJson).toHaveBeenCalledTimes(1)
    expect(fetchJson).toHaveBeenCalledWith(`/api/workflows/${workflowId}/pack-version?version=draft`, { signal: expect.any(AbortSignal) })
    await act(async () => resolveHead({ ...snapshot, baseRef: `origin/wf/${workflowId}`, baseCommit: REMOTE, draftHead: REMOTE }))
    expect(screen.getByRole('region', { name: '保存结果对账' })).toHaveTextContent(`读取时的远端草稿 HEAD：${REMOTE}`)
    expect(headButton).toBeEnabled()
    expect(screen.getByRole('textbox')).toHaveAttribute('readonly')
    expect(screen.getByRole('textbox')).toHaveValue('frozen')
    expect(session.state.versions.current.snapshot!.baseCommit).toBe(SOURCE)
    expect(screen.getByRole('button', { name: '保存到草稿' })).toBeDisabled()
  })

  it('retains export errors, HEAD failures and reconciliation warnings when the save error is hidden', async () => {
    const { workflowId } = frozenSession()
    vi.stubGlobal('URL', { createObjectURL: () => { throw new Error('download unavailable') }, revokeObjectURL: vi.fn() })
    vi.mocked(fetchJson).mockRejectedValueOnce(new Error('private transport failure'))
    render(<ScriptFilesTab workflowId={workflowId} showSaveError={false} />)
    expect(screen.queryByText(SAVE_ERROR)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '下载原始尝试候选（JSON）' }))
    expect(screen.getByText(/下载失败/)).toHaveAttribute('role', 'alert')
    fireEvent.click(screen.getByRole('button', { name: '查看远端草稿 HEAD' }))
    expect(await screen.findByText(/无法读取远端 HEAD/)).toHaveTextContent('未清除对账状态或本地草稿')
    expect(screen.getByText(/原始候选可导出为 JSON/)).toHaveTextContent('敏感信息')
    expect(screen.getByText(/系统不保存操作日志/)).toHaveAttribute('role', 'alert')
    expect(screen.getByRole('textbox')).toHaveValue('frozen')
  })
})
