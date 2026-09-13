import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import SingleboxTaskEntry from '../SingleboxTaskEntry'

const render = (taskType: string | null, version: 'openversion' | 'internalversion' = 'openversion') =>
  renderToStaticMarkup(<SingleboxTaskEntry version={version} taskType={taskType}><button>submit-original-task</button></SingleboxTaskEntry>)

describe('Singlebox task entry preserves requested intent', () => {
  it.each(['repair', 'session_analysis'])('does not silently create diagnose for %s', (type) => {
    const html = render(type)
    expect(html).toContain('当前不可提交')
    expect(html).not.toContain('submit-original-task')
  })
  it('opens Pack restore without redirecting to diagnosis', () => { expect(render('pack_restore')).toContain('submit-original-task') })
  it.each(['diagnose', 'bench', 'runtime_cleanup', 'pack', 'optimize', 'bench_optimize', 'full', null])('keeps available entry %s', (type) => {
    expect(render(type)).toContain('submit-original-task')
  })
  it.each(['pack', 'pack_restore', 'optimize', 'full'])('does not restrict internalversion %s', (type) => {
    expect(render(type, 'internalversion')).toContain('submit-original-task')
  })
})

it('blocks governance without blocking ordinary full tasks', () => {
  const html = renderToStaticMarkup(<SingleboxTaskEntry version="openversion" taskType="full" governance><button>submit-original-task</button></SingleboxTaskEntry>)
  expect(html).not.toContain('submit-original-task')
  expect(render('full')).toContain('submit-original-task')
})
