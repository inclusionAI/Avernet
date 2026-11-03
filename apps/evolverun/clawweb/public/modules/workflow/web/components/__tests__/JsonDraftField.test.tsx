import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import JsonDraftField from '../editor/JsonDraftField'
import { useEditorStore } from '../../editor/store'

beforeEach(() => {
  for (const key of Object.keys(useEditorStore.getState().pendingEdits)) useEditorStore.getState().setPendingEdit(key, null)
  useEditorStore.getState().markClean()
  useEditorStore.getState().reset()
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })
const key = 'node:a:args'

describe('JsonDraftField with the actual editor store', () => {
  it('keeps valid text pending until explicit Apply and clears its own key first', () => {
    const apply = vi.fn(() => expect(useEditorStore.getState().pendingEdits[key]).toBeUndefined())
    render(<JsonDraftField label="参数" value={{ old: 1 }} draftKey={key} onApply={apply} />)
    fireEvent.change(screen.getByLabelText('参数'), { target: { value: '{"next":false}' } })
    expect(apply).not.toHaveBeenCalled()
    expect(useEditorStore.getState().pendingEdits[key]).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '应用 参数' }))
    expect(apply).toHaveBeenCalledWith({ next: false })
  })

  it('retains invalid text, exposes a real error and can discard', () => {
    const apply = vi.fn()
    render(<JsonDraftField label="参数" value={0} draftKey={key} onApply={apply} />)
    fireEvent.change(screen.getByLabelText('参数'), { target: { value: '{broken' } })
    expect(screen.getByRole('alert')).toHaveTextContent(/JSON/)
    expect(screen.getByRole('button', { name: '应用 参数' })).toBeDisabled()
    expect(screen.getByLabelText('参数')).toHaveValue('{broken')
    fireEvent.click(screen.getByRole('button', { name: '放弃 参数' }))
    expect(screen.getByLabelText('参数')).toHaveValue('0')
    expect(useEditorStore.getState().pendingEdits).toEqual({})
    expect(apply).not.toHaveBeenCalled()
  })

  it.each(['{"__proto__":{"polluted":true}}', '{"safe":[{"constructor":{"prototype":{}}}]}'])('rejects unsafe JSON %s', text => {
    render(<JsonDraftField label="参数" value={{}} draftKey={key} onApply={vi.fn()} />)
    fireEvent.change(screen.getByLabelText('参数'), { target: { value: text } })
    expect(screen.getByRole('alert')).toHaveTextContent(/不安全/)
    expect(screen.getByRole('button', { name: '应用 参数' })).toBeDisabled()
    expect(({} as Record<string, unknown>).polluted).toBeUndefined()
  })

  it('does not overwrite a draft on external updates or silently clear pending on unmount', () => {
    const apply = vi.fn()
    const view = render(<JsonDraftField label="参数" value={{ old: 1 }} draftKey={key} onApply={apply} />)
    fireEvent.change(screen.getByLabelText('参数'), { target: { value: '{"mine":2}' } })
    view.rerender(<JsonDraftField label="参数" value={{ other: 3 }} draftKey={key} onApply={apply} />)
    expect(screen.getByLabelText('参数')).toHaveValue('{"mine":2}')
    expect(screen.getByRole('button', { name: '应用 参数' })).toBeDisabled()
    view.unmount()
    expect(useEditorStore.getState().pendingEdits[key]).toBeTruthy()
  })

  it('keeps other pending keys and preserves text when an apply fails', () => {
    act(() => useEditorStore.getState().setPendingEdit('yaml', 'YAML 未应用'))
    render(<JsonDraftField label="参数" value={null} draftKey={key} onApply={() => { throw new Error('写入被拒绝') }} />)
    fireEvent.change(screen.getByLabelText('参数'), { target: { value: 'false' } })
    fireEvent.click(screen.getByRole('button', { name: '应用 参数' }))
    expect(screen.getByRole('alert')).toHaveTextContent('其他未应用草稿')
    expect(screen.getByLabelText('参数')).toHaveValue('false')
    expect(useEditorStore.getState().pendingEdits[key]).toBeTruthy()
    expect(useEditorStore.getState().pendingEdits.yaml).toBeTruthy()
    act(() => useEditorStore.getState().setPendingEdit('yaml', null))
    fireEvent.click(screen.getByRole('button', { name: '应用 参数' }))
    expect(screen.getByRole('alert')).toHaveTextContent('写入被拒绝')
    expect(useEditorStore.getState().pendingEdits[key]).toBeTruthy()
  })
})


describe('JSON validation, deletion and value fidelity', () => {
  it.each([null, false, 0, '', [], {}])('preserves pristine JSON value %j', value => {
    const apply = vi.fn()
    render(<JsonDraftField label="配置" value={value} draftKey={key} onApply={apply} />)
    expect(screen.getByLabelText('配置')).toHaveValue(JSON.stringify(value, null, 2))
    expect(useEditorStore.getState().pendingEdits).toEqual({})
    expect(apply).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '应用 配置' })).toBeDisabled()
  })

  it('refreshes pristine values, validates shape and then recovers from validation errors', () => {
    const apply = vi.fn()
    const validate = (value: unknown) => Array.isArray(value) ? null : '必须是数组'
    const view = render(<JsonDraftField label="配置" value={undefined} draftKey={key} onApply={apply} validate={validate} />)
    expect(screen.getByLabelText('配置')).toHaveValue('')
    view.rerender(<JsonDraftField label="配置" value={[]} draftKey={key} onApply={apply} validate={validate} />)
    expect(screen.getByLabelText('配置')).toHaveValue('[]')
    fireEvent.change(screen.getByLabelText('配置'), { target: { value: '{}' } })
    expect(screen.getByRole('alert')).toHaveTextContent('必须是数组')
    expect(screen.getByRole('button', { name: '应用 配置' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('配置'), { target: { value: '[true, "true", 0, null]' } })
    fireEvent.click(screen.getByRole('button', { name: '应用 配置' }))
    expect(apply).toHaveBeenCalledWith([true, 'true', 0, null])
  })

  it('does not interpret HTML content and rejects overflow and nested pollution keys', () => {
    const apply = vi.fn()
    render(<JsonDraftField label="配置" value={null} draftKey={key} onApply={apply} />)
    for (const text of ['1e999', '{"nested":{"prototype":{}}}']) {
      fireEvent.change(screen.getByLabelText('配置'), { target: { value: text } })
      expect(screen.getByRole('button', { name: '应用 配置' })).toBeDisabled()
    }
    const text = '<img src=x onerror=alert(1)>'
    fireEvent.change(screen.getByLabelText('配置'), { target: { value: JSON.stringify(text) } })
    expect(document.querySelector('img')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '应用 配置' }))
    expect(apply).toHaveBeenCalledWith(text)
  })

  it('requires explicit confirmation before removal, keeps drafts and reports a rejected removal', () => {
    const remove = vi.fn(() => { throw new Error('仍被引用') })
    render(<JsonDraftField label="配置" value={{ keep: true }} draftKey={key} onApply={vi.fn()} allowRemove onRemove={remove} />)
    vi.mocked(window.confirm).mockReturnValueOnce(false)
    fireEvent.click(screen.getByRole('button', { name: '删除 配置' }))
    expect(remove).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '删除 配置' }))
    expect(screen.getByRole('alert')).toHaveTextContent('仍被引用')
    fireEvent.change(screen.getByLabelText('配置'), { target: { value: '{"draft":false}' } })
    expect(screen.getByRole('button', { name: '删除 配置' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '放弃 配置' }))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('restores the latest external value when explicitly discarding a conflicting draft', () => {
    const apply = vi.fn()
    const view = render(<JsonDraftField label="配置" value={1} draftKey={key} onApply={apply} />)
    fireEvent.change(screen.getByLabelText('配置'), { target: { value: '2' } })
    view.rerender(<JsonDraftField label="配置" value={3} draftKey={key} onApply={apply} />)
    fireEvent.click(screen.getByRole('button', { name: '放弃 配置' }))
    expect(screen.getByLabelText('配置')).toHaveValue('3')
    expect(useEditorStore.getState().pendingEdits).toEqual({})
  })

  it('retains an omitted nested object until confirmed and clears pending if text is restored', () => {
    const apply = vi.fn()
    const original = { nested: { value: 0 }, keep: false }
    render(<JsonDraftField label="配置" value={original} draftKey={key} onApply={apply} />)
    fireEvent.change(screen.getByLabelText('配置'), { target: { value: '{"nested":null,"keep":false}' } })
    vi.mocked(window.confirm).mockReturnValueOnce(false)
    fireEvent.click(screen.getByRole('button', { name: '应用 配置' }))
    expect(apply).not.toHaveBeenCalled()
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining('$.nested'))
    fireEvent.change(screen.getByLabelText('配置'), { target: { value: JSON.stringify(original, null, 2) } })
    expect(useEditorStore.getState().pendingEdits).toEqual({})
  })
})

describe('shared widget boundary with concurrent drafts', () => {
  it('never calls a non-throwing store consumer or clears either draft while another field is pending', () => {
    useEditorStore.getState().loadSpec({ id: 'workflow', version: '1', title: '流程', nodes: [], config: { keep: 1 }, params: {} })
    const applyConfig = vi.fn((value: unknown) => {
      // Mirrors consumers that use the void store action without checking actionError.
      useEditorStore.getState().updateSpecField('config', value)
    })
    function SettingsFields() {
      const spec = useEditorStore(state => state.spec)!
      return <>
        <JsonDraftField label="流程配置" value={spec.config} draftKey="settings:config" onApply={applyConfig} />
        <JsonDraftField label="流程参数" value={spec.params} draftKey="settings:params"
          onApply={value => useEditorStore.getState().updateSpecField('params', value)} />
      </>
    }
    render(<SettingsFields />)
    fireEvent.change(screen.getByLabelText('流程配置'), { target: { value: '{"keep":1,"mine":false}' } })
    fireEvent.change(screen.getByLabelText('流程参数'), { target: { value: '{"other":0}' } })
    const beforePending = { ...useEditorStore.getState().pendingEdits }
    fireEvent.click(screen.getByRole('button', { name: '应用 流程配置' }))
    expect(applyConfig).not.toHaveBeenCalled()
    expect(useEditorStore.getState().spec!.config).toEqual({ keep: 1 })
    expect(useEditorStore.getState().pendingEdits).toEqual(beforePending)
    expect(screen.getByLabelText('流程配置')).toHaveValue('{"keep":1,"mine":false}')
    expect(screen.getByLabelText('流程参数')).toHaveValue('{"other":0}')
    expect(screen.getByRole('alert')).toHaveTextContent('其他未应用草稿')
    expect(window.confirm).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: '放弃 流程参数' }))
    fireEvent.click(screen.getByRole('button', { name: '应用 流程配置' }))
    expect(applyConfig).toHaveBeenCalledOnce()
    expect(useEditorStore.getState().spec!.config).toEqual({ keep: 1, mine: false })
    expect(useEditorStore.getState().pendingEdits).toEqual({})
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})


it('checks newly registered pending keys before invoking a non-throwing remove callback', () => {
  const remove = vi.fn()
  render(<JsonDraftField label="配置" value={{ keep: 0 }} draftKey={key} onApply={vi.fn()} allowRemove onRemove={remove} />)
  act(() => useEditorStore.getState().setPendingEdit('yaml', 'YAML 草稿未应用'))
  fireEvent.click(screen.getByRole('button', { name: '删除 配置' }))
  expect(remove).not.toHaveBeenCalled()
  expect(window.confirm).not.toHaveBeenCalled()
  expect(screen.getByRole('alert')).toHaveTextContent('其他未应用草稿')
  expect(useEditorStore.getState().pendingEdits.yaml).toBe('YAML 草稿未应用')
})
it('retains a rejected JSON apply across remount and can retry or restore the pristine value', () => {
  const onApply = vi.fn().mockImplementationOnce(() => { throw new Error('写入被拒绝') })
  const props = { label: '参数', value: { keep: 1 }, draftKey: key, onApply }
  const view = render(<JsonDraftField {...props} />)
  const text = '{ "keep": 1, "next": false }\n'
  fireEvent.change(screen.getByLabelText('参数'), { target: { value: text } })
  fireEvent.click(screen.getByRole('button', { name: '应用 参数' }))
  expect(screen.getByRole('alert')).toHaveTextContent('写入被拒绝')
  view.unmount()
  expect(useEditorStore.getState().pendingEdits).toHaveProperty(key)
  const recovered = render(<JsonDraftField {...props} />)
  expect(screen.getByLabelText('参数')).toHaveValue(text)
  fireEvent.click(screen.getByRole('button', { name: '应用 参数' }))
  expect(onApply).toHaveBeenCalledTimes(2)
  expect(useEditorStore.getState().pendingEdits).toEqual({})
  fireEvent.change(screen.getByLabelText('参数'), { target: { value: '{unfinished' } })
  fireEvent.change(screen.getByLabelText('参数'), { target: { value: JSON.stringify(props.value, null, 2) } })
  recovered.unmount()
  render(<JsonDraftField {...props} />)
  expect(screen.getByLabelText('参数')).toHaveValue(JSON.stringify(props.value, null, 2))
  expect(screen.getByRole('button', { name: '放弃 参数' })).toBeDisabled()
  expect(useEditorStore.getState().pendingEdits).toEqual({})
})
