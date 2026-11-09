import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import ScriptFilesTab from '../ScriptFilesTab'
import { sessionFor } from '../../editor/packVersionSession'

vi.mock('@avernet/clawweb-shared/web/api/client', () => ({ fetchJson: vi.fn() }))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.resetAllMocks()
})

const COMMIT = 'a'.repeat(40)
const GENERIC_READ_FAILURE = '无法读取完整版本文件，服务响应可能不兼容，请稍后重试。'
const SNAPSHOT_CONFLICT_FAILURE = '同一工作流匹配到多个 Pack，或 Pack 布局存在冲突；暂无法安全读取，请先整理重复的 Pack 或冲突的目录布局，再重新读取。'
const VERSION_CONFLICT_FAILURE = '版本已发生变化，请刷新或重新读取后再继续。'
let sequence = 0

function workflowId() { return `read-errors-${++sequence}` }
function snapshot(id: string, canWrite?: boolean) {
  return {
    workflowId: id,
    packId: 'pack',
    baseRef: `origin/wf/${id}`,
    baseCommit: COMMIT,
    draftHead: COMMIT,
    versionFiles: [{ path: 'scripts/a', contentBase64: 'b2xk', mode: '100755' }],
    ...(canWrite === undefined ? {} : { canWrite }),
  }
}
function rejectRead(status: number | undefined, body?: unknown) {
  return Object.assign(new Error('private backend details'), { status, body })
}
async function loaded(id: string, canWrite?: boolean) {
  vi.mocked(fetchJson).mockResolvedValueOnce(snapshot(id, canWrite))
  render(<ScriptFilesTab workflowId={id} />)
  await screen.findByRole('button', { name: 'scripts/a' })
}

const disabledCopy = 'Pack 版本文件写入尚未启用'
const enabledCopy = '可编辑 Pack 版本文件'

describe('ScriptFilesTab read failures and capabilities', () => {
  it('does not show a disabled capability banner while the snapshot read is pending', async () => {
    let resolve!: (value: unknown) => void
    vi.mocked(fetchJson).mockImplementationOnce(() => new Promise(result => { resolve = result }))
    const id = workflowId()
    render(<ScriptFilesTab workflowId={id} />)

    await waitFor(() => expect(fetchJson).toHaveBeenCalledOnce())
    expect(screen.getByText('正在读取版本文件…')).toBeInTheDocument()
    expect(screen.queryByText(new RegExp(disabledCopy))).not.toBeInTheDocument()
    expect(screen.queryByText(new RegExp(enabledCopy))).not.toBeInTheDocument()
    await act(async () => { resolve(snapshot(id)); await Promise.resolve() })
    await screen.findByRole('button', { name: 'scripts/a' })
  })

  it('shows the safe Pack-layout diagnosis only for an exact top-level SNAPSHOT_CONFLICT code', async () => {
    const id = workflowId()
    vi.mocked(fetchJson).mockRejectedValueOnce(rejectRead(409, JSON.stringify({ code: 'SNAPSHOT_CONFLICT', error: 'backend secret' })))
    render(<ScriptFilesTab workflowId={id} />)

    expect(await screen.findByRole('alert')).toHaveTextContent(SNAPSHOT_CONFLICT_FAILURE)
    expect(screen.queryByText(new RegExp(disabledCopy))).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).not.toHaveTextContent('backend secret')
  })

  it('keeps VERSION_CONFLICT as a refresh message without diagnosing Pack layout', async () => {
    const id = workflowId()
    vi.mocked(fetchJson).mockRejectedValueOnce(rejectRead(409, JSON.stringify({ code: 'VERSION_CONFLICT', message: 'private detail' })))
    render(<ScriptFilesTab workflowId={id} />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(VERSION_CONFLICT_FAILURE)
    expect(alert).not.toHaveTextContent(/Pack|布局|重复/)
    expect(alert).not.toHaveTextContent('private detail')
  })

  it.each([
    ['absent body', undefined],
    ['array body', JSON.stringify([{ code: 'SNAPSHOT_CONFLICT' }])],
    ['malformed code', JSON.stringify({ code: ['SNAPSHOT_CONFLICT'] })],
    ['arbitrary 409', JSON.stringify({ code: 'OTHER', error: 'secret' })],
    ['nested code', JSON.stringify({ details: { code: 'SNAPSHOT_CONFLICT' } })],
    ['non-JSON body', '<html>secret proxy page</html>'],
    ['oversized body', JSON.stringify({ code: 'SNAPSHOT_CONFLICT', padding: 'x'.repeat(8200) })],
  ])('uses the generic safe read failure for %s', async (_label, body) => {
    vi.mocked(fetchJson).mockRejectedValueOnce(rejectRead(409, body))
    render(<ScriptFilesTab workflowId={workflowId()} />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(GENERIC_READ_FAILURE)
    expect(alert).not.toHaveTextContent(/SNAPSHOT_CONFLICT|Pack|布局|重复/)
    expect(alert).not.toHaveTextContent('secret')
  })

  it.each([
    [401, '请先登录'],
    [403, '没有权限或请求来源不被允许'],
    [404, '版本或接口不可用'],
    [503, '版本服务暂不可用'],
    [500, GENERIC_READ_FAILURE],
    [undefined, GENERIC_READ_FAILURE],
  ])('keeps status %s in a safe read-error category', async (status, expected) => {
    vi.mocked(fetchJson).mockRejectedValueOnce(rejectRead(status, JSON.stringify({ error: 'secret' })))
    render(<ScriptFilesTab workflowId={workflowId()} />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent(expected)
    expect(alert).not.toHaveTextContent('secret')
  })

  it.each([
    [false, disabledCopy],
    [undefined, disabledCopy],
    [true, enabledCopy],
  ])('retains the existing %s capability behavior after a successful read', async (canWrite, expectedCopy) => {
    const id = workflowId()
    await loaded(id, canWrite as boolean | undefined)
    expect(screen.getByText(new RegExp(expectedCopy))).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'scripts/a' }))
    fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'local edit' } })
    if (canWrite === true) expect(screen.getByRole('button', { name: '保存到草稿' })).toBeEnabled()
    else expect(screen.getByRole('button', { name: '保存到草稿' })).toBeDisabled()
    expect(screen.getByLabelText('文件内容')).toHaveValue('local edit')
  })

  it.each([
    [true, false, disabledCopy],
    [false, true, enabledCopy],
  ])('retains local edits and hides capability during failed reload, then restores the retry capability', async (initialCanWrite, retryCanWrite, retryCopy) => {
    const id = workflowId()
    vi.mocked(fetchJson).mockResolvedValueOnce(snapshot(id, initialCanWrite))
      .mockRejectedValueOnce(rejectRead(503, JSON.stringify({ error: 'secret reload detail' })))
      .mockResolvedValueOnce(snapshot(id, retryCanWrite))
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    render(<ScriptFilesTab workflowId={id} />)

    await screen.findByRole('button', { name: 'scripts/a' })
    fireEvent.click(screen.getByRole('button', { name: 'scripts/a' }))
    fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'local edit' } })
    fireEvent.click(screen.getByRole('button', { name: '重新读取' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('版本服务暂不可用')
    expect(screen.getByLabelText('文件内容')).toHaveValue('local edit')
    expect(screen.getByRole('button', { name: '保存到草稿' })).toBeDisabled()
    expect(screen.queryByText(new RegExp(disabledCopy))).not.toBeInTheDocument()
    expect(screen.queryByText(new RegExp(enabledCopy))).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '重新读取' }))
    await waitFor(() => expect(screen.getByText(new RegExp(retryCopy))).toBeInTheDocument())
  })

  it('leaves the safe read failure for the parent without a duplicate local alert', async () => {
    const id = workflowId()
    vi.mocked(fetchJson).mockRejectedValueOnce(rejectRead(503, JSON.stringify({ error: 'secret' })))
    render(<ScriptFilesTab workflowId={id} showSaveError={false} />)

    await waitFor(() => expect(sessionFor(id).state.versions.draft.readFailure).toBeTruthy())
    expect(sessionFor(id).state.versions.draft.error).toBe('版本服务暂不可用，请稍后重试。')
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByText(new RegExp(disabledCopy))).not.toBeInTheDocument()
    expect(screen.queryByText(new RegExp(enabledCopy))).not.toBeInTheDocument()
  })
})
