import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ScriptFilesPanel from '../ScriptFilesPanel'

const files = [
  { path: 'scripts/a.py', content: 'draft <script>', savedContent: 'old', isNew: false },
  { path: 'scripts/b.js', content: 'hello', savedContent: null, isNew: true },
  { path: 'scripts/c.sh', content: null, savedContent: 'old', isNew: false },
]
const props = () => ({ files, selectedPath: 'scripts/a.py', onSelect: vi.fn(), onChange: vi.fn(), onAdd: vi.fn(), onDelete: vi.fn(), readOnly: false as const, loading: false, error: null, onRetry: vi.fn() })
afterEach(cleanup)

describe('ScriptFilesPanel', () => {
  it('shows status and controlled drafts without rendering HTML', () => {
    const p = props(); const view = render(<ScriptFilesPanel {...p} />)
    expect(screen.getByText('已修改')).toBeInTheDocument()
    expect(screen.getByText('新增')).toBeInTheDocument()
    expect(screen.getByText('已删除')).toBeInTheDocument()
    expect(screen.getByLabelText('脚本内容')).toHaveValue('draft <script>')
    fireEvent.change(screen.getByLabelText('脚本内容'), { target: { value: 'new draft' } })
    expect(p.onChange).toHaveBeenCalledWith('scripts/a.py', 'new draft')
    fireEvent.click(screen.getByRole('button', { name: /scripts\/b.js/ }))
    expect(p.onSelect).toHaveBeenCalledWith('scripts/b.js')
    view.rerender(<ScriptFilesPanel {...p} selectedPath="scripts/b.js" />)
    expect(screen.getByLabelText('脚本内容')).toHaveValue('hello')
    view.rerender(<ScriptFilesPanel {...p} />)
    expect(screen.getByLabelText('脚本内容')).toHaveValue('draft <script>')
    expect(document.querySelector('script')).toBeNull()
  })
  it('validates paths, duplicates and creates only via callback', () => {
    const p = props(); render(<ScriptFilesPanel {...p} />)
    const path = screen.getByLabelText('新脚本路径')
    fireEvent.change(path, { target: { value: 'scripts/../bad.py' } })
    fireEvent.click(screen.getByRole('button', { name: '新建脚本' }))
    expect(screen.getByRole('alert')).toHaveTextContent('路径')
    fireEvent.change(path, { target: { value: 'scripts/a.py' } })
    fireEvent.click(screen.getByRole('button', { name: '新建脚本' }))
    expect(p.onAdd).not.toHaveBeenCalled()
    fireEvent.change(path, { target: { value: 'scripts/new.ts' } })
    fireEvent.click(screen.getByRole('button', { name: '新建脚本' }))
    expect(p.onAdd).toHaveBeenCalledWith('scripts/new.ts', '')
  })
  it('requires confirmation before deleting', () => {
    const p = props(); render(<ScriptFilesPanel {...p} />)
    fireEvent.click(screen.getByRole('button', { name: '删除脚本' }))
    expect(p.onDelete).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(p.onDelete).toHaveBeenCalledWith('scripts/a.py')
  })
  it('uploads text and rejects invalid UTF-8 and oversized files', async () => {
    const p = props(); render(<ScriptFilesPanel {...p} />)
    const upload = screen.getByLabelText('上传脚本')
    fireEvent.change(upload, { target: { files: [new File(['print(1)'], 'upload.py')] } })
    await waitFor(() => expect(p.onAdd).toHaveBeenCalledWith('scripts/upload.py', 'print(1)'))
    fireEvent.change(upload, { target: { files: [new File([new Uint8Array([255])], 'bad.py')] } })
    expect(await screen.findByRole('alert')).toHaveTextContent('UTF-8')
    fireEvent.change(upload, { target: { files: [new File([new Uint8Array(1024 * 1024 + 1)], 'large.py')] } })
    expect(screen.getByRole('alert')).toHaveTextContent('1 MB')
    expect(p.onAdd).toHaveBeenCalledTimes(1)
  })
  it('does not present an error as an empty snapshot', () => {
    render(<ScriptFilesPanel {...props()} files={[]} selectedPath={null} error="加载失败" />)
    expect(screen.getByRole('alert')).toHaveTextContent('加载失败')
    expect(screen.queryByText('没有脚本文件。')).not.toBeInTheDocument()
  })
  it('reports loading, error, retry and read-only without enabling writes', () => {
    const p = props(); const view = render(<ScriptFilesPanel {...p} loading />)
    expect(screen.getByText('正在加载脚本…')).toBeInTheDocument()
    expect(screen.getByLabelText('脚本内容')).toBeDisabled()
    view.rerender(<ScriptFilesPanel {...p} error="加载失败" />)
    expect(screen.getByRole('alert')).toHaveTextContent('加载失败')
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(p.onRetry).toHaveBeenCalled()
    view.rerender(<ScriptFilesPanel {...p} readOnly />)
    expect(screen.getByText('只读模式')).toBeInTheDocument()
    expect(screen.getByLabelText('脚本内容')).toHaveAttribute('readonly')
    expect(screen.queryByRole('button', { name: '新建脚本' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除脚本' })).not.toBeInTheDocument()
  })
  it('supports a real read-only viewer without mutation callbacks', () => {
    const onSelect = vi.fn()
    const p = { files, selectedPath: 'scripts/a.py', onSelect, readOnly: true as const, loading: false, error: null, onRetry: vi.fn() }
    const view = render(<ScriptFilesPanel {...p} />)
    expect(screen.getByRole('region', { name: '文件树' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: '文件信息' })).toHaveTextContent('scripts/a.py')
    expect(screen.getByLabelText('脚本内容')).toHaveValue('draft <script>')
    expect(screen.getByLabelText('脚本内容')).toHaveAttribute('readonly')
    expect(screen.queryByLabelText('新脚本路径')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('上传脚本')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除脚本' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /scripts\/b.js/ }))
    expect(onSelect).toHaveBeenCalledWith('scripts/b.js')
    view.rerender(<ScriptFilesPanel {...p} selectedPath="scripts/b.js" />)
    expect(screen.getByLabelText('脚本内容')).toHaveValue('hello')
    expect(screen.getByRole('region', { name: '文件信息' })).toHaveTextContent('scripts/b.js')
  })

})
