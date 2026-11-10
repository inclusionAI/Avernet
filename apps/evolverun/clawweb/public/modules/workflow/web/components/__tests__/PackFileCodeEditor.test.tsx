import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useState } from 'react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PackFileCodeEditor from '../PackFileCodeEditor'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

function Harness({ path = 'workflow.yaml', initial = '', disabled = false, onChange = () => {} }: {
  path?: string; initial?: string; disabled?: boolean; onChange?: (text: string) => void
}) {
  const [value, setValue] = useState(initial)
  return <PackFileCodeEditor path={path} value={value} disabled={disabled} onChange={next => { setValue(next); onChange(next) }} />
}

function input() { return screen.getByRole('textbox', { name: '文件内容' }) as HTMLTextAreaElement }
function highlight(container: HTMLElement) { return container.querySelector('[data-testid="pack-file-highlight"]') as HTMLElement }

describe('PackFileCodeEditor', () => {
  it('forwards a custom accessible label and keydown event from the native textarea', () => {
    let target: HTMLTextAreaElement | null = null
    const onKeyDown = vi.fn(event => { target = event.currentTarget; event.preventDefault() })
    render(<PackFileCodeEditor path="workflow.yaml" value="title: test" onChange={() => {}} ariaLabel="工作流 YAML" onKeyDown={onKeyDown} />)
    const textarea = screen.getByRole('textbox', { name: '工作流 YAML' })
    expect(screen.queryByRole('textbox', { name: '文件内容' })).not.toBeInTheDocument()
    expect(fireEvent.keyDown(textarea, { key: 'Tab' })).toBe(false)
    expect(onKeyDown).toHaveBeenCalledOnce()
    expect(target).toBe(textarea)
  })

  it('preserves the default 430px file layout but lets a fill editor shrink and clip its overlay', () => {
    const props = { path: 'workflow.yaml', value: 'key: value\n'.repeat(200), onChange: vi.fn() }
    const { container, rerender } = render(<PackFileCodeEditor {...props} />)
    const textarea = input()
    expect(textarea.parentElement).toHaveClass('min-h-[430px]', 'h-full')
    rerender(<PackFileCodeEditor {...props} layout="fill" />)
    expect(input()).toBe(textarea)
    expect(textarea.parentElement).toHaveClass('min-h-0', 'flex-1', 'overflow-hidden')
    expect(textarea.parentElement).not.toHaveClass('min-h-[430px]', 'h-full')
    Object.defineProperty(textarea, 'clientHeight', { configurable: true, value: 120 })
    fireEvent(window, new Event('resize'))
    expect(highlight(container).style.height).toBe('120px')
    expect(props.onChange).not.toHaveBeenCalled()
  })

  it.each([
    ['workflow.yaml', 'name: "测试"\n', 'key', 'name'],
    ['nested/WORKFLOW.YML', 'enabled: true\n', 'boolean', 'true'],
    ['screenshots/capture.py', 'def capture():\n    return 42\n', 'keyword', 'def'],
    ['config/settings.json', '{"enabled":true}\n', 'key', '"enabled"'],
  ])('selects syntax from %s while preserving the accessible input', (path, value, kind, token) => {
    const onChange = vi.fn()
    const { container } = render(<Harness path={path} initial={value} onChange={onChange} />)
    expect(input()).toHaveValue(value)
    expect(input()).toHaveAttribute('wrap', 'off')
    expect(input()).toHaveAttribute('spellcheck', 'false')
    expect(highlight(container)).toHaveAttribute('aria-hidden', 'true')
    expect(Array.from(highlight(container).querySelectorAll(`[data-syntax-kind="${kind}"]`), node => node.textContent)).toContain(token)
    expect(highlight(container).textContent).toBe(value)
    expect(onChange).not.toHaveBeenCalled()
  })

  it('keeps the same focused input and selection through synchronous edits and composition', () => {
    const onChange = vi.fn()
    const { container } = render(<Harness initial={'name: test\n\n'} onChange={onChange} />)
    const textarea = input()
    textarea.focus()
    textarea.setSelectionRange(6, 10)
    fireEvent.compositionStart(textarea)
    const next = 'name: 测试\n\n'
    fireEvent.change(textarea, { target: { value: next, selectionStart: 8, selectionEnd: 8 } })
    fireEvent.compositionEnd(textarea)
    expect(input()).toBe(textarea)
    expect(textarea).toHaveFocus()
    expect(textarea.selectionStart).toBe(8)
    expect(textarea.selectionEnd).toBe(8)
    expect(onChange).toHaveBeenLastCalledWith(next)
    expect(highlight(container).textContent).toBe(next)
  })

  it('leaves unknown extensions as editable plain text without interpreting markup', () => {
    const value = '<img src=x>\n\tplain & text\n'
    const onChange = vi.fn()
    const { container } = render(<Harness path="assets/example.unknown" initial={value} onChange={onChange} />)
    expect(input()).toHaveValue(value)
    expect(container.querySelector('img')).toBeNull()
    expect(container.querySelector('[data-syntax-kind="keyword"], [data-syntax-kind="key"]')).toBeNull()
    fireEvent.change(input(), { target: { value: `${value}next` } })
    expect(onChange).toHaveBeenLastCalledWith(`${value}next`)
  })

  it('disables the native input without changing the value or notifying the parent', async () => {
    const onChange = vi.fn()
    render(<Harness initial="name: test" disabled onChange={onChange} />)
    expect(input()).toBeDisabled()
    await userEvent.setup().type(input(), 'ignored')
    expect(input()).toHaveValue('name: test')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('synchronizes both scroll axes, resets on file changes and updates external content', () => {
    const onChange = vi.fn()
    const value = Array.from({ length: 1000 }, (_, i) => `key_${i}: "${'long'.repeat(40)}"\n`).join('')
    const { container, rerender } = render(<PackFileCodeEditor path="long.yml" value={value} onChange={onChange} />)
    const textarea = input()
    Object.defineProperties(textarea, { clientHeight: { configurable: true, value: 430 }, clientWidth: { configurable: true, value: 600 } })
    fireEvent.scroll(textarea, { target: { scrollTop: 2200, scrollLeft: 200 } })
    expect(highlight(container).textContent).toContain('key_100:')
    expect(highlight(container).textContent).not.toContain('key_0:')
    const pre = highlight(container).querySelector('pre')!
    expect(pre.style.transform).toContain('-200px')
    expect(highlight(container).style.width).toBe('600px')
    expect(highlight(container).style.height).toBe('430px')
    rerender(<PackFileCodeEditor path="screenshots/capture.py" value={'return True\n'} onChange={onChange} />)
    expect(input()).toBe(textarea)
    expect(textarea.scrollTop).toBe(0)
    expect(textarea.scrollLeft).toBe(0)
    expect(highlight(container).textContent).toBe('return True\n')
    expect(highlight(container).querySelector('[data-syntax-kind="keyword"]')).toHaveTextContent('return')
    rerender(<PackFileCodeEditor path="screenshots/capture.py" value={'return False\n\n'} onChange={onChange} />)
    expect(input()).toHaveValue('return False\n\n')
    expect(highlight(container).textContent).toBe('return False\n\n')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('bounds token DOM for a 1 MiB file while highlighting newly scrolled rows', () => {
    const value = 'enabled: true # test\n'.repeat(60_000).slice(0, 1024 * 1024)
    const { container } = render(<Harness initial={value} />)
    expect(input().value.length).toBe(1024 * 1024)
    expect(container.querySelectorAll('[data-syntax-kind]').length).toBeLessThan(4096)
    fireEvent.scroll(input(), { target: { scrollTop: 44_000 } })
    expect(highlight(container).querySelector('[data-syntax-kind="boolean"]')).toHaveTextContent('true')
    expect(container.querySelectorAll('[data-syntax-kind]').length).toBeLessThan(4096)
    fireEvent.change(input(), { target: { value: `${value.slice(0, -1)}x` } })
    expect(input().value).toBe(`${value.slice(0, -1)}x`)
  })

  it('bounds pathological one-line token DOM without truncating or wrapping the file', () => {
    const value = `[${'"x",'.repeat(262_143)}0]`
    const { container } = render(<Harness path="large.json" initial={value} />)
    expect(input().value).toBe(value)
    expect(highlight(container).textContent).toBe(value)
    expect(container.querySelectorAll('[data-syntax-kind]').length).toBeLessThan(4096)
    expect(input()).toHaveAttribute('wrap', 'off')
  })


  it('shares every text metric between layers and uses only colors on syntax spans', () => {
    const { container } = render(<Harness initial={'name: "你好"\n# comment\n\tvalue: true\n'} />)
    const textarea = input()
    const pre = highlight(container).querySelector('pre')!
    for (const property of ['font-family', 'font-size', 'font-weight', 'font-style', 'line-height', 'letter-spacing', 'font-variant-ligatures', 'tab-size', 'white-space', 'overflow-wrap', 'word-break', 'text-indent', 'text-transform', 'padding', 'margin', 'border', 'box-sizing']) {
      expect(pre.style.getPropertyValue(property)).toBe(textarea.style.getPropertyValue(property))
    }
    for (const token of highlight(container).querySelectorAll('[data-syntax-kind]')) {
      expect(token.className).toMatch(/^text-/)
      expect(token.className).not.toMatch(/font-|italic|block/)
    }
  })

  it('remeasures the clipping viewport after a resize without changing input focus or scroll', () => {
    const { container } = render(<Harness initial={'key: value\n'.repeat(200)} />)
    const textarea = input()
    textarea.focus()
    Object.defineProperties(textarea, { clientWidth: { configurable: true, value: 321 }, clientHeight: { configurable: true, value: 480 } })
    fireEvent.scroll(textarea, { target: { scrollTop: 700, scrollLeft: 20 } })
    fireEvent(window, new Event('resize'))
    expect(highlight(container).style.width).toBe('321px')
    expect(highlight(container).style.height).toBe('480px')
    expect(textarea.scrollTop).toBe(700)
    expect(textarea.scrollLeft).toBe(20)
    expect(textarea).toHaveFocus()
  })

  it('retains Python multiline string context when its opening line is above the viewport', () => {
    const value = `description = """\n${'# string, not comment\n'.repeat(200)}"""\nreturn True\n`
    const { container } = render(<Harness path="screenshots/capture.py" initial={value} />)
    fireEvent.scroll(input(), { target: { scrollTop: 2200 } })
    expect(highlight(container).querySelector('[data-syntax-kind="comment"]')).toBeNull()
    expect(highlight(container).querySelector('[data-syntax-kind="string"]')).toHaveTextContent('# string, not comment')
  })
})
