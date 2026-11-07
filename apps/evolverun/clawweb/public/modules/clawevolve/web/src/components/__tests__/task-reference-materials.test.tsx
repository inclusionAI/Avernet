// @vitest-environment jsdom
import React from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { TaskReferenceMaterialsField, TaskReferenceMaterialsDetail } from '../TaskReferenceMaterials'
import { useTaskReferenceMaterials, validateSelection } from '../../hooks/useTaskReferenceMaterials'
import type { MaterialOptions } from '../../api/reference-materials'

const api = vi.hoisted(() => ({ options: vi.fn(), upload: vi.fn(), view: vi.fn() }))
vi.mock('../../api/reference-materials', () => ({ referenceMaterialsApi: api }))
const options: MaterialOptions = { available: true, maxFiles: 500, maxFileBytes: 20 * 1024 * 1024,
  maxTotalBytes: 100 * 1024 * 1024, maxArchiveBytes: 110 * 1024 * 1024, maxPathBytes: 1024, maxPathComponentBytes: 255, maxDepth: 20 }
function Harness() {
  const state = useTaskReferenceMaterials(true)
  return <><TaskReferenceMaterialsField state={state} /><button disabled={state.blocked}>创建任务</button><output>{state.uploadId}</output></>
}
beforeEach(() => {
  vi.stubGlobal('React', React); vi.resetAllMocks()
  api.options.mockResolvedValue(options)
  api.upload.mockResolvedValue({ materialId: 'upload-1', expiresAt: Date.now() + 10000, files: [], totalBytes: 3 })
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })
it('preserves nested selections, blocks creation until uploaded, and allows removing a whole directory', async () => {
  render(<Harness />)
  fireEvent.click(await screen.findByRole('button', { name: '添加参考材料' }))
  await screen.findByRole('button', { name: '添加文件' })
  const file = new File(['abc'], 'rules.md')
  // jsdom File lacks arrayBuffer; real browsers provide it.
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode('abc').buffer })
  Object.defineProperty(file, 'webkitRelativePath', { value: '业务/规则/rules.md' })
  fireEvent.change(screen.getByLabelText('选择参考目录'), { target: { files: [file] } })
  expect(screen.getByText('业务/规则/rules.md')).toBeTruthy()
  expect((screen.getByRole('button', { name: '创建任务' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '上传材料' }))
  await waitFor(() => expect(api.upload).toHaveBeenCalledOnce())
  await screen.findByText('upload-1')
  expect((screen.getByRole('button', { name: '创建任务' }) as HTMLButtonElement).disabled).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: '移除目录 业务' }))
  expect(screen.queryByText('业务/规则/rules.md')).toBeNull()
  expect(screen.queryByText('upload-1')).toBeNull()
})
it('retains selection after upload failure and enables creation when the failed material is removed', async () => {
  api.upload.mockRejectedValue(new Error('upload failed'))
  render(<Harness />); fireEvent.click(await screen.findByRole('button', { name: '添加参考材料' })); await screen.findByRole('button', { name: '添加文件' })
  const file = new File(['abc'], 'rules.md')
  Object.defineProperty(file, 'arrayBuffer', { value: async () => new TextEncoder().encode('abc').buffer })
  fireEvent.change(screen.getByLabelText('选择参考文件'), { target: { files: [file] } })
  fireEvent.click(screen.getByRole('button', { name: '上传材料' }))
  await screen.findByText('upload failed')
  expect((screen.getByRole('button', { name: '创建任务' }) as HTMLButtonElement).disabled).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: '移除 rules.md' }))
  expect((screen.getByRole('button', { name: '创建任务' }) as HTMLButtonElement).disabled).toBe(false)
})
it('rejects duplicate paths, traversal and excessive bytes while allowing matching basenames in different folders', () => {
  const file = new File(['abc'], 'rules.md')
  expect(() => validateSelection([{ file, path: '../rules.md' }], options)).toThrow()
  expect(() => validateSelection([{ file, path: '规'.repeat(100) + '.md' }], options)).toThrow()
  expect(() => validateSelection([{ file, path: 'a' }, { file, path: 'a' }], options)).toThrow()
  expect(() => validateSelection([{ file, path: 'a/rules.md' }, { file, path: 'b/rules.md' }], options)).not.toThrow()
  expect(() => validateSelection([{ file, path: 'a' }], { ...options, maxTotalBytes: 2 })).toThrow()
})
it('re-signs downloads after leaving a detail page open', async () => {
  api.view.mockResolvedValueOnce({ files: [{ path: '规则.md', size: 3 }], totalBytes: 3, download: { url: 'old' } })
    .mockResolvedValueOnce({ files: [], totalBytes: 3, download: { url: 'https://signed.example.test/new' } })
  const downloads: Array<{ href: string; filename: string }> = []
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { downloads.push({ href: this.href, filename: this.download }) })
  render(<TaskReferenceMaterialsDetail taskId="EV-1" />)
  await screen.findByText('规则.md')
  fireEvent.click(screen.getByRole('button', { name: '下载材料包' }))
  await waitFor(() => expect(downloads).toEqual([{ href: 'https://signed.example.test/new', filename: 'EV-1-reference-materials.zip' }]))
  expect(api.view).toHaveBeenCalledTimes(2)
  click.mockRestore()
})

it('does not request material capabilities or block creation until the optional control is used', async () => {
  render(<Harness />)
  expect(screen.getByRole('button', { name: '创建任务' }).hasAttribute('disabled')).toBe(false)
  expect(api.options).not.toHaveBeenCalled()
  expect(api.upload).not.toHaveBeenCalled()
  fireEvent.click(await screen.findByRole('button', { name: '添加参考材料' }))
  await screen.findByRole('button', { name: '添加文件' })
  expect(api.options).toHaveBeenCalledOnce()
  expect(screen.getByRole('button', { name: '创建任务' }).hasAttribute('disabled')).toBe(false)
})
