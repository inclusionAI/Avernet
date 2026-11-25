import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PackVersionEditor, { MAX_PACK_VERSION_FILE_BYTES, type PackVersionFile } from '../PackVersionEditor'

afterEach(() => { cleanup(); vi.restoreAllMocks() })
const initial: PackVersionFile[] = [
  { path: 'workflow.yaml', contentBase64: 'bmFtZTog5rWL6K+VCg==', mode: '100644' },
  { path: 'scripts/run.sh', contentBase64: 'ZWNobyBoaQo=', mode: '100755' },
  { path: 'images/logo.bin', contentBase64: 'AP+A', mode: '100644' },
]
function Harness({ onChange = () => {}, onUploadStateChange = () => {} }: { onChange?: (files: PackVersionFile[]) => void; onUploadStateChange?: (uploading: boolean) => void }) {
  const [value, setValue] = useState(initial)
  return <PackVersionEditor files={value} onUploadStateChange={onUploadStateChange} onChange={next => { onChange(next); setValue(next) }} />
}
function byCalls(mock: ReturnType<typeof vi.fn>) { return mock.mock.calls.map(call => call[0]) }
function select(path: string) { fireEvent.click(screen.getByRole('button', { name: path })) }

describe('PackVersionEditor', () => {
  it('highlights selected YAML, Python and JSON without rewriting bytes or other file metadata', () => {
    const encode = (text: string) => btoa(Array.from(new TextEncoder().encode(text), byte => String.fromCharCode(byte)).join(''))
    const yaml = '\ufeffname: "测试"\n\n# <img src=x>\n  '
    const python = '# screenshot\ndef capture():\n\treturn "你好 🐍"\n\n'
    const json = '{"name":"<script>","enabled":true}\n'
    const files: PackVersionFile[] = [
      { path: 'workflow.yaml', contentBase64: encode(yaml), mode: '100644' },
      { path: 'screenshots/capture.py', contentBase64: encode(python), mode: '100755' },
      { path: 'config/settings.json', contentBase64: encode(json), mode: '100644' },
    ]
    const original = structuredClone(files)
    const onChange = vi.fn()
    const { container } = render(<PackVersionEditor files={files} onChange={onChange} onUploadStateChange={() => {}} />)
    for (const [path, text, kind, token] of [
      ['workflow.yaml', yaml, 'key', 'name'],
      ['screenshots/capture.py', python, 'keyword', 'def'],
      ['config/settings.json', json, 'key', '"name"'],
    ]) {
      select(path)
      expect(screen.getByRole('textbox', { name: '文件内容' })).toHaveValue(text)
      expect(Array.from(container.querySelectorAll(`[data-syntax-kind="${kind}"]`), node => node.textContent)).toContain(token)
      expect(onChange).not.toHaveBeenCalled()
    }
    expect(files).toEqual(original)
    expect(container.querySelector('img, script')).toBeNull()
    select('screenshots/capture.py')
    const next = python.replace('你好', '再见')
    fireEvent.change(screen.getByRole('textbox', { name: '文件内容' }), { target: { value: next } })
    expect(onChange).toHaveBeenLastCalledWith([files[0], { ...files[1], contentBase64: encode(next) }, files[2]])
  })
  it('opens a full 1 MiB candidate without emitting changes or mounting every line', () => {
    const text = 'enabled: true # keep\n'.repeat(60_000).slice(0, MAX_PACK_VERSION_FILE_BYTES)
    const file: PackVersionFile = { path: 'large.yaml', contentBase64: btoa(text), mode: '100644' }
    const onChange = vi.fn()
    const { container } = render(<PackVersionEditor files={[file]} onChange={onChange} onUploadStateChange={() => {}} />)
    select(file.path)
    expect(screen.getByRole('textbox', { name: '文件内容' })).toHaveValue(text)
    expect(container.querySelector('[data-syntax-kind="key"]')).toHaveTextContent('enabled')
    expect(container.querySelectorAll('[data-syntax-kind]').length).toBeLessThan(4096)
    expect(onChange).not.toHaveBeenCalled()
    expect(file.contentBase64).toBe(btoa(text))
  })
  it('edits UTF-8 while preserving other files and modes', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    select('workflow.yaml')
    expect(screen.getByLabelText('文件内容')).toHaveValue('name: 测试\n')
    fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: '你好\n' } })
    expect(onChange).toHaveBeenLastCalledWith([{ ...initial[0], contentBase64: '5L2g5aW9Cg==' }, initial[1], initial[2]])
    expect(screen.getByText(/不会自动保存或部署/)).toBeInTheDocument()
  })
  it('filters the grouped file tree by search while keeping selection', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    fireEvent.change(screen.getByLabelText('搜索 Pack 文件'), { target: { value: 'run' } })
    expect(screen.getByRole('button', { name: 'scripts/run.sh' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'workflow.yaml' })).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('搜索 Pack 文件'), { target: { value: '' } })
    expect(screen.getByRole('button', { name: 'workflow.yaml' })).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })
  it('shows selected file details, version source, and parent save state', () => {
    render(<PackVersionEditor files={initial} onUploadStateChange={() => {}} onChange={() => {}} versionSource={{ version: 'draft', packId: 'pack-1', baseRef: 'wf/pack-1', baseCommit: 'commit-1' }} saveStatus="dirty" />)
    select('images/logo.bin')
    const info = screen.getByRole('complementary', { name: '文件信息' })
    expect(info).toHaveTextContent('images/logo.bin')
    expect(info).toHaveTextContent('100644')
    expect(info).toHaveTextContent('草稿版（wf）')
    expect(info).toHaveTextContent('wf/pack-1')
    expect(info).toHaveTextContent('有未保存更改')
  })

  it('supports binary replacement while retaining executable mode', async () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    select('scripts/run.sh')
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File([new Uint8Array([0, 255, 128, 10])], 'payload.bin')] } })
    await waitFor(() => expect(onChange).toHaveBeenCalledWith([initial[0], { ...initial[1], contentBase64: 'AP+ACg==' }, initial[2]]))
    expect(screen.getByText(/不是可安全编辑/)).toBeInTheDocument()
    expect(screen.queryByLabelText('文件内容')).not.toBeInTheDocument()
  })
  it.each(['AP+A', '/w==', 'aGVsbG8NCg==', 'not base64', '77u/aGk=AA=='])('preserves unsafe contents and offers replacement: %s', contentBase64 => {
    const onChange = vi.fn()
    render(<PackVersionEditor onUploadStateChange={() => {}} files={[{ path: 'data', contentBase64, mode: '100755' }]} onChange={onChange} />)
    select('data')
    expect(screen.getByText(/原内容保持不变/)).toBeInTheDocument()
    expect(screen.getByLabelText('上传文件替换所选文件')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })
  it('adds and deletes arbitrary paths and rejects invalid or conflicting paths', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    const path = screen.getByLabelText('新文件相对路径')
    for (const invalid of ['../secret', '/absolute', 'images/logo.bin/child', 'scripts', 'a//b', 'x\\y', 'C:/absolute']) {
      fireEvent.change(path, { target: { value: invalid } })
      fireEvent.click(screen.getByRole('button', { name: '添加空文件' }))
      expect(screen.getByRole('alert')).toBeInTheDocument()
      expect(onChange).not.toHaveBeenCalled()
    }
    fireEvent.change(path, { target: { value: 'assets/new.json' } })
    fireEvent.click(screen.getByRole('button', { name: '添加空文件' }))
    expect(onChange).toHaveBeenLastCalledWith([...initial, { path: 'assets/new.json', contentBase64: '', mode: '100644' }])
    expect(screen.getByLabelText('文件内容')).toHaveValue('')
    fireEvent.click(screen.getByRole('button', { name: '删除所选文件' }))
    expect(onChange).toHaveBeenLastCalledWith(initial)
  })
  it('rejects oversized uploads and edits without modifying existing content', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    select('workflow.yaml')
    fireEvent.change(screen.getByLabelText('文件内容'), { target: { value: 'x'.repeat(MAX_PACK_VERSION_FILE_BYTES + 1) } })
    expect(screen.getByRole('alert')).toHaveTextContent('1 MiB')
    expect(screen.getByLabelText('文件内容')).toHaveValue('name: 测试\n')
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File([new Uint8Array(MAX_PACK_VERSION_FILE_BYTES + 1)], 'big')] } })
    expect(screen.getByRole('alert')).toHaveTextContent('1 MiB')
    expect(onChange).not.toHaveBeenCalled()
  })
  it('blocks local edits and reports busy until the uploaded content is applied', () => {
    let reader!: FileReader
    vi.spyOn(FileReader.prototype, 'readAsArrayBuffer').mockImplementation(function (this: FileReader) { reader = this })
    const events: string[] = []
    const onChange = vi.fn(() => events.push('change'))
    render(<Harness onChange={onChange} onUploadStateChange={busy => events.push(String(busy))} />)
    select('workflow.yaml')
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File(['replacement'], 'a')] } })
    expect(events).toEqual(['true'])
    expect(screen.getByRole('status')).toHaveTextContent('正在读取')
    expect(screen.getByRole('button', { name: 'scripts/run.sh' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '删除所选文件' })).toBeDisabled()
    expect(screen.getByLabelText('文件内容')).toBeDisabled()
    expect(screen.getByLabelText('新文件模式')).toBeDisabled()
    const bytes = new Uint8Array(11)
    bytes.set([114, 101, 112, 108, 97, 99, 101, 109, 101, 110, 116])
    Object.defineProperty(reader, 'result', { value: bytes.buffer })
    act(() => reader.dispatchEvent(new ProgressEvent('load')))
    expect(events).toEqual(['true', 'change', 'false'])
    expect(screen.getByLabelText('文件内容')).toHaveValue('replacement')
    expect(screen.getByRole('button', { name: 'scripts/run.sh' })).toBeEnabled()
  })
  it('follows parent-controlled replacement without an unsolicited change', () => {
    const onChange = vi.fn()
    const view = render(<PackVersionEditor onUploadStateChange={() => {}} files={initial} onChange={onChange} />)
    select('workflow.yaml')
    view.rerender(<PackVersionEditor onUploadStateChange={() => {}} files={[{ ...initial[0], contentBase64: 'bmV3' }]} onChange={onChange} />)
    expect(screen.getByLabelText('文件内容')).toHaveValue('new')
    expect(onChange).not.toHaveBeenCalled()
  })
  it('keeps oversized existing files untouched and replaceable', () => {
    const onChange = vi.fn()
    const contentBase64 = 'YWFh'.repeat(Math.ceil((MAX_PACK_VERSION_FILE_BYTES + 1) / 3))
    render(<PackVersionEditor onUploadStateChange={() => {}} files={[{ path: 'large.dat', contentBase64, mode: '100644' }]} onChange={onChange} />)
    select('large.dat')
    expect(screen.getByText(/超过编辑大小限制/)).toBeInTheDocument()
    expect(screen.getByLabelText('上传文件替换所选文件')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })
  it('ignores pending upload if the parent changes the candidate', async () => {
    const onChange = vi.fn()
    const view = render(<PackVersionEditor onUploadStateChange={() => {}} files={initial} onChange={onChange} />)
    select('workflow.yaml')
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File(['old'], 'old')] } })
    view.rerender(<PackVersionEditor onUploadStateChange={() => {}} files={[{ ...initial[0], contentBase64: 'bmV3' }]} onChange={onChange} />)
    await new Promise(resolve => setTimeout(resolve, 30))
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByLabelText('文件内容')).toHaveValue('new')
  })

  it('creates and recreates an executable file and allows changing an existing mode', () => {
    const onChange = vi.fn()
    render(<Harness onChange={onChange} />)
    select('scripts/run.sh')
    fireEvent.click(screen.getByRole('button', { name: '删除所选文件' }))
    fireEvent.change(screen.getByLabelText('新文件相对路径'), { target: { value: 'scripts/run.sh' } })
    fireEvent.change(screen.getByLabelText('新文件模式'), { target: { value: '100755' } })
    fireEvent.click(screen.getByRole('button', { name: '添加空文件' }))
    expect(onChange).toHaveBeenLastCalledWith([initial[0], initial[2], { path: 'scripts/run.sh', contentBase64: '', mode: '100755' }])
    expect(screen.getByLabelText('所选文件模式')).toHaveValue('100755')
    fireEvent.change(screen.getByLabelText('所选文件模式'), { target: { value: '100644' } })
    expect(onChange).toHaveBeenLastCalledWith([initial[0], initial[2], { path: 'scripts/run.sh', contentBase64: '', mode: '100644' }])
  })
  it('reports read errors and clears busy', () => {
    let reader!: FileReader
    vi.spyOn(FileReader.prototype, 'readAsArrayBuffer').mockImplementation(function (this: FileReader) { reader = this })
    const busy = vi.fn()
    render(<Harness onUploadStateChange={busy} />)
    select('workflow.yaml')
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File(['x'], 'a')] } })
    act(() => reader.dispatchEvent(new ProgressEvent('error')))
    expect(busy.mock.calls).toEqual([[true], [false]])
    expect(screen.getByRole('alert')).toHaveTextContent('读取文件失败')
  })
  it('cancels a changed parent candidate and reports it, including busy reset', () => {
    let reader!: FileReader
    vi.spyOn(FileReader.prototype, 'readAsArrayBuffer').mockImplementation(function (this: FileReader) { reader = this })
    const busy = vi.fn()
    const onChange = vi.fn()
    const view = render(<PackVersionEditor files={initial} onChange={onChange} onUploadStateChange={busy} />)
    select('workflow.yaml')
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File(['x'], 'a')] } })
    view.rerender(<PackVersionEditor files={[{ ...initial[0], contentBase64: 'bmV3' }]} onChange={onChange} onUploadStateChange={busy} />)
    expect(busy.mock.calls).toEqual([[true], [false]])
    expect(screen.getByRole('alert')).toHaveTextContent('上传已取消')
    act(() => reader.dispatchEvent(new ProgressEvent('load')))
    expect(onChange).not.toHaveBeenCalled()
  })
  it('keeps an upload active when the parent recreates an equivalent array', () => {
    vi.spyOn(FileReader.prototype, 'readAsArrayBuffer').mockImplementation(() => {})
    const busy = vi.fn()
    const onChange = vi.fn()
    const view = render(<PackVersionEditor files={initial} onChange={onChange} onUploadStateChange={busy} />)
    select('workflow.yaml')
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File(['x'], 'a')] } })
    view.rerender(<PackVersionEditor files={initial.map(file => ({ ...file }))} onChange={onChange} onUploadStateChange={busy} />)
    expect(busy.mock.calls).toEqual([[true]])
    expect(screen.getByRole('status')).toHaveTextContent('正在读取')
    view.unmount()
    expect(busy.mock.calls).toEqual([[true], [false]])
  })

  it('reports synchronous read errors and never leaves the parent busy', () => {
    vi.spyOn(FileReader.prototype, 'readAsArrayBuffer').mockImplementation(() => { throw new Error('read failed') })
    const busy = vi.fn()
    render(<Harness onUploadStateChange={busy} />)
    select('workflow.yaml')
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File(['x'], 'a')] } })
    expect(byCalls(busy)).toEqual([true, false])
    expect(screen.getByRole('alert')).toHaveTextContent('读取文件失败')
  })
  it('handles unexpected reader aborts and oversized files without hanging busy state', () => {
    let reader!: FileReader
    vi.spyOn(FileReader.prototype, 'readAsArrayBuffer').mockImplementation(function (this: FileReader) { reader = this })
    const busy = vi.fn()
    render(<Harness onUploadStateChange={busy} />)
    select('workflow.yaml')
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File([new Uint8Array(MAX_PACK_VERSION_FILE_BYTES + 1)], 'big')] } })
    expect(busy).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('上传文件替换所选文件'), { target: { files: [new File(['x'], 'a')] } })
    act(() => reader.dispatchEvent(new ProgressEvent('abort')))
    expect(byCalls(busy)).toEqual([true, false])
    expect(screen.getByRole('alert')).toHaveTextContent('上传已取消')
  })

})
