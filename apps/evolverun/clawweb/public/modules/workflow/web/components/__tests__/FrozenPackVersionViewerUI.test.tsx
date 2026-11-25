import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import FrozenPackVersionViewer from '../FrozenPackVersionViewer'
import type { PackVersionFile } from '../PackVersionEditor'

const files: PackVersionFile[] = [
  { path: 'scripts/start.sh', contentBase64: btoa('#!/bin/sh\necho frozen\n'), mode: '100755' },
  { path: 'notes/readme.txt', contentBase64: btoa('<b>not markup</b>\nsecond file'), mode: '100644' },
  { path: 'assets/raw.bin', contentBase64: 'AP8NgA==', mode: '100644' },
]
function readBytes(blob: Blob): Promise<number[]> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve([...new Uint8Array(reader.result as ArrayBuffer)])
    reader.onerror = reject
    reader.readAsArrayBuffer(blob)
  })
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

describe('frozen Pack file workbench', () => {
  it('provides labelled file navigation, keyboard selection and copyable read-only content', async () => {
    const user = userEvent.setup()
    render(<FrozenPackVersionViewer files={files} />)
    const navigation = screen.getByRole('complementary', { name: '冻结 Pack 文件导航' })
    const first = within(navigation).getByRole('button', { name: 'scripts/start.sh' })
    const second = within(navigation).getByRole('button', { name: 'notes/readme.txt' })
    const content = screen.getByRole('textbox', { name: '文件内容' })
    expect(first).toHaveAttribute('aria-pressed', 'true')
    expect(content).toHaveValue('#!/bin/sh\necho frozen\n')
    expect(content).toHaveAttribute('readonly')
    expect(content).toBeEnabled()
    expect(content).toHaveAttribute('wrap', 'off')
    expect(screen.getByRole('region', { name: '冻结所选文件' })).toHaveTextContent('100755')
    await user.tab()
    expect(first).toHaveFocus()
    await user.tab()
    expect(second).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(second).toHaveAttribute('aria-pressed', 'true')
    expect(first).toHaveAttribute('aria-pressed', 'false')
    expect(content).toHaveValue('<b>not markup</b>\nsecond file')
    expect(screen.queryByText('not markup', { selector: 'b' })).not.toBeInTheDocument()
    await user.type(content, 'must not edit')
    expect(content).toHaveValue('<b>not markup</b>\nsecond file')
    const textarea = content as HTMLTextAreaElement
    textarea.select()
    expect(textarea.value.slice(textarea.selectionStart, textarea.selectionEnd)).toBe('<b>not markup</b>\nsecond file')
    expect(screen.queryByRole('button', { name: /删除|上传|保存|新建/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  })

  it('downloads selected binary bytes unchanged without rendering an editable preview', async () => {
    let downloaded: Blob | undefined
    vi.stubGlobal('URL', { createObjectURL: (blob: Blob) => { downloaded = blob; return 'blob:frozen-file' }, revokeObjectURL: vi.fn() })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe('pack-version-file.bin')
      expect(this.href).toBe('blob:frozen-file')
    })
    render(<FrozenPackVersionViewer files={files} />)
    fireEvent.click(screen.getByRole('button', { name: 'assets/raw.bin' }))
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.getByText(/二进制、无法安全显示的文本或大文件/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '下载所选文件原始字节' }))
    expect(click).toHaveBeenCalledOnce()
    expect(downloaded!.type).toBe('application/octet-stream')
    expect(await readBytes(downloaded!)).toEqual([0, 255, 13, 128])
    expect(document.querySelector('a[download]')).toBeNull()
  })

  it('keeps download failures visible without losing content and clears them when selecting another file', () => {
    vi.stubGlobal('URL', { createObjectURL: () => { throw new Error('download unavailable') }, revokeObjectURL: vi.fn() })
    render(<FrozenPackVersionViewer files={files} />)
    fireEvent.click(screen.getByRole('button', { name: '下载所选文件原始字节' }))
    expect(screen.getByRole('alert')).toHaveTextContent('候选仍保留')
    expect(screen.getByRole('textbox')).toHaveValue('#!/bin/sh\necho frozen\n')
    fireEvent.click(screen.getByRole('button', { name: 'notes/readme.txt' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('textbox')).toHaveValue('<b>not markup</b>\nsecond file')
  })

  it('keeps the empty navigation and read-only explanation without offering a download', () => {
    render(<FrozenPackVersionViewer files={[]} />)
    expect(screen.getByRole('complementary', { name: '冻结 Pack 文件导航' })).toHaveTextContent('没有文件。')
    expect(screen.getByText(/对账期间只读/)).toHaveTextContent('敏感信息')
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /下载/ })).not.toBeInTheDocument()
  })
})
