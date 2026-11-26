import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import type { FlowRun } from '@avernet/clawweb-shared/web/types'
import { describe, expect, it } from 'vitest'
import { MemoryRouter } from 'react-router-dom'
import RunSummaryHeader from '../RunSummaryHeader'
import { formatTime } from '../../utils/time'

type Props = Parameters<typeof RunSummaryHeader>[0]

const legacyRun: FlowRun = {
  flow_id: 'flow-1',
  workflow_id: 'workflow-1',
  workflow_title: 'Example workflow',
  status: 'succeeded',
  triggered_by: null,
  node_count: 1,
  succeeded_count: 1,
  failed_count: 0,
  total_duration_ms: 300,
  total_token_usage: null,
  started_at: '2026-01-01T00:00:00Z',
  completed_at: null,
  user_id: null,
  origin_bot_id: null,
  plugin_version: null,
  engine: null,
  workflow_version: 1,
  workflow_deploy_number: 1,
}

const metadataLabels = ['device_id', 'BOT_TYPE', 'STAGE']

function renderSummary(run: Props['run'] = legacyRun, props: Partial<Props> = {}) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <RunSummaryHeader run={run} nodeCount={run.node_count} {...props} />
      </MemoryRouter>
    </QueryClientProvider>,
  )

  return within(screen.getByRole('region', { name: '运行摘要' }))
}

function valueOf(summary: ReturnType<typeof renderSummary>, label: string) {
  return summary.getByText(label, { selector: 'dt' }).parentElement!.querySelector('dd')!
}

function metadataValues(summary: ReturnType<typeof renderSummary>) {
  return metadataLabels.map(label => valueOf(summary, label).textContent)
}

describe('RunSummaryHeader grouped layout', () => {
  it('groups every stored field by identity, execution context and usage, before node progress', () => {
    const summary = renderSummary({
      ...legacyRun, env: 'fixture-env', tag: 'fixture-tag',
      user_id: 'creator', triggered_by: 'trigger', origin_bot_id: 'bot', plugin_version: '0.8.2',
      engine: 'engine', device_id: 'device', bot_type: 'sample-bot', stage: 'sample-stage',
      total_token_usage: 24680,
    })
    const identity = within(summary.getByRole('region', { name: '运行身份' }))
    const context = within(summary.getByRole('region', { name: '执行上下文' }))
    const usage = within(summary.getByRole('region', { name: '运行用量与进度' }))

    expect(identity.getAllByRole('term').map(term => term.textContent)).toEqual(['工作流', '版本', '创建者', '发起 Bot'])
    expect(valueOf(identity, '工作流')).toHaveTextContent('workflow-1')
    expect(valueOf(identity, '创建者')).toHaveTextContent('creator')
    expect(identity.queryByText('trigger')).not.toBeInTheDocument()
    expect(valueOf(identity, '发起 Bot')).toHaveTextContent('bot')
    expect(identity.getByText('plugin_version · 0.8.2')).toBeInTheDocument()
    expect(context.getAllByRole('term').map(term => term.textContent)).toEqual(['运行引擎', '流量标识', '标签', ...metadataLabels])
    expect(valueOf(context, '流量标识')).toHaveTextContent('fixture-env')
    expect(valueOf(context, '标签')).toHaveTextContent('fixture-tag')
    expect(metadataValues(context)).toEqual(['device', 'sample-bot', 'sample-stage'])
    expect(usage.getAllByRole('term').map(term => term.textContent)).toEqual(['耗时', '开始时间', 'Token 用量'])
    expect(valueOf(usage, '开始时间')).toHaveTextContent(formatTime(legacyRun.started_at))
    expect(valueOf(usage, 'Token 用量')).toHaveTextContent((24680).toLocaleString())

    const groups = Array.from(screen.getByRole('region', { name: '运行摘要' }).children)
      .map(element => element.getAttribute('aria-label')).filter(Boolean)
    expect(groups).toEqual(['运行操作', '运行身份', '执行上下文', '运行用量与进度'])
    expect(summary.getByRole('heading', { level: 1 })).toHaveTextContent('Example workflow')
    expect(summary.getByText('flow-1')).toBeInTheDocument()
    expect(summary.getByText('已成功')).toBeInTheDocument()
  })

  it('uses semantic surfaces and two-column mobile groups with wider desktop grids', () => {
    const summary = renderSummary({ ...legacyRun, engine: 'engine' })
    const root = screen.getByRole('region', { name: '运行摘要' })
    expect(root).toHaveClass('min-w-0', 'bg-card', 'border-border', 'text-foreground')
    expect(summary.getByRole('region', { name: '运行身份' }).querySelector('dl')).toHaveClass('grid-cols-2', 'sm:grid-cols-4')
    expect(summary.getByRole('region', { name: '执行上下文' }).querySelector('dl')).toHaveClass('grid-cols-2', 'md:grid-cols-3', 'xl:grid-cols-6')
    for (const element of [root, ...Array.from(root.querySelectorAll('[class]'))]) {
      expect(element.getAttribute('class')).not.toMatch(/(?:bg|text|border)-(?:white|black|blue|slate|gray|red|green|amber)(?:-|\b)/)
    }
  })

  it('wraps the full run identity, Bot, engine, metadata and markers instead of truncating them', () => {
    const longId = `identifier-${'a'.repeat(200)}`
    const summary = renderSummary({
      ...legacyRun, flow_id: longId, workflow_title: longId, workflow_id: longId,
      user_id: longId, origin_bot_id: longId, plugin_version: longId, engine: longId,
      device_id: longId, bot_type: longId, stage: longId, env: longId, tag: longId,
    })
    expect(summary.getByRole('heading', { level: 1 })).toHaveTextContent(longId)
    expect(summary.getByRole('heading', { level: 1 })).toHaveClass('break-words', '[overflow-wrap:anywhere]')
    const flowId = summary.getByText(longId, { selector: 'p' })
    expect(flowId.textContent).toBe(longId)
    expect(flowId).toHaveClass('break-words', '[overflow-wrap:anywhere]')
    for (const term of summary.getAllByRole('term')) {
      expect(term.parentElement).toHaveClass('min-w-0')
      expect(term.parentElement?.querySelector('dd')).toHaveClass('break-words', '[overflow-wrap:anywhere]')
      expect(term.parentElement?.querySelector('dd')).not.toHaveClass('truncate')
    }
  })

  it('keeps the workflow ID as the title fallback and triggered_by as the creator fallback', () => {
    const summary = renderSummary({ ...legacyRun, workflow_title: '', triggered_by: 'trigger-user' })
    expect(summary.getByRole('heading', { level: 1 })).toHaveTextContent('workflow-1')
    expect(valueOf(summary, '创建者')).toHaveTextContent('trigger-user')
  })

  it('retains a plugin version even when the origin Bot is unavailable', () => {
    const summary = renderSummary({ ...legacyRun, plugin_version: '1.2.3' })
    expect(valueOf(summary, '发起 Bot')).toHaveTextContent('—plugin_version · 1.2.3')
    expect(summary.queryByTitle('重跑：重新发送原始命令到 Bot')).not.toBeInTheDocument()
  })

  it.each([
    { version: 0, display: '0' },
    { version: 39, display: '39' },
    { version: -1, display: '未绑定发布版本' },
    { version: null, display: '—' },
  ])('retains workflow version $version without implying publication', ({ version, display }) => {
    expect(valueOf(renderSummary({ ...legacyRun, workflow_version: version }), '版本').textContent).toBe(display)
  })
})

describe('RunSummaryHeader missing and empty metadata', () => {
  it.each([
    { kind: 'absent', metadata: {} },
    { kind: 'null', metadata: { device_id: null, bot_type: null, stage: null } },
    { kind: 'empty', metadata: { device_id: '', bot_type: '', stage: '' } },
  ])('shows empty values when runtime metadata fields are $kind', ({ metadata }) => {
    expect(metadataValues(renderSummary({ ...legacyRun, ...metadata }))).toEqual(['', '', ''])
  })

  it.each([
    { kind: 'absent', markers: {}, env: '未记录', tag: '未记录' },
    { kind: 'null', markers: { env: null, tag: null }, env: '未记录', tag: '未记录' },
    { kind: 'empty', markers: { env: '', tag: '' }, env: '', tag: '—' },
  ])('preserves the $kind marker semantics independently', ({ markers, env, tag }) => {
    const summary = renderSummary({ ...legacyRun, ...markers })
    expect(valueOf(summary, '流量标识').textContent).toBe(env)
    expect(valueOf(summary, '标签').textContent).toBe(tag)
  })

  it.each([
    { duration: 0, display: '0ms' },
    { duration: null, display: '—' },
  ])('does not treat duration $duration as a different value', ({ duration, display }) => {
    expect(valueOf(renderSummary({ ...legacyRun, total_duration_ms: duration }), '耗时').textContent).toBe(display)
  })

  it('does not invent a start time for an absent stored value', () => {
    expect(valueOf(renderSummary({ ...legacyRun, started_at: '' }), '开始时间').textContent).toBe('—')
  })

  it.each([null, 0, -1])('hides nonpositive or unavailable token usage %s', total_token_usage => {
    const summary = renderSummary({ ...legacyRun, total_token_usage })
    expect(summary.queryByText('Token 用量')).not.toBeInTheDocument()
  })
})

describe('RunSummaryHeader node progress', () => {
  it('uses supplied counts and total without turning skipped/unexecuted nodes into successes', () => {
    const summary = renderSummary({ ...legacyRun, status: 'skipped', succeeded_count: 5, failed_count: 2 }, {
      nodeCount: 8, succeededCount: 3, failedCount: 1,
    })
    const progress = summary.getByRole('progressbar', { name: '节点进度' })
    expect(summary.getByText('3 成功 / 1 失败 / 8 总计')).toBeInTheDocument()
    expect(progress).toHaveAttribute('aria-valuenow', '4')
    expect(progress).toHaveAttribute('aria-valuemax', '8')
    expect(progress).toHaveAttribute('aria-valuetext', '3 成功 / 1 失败 / 8 总计')
    expect(progress.children[0]).toHaveClass('bg-success')
    expect(progress.children[0]).toHaveStyle({ width: '38%' })
    expect(progress.children[1]).toHaveClass('bg-destructive')
    expect(progress.children[1]).toHaveStyle({ width: '13%' })
  })

  it('uses stored counts when overrides are absent', () => {
    const summary = renderSummary({ ...legacyRun, node_count: 4, succeeded_count: 2, failed_count: 1 })
    expect(summary.getByText('2 成功 / 1 失败 / 4 总计')).toBeInTheDocument()
    expect(summary.getByRole('progressbar').children[0]).toHaveStyle({ width: '50%' })
    expect(summary.getByRole('progressbar').children[1]).toHaveStyle({ width: '25%' })
  })

  it('respects explicit zero overrides and keeps the zero-node bar empty', () => {
    const summary = renderSummary(legacyRun, { nodeCount: 0, succeededCount: 0, failedCount: 0 })
    expect(summary.getByText('0 成功 / 0 失败 / 0 总计')).toBeInTheDocument()
    expect(summary.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')
    expect(summary.getByRole('progressbar').children).toHaveLength(0)
  })
})
