import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import ChildRunDrawer from '../ChildRunDrawer'

afterEach(() => vi.restoreAllMocks())

describe('drawer focus', () => {
  it('includes visible summary elements, excludes hidden controls and traps both directions', () => {
    // JSDOM does not calculate layout; simulate rendered elements with client rects.
    vi.spyOn(HTMLElement.prototype, 'getClientRects').mockReturnValue([{ width: 1, height: 1 }] as unknown as DOMRectList)
    render(<ChildRunDrawer onClose={() => {}}>
      <details><summary>可聚焦摘要</summary><button>折叠内按钮</button></details>
      <button style={{ display: 'none' }}>隐藏按钮</button>
      <button disabled>禁用按钮</button>
    </ChildRunDrawer>)
    const dialog = screen.getByRole('dialog')
    const close = screen.getByRole('button', { name: '关闭子运行详情' })
    const summary = screen.getByText('可聚焦摘要')
    close.focus()
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true })
    expect(summary).toHaveFocus()
    fireEvent.keyDown(dialog, { key: 'Tab' })
    expect(close).toHaveFocus()
  })
})
