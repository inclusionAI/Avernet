import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import type { FlowRun } from '@avernet/clawweb-shared/web/types'
import { describe, expect, it } from 'vitest'
import RunSummaryHeader from '../RunSummaryHeader'

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

function renderSummary(run: Parameters<typeof RunSummaryHeader>[0]['run']) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunSummaryHeader run={run} nodeCount={run.node_count} />
    </QueryClientProvider>,
  )

  return within(screen.getByRole('region', { name: '运行摘要' }))
}

function metadataValues(summary: ReturnType<typeof renderSummary>) {
  return metadataLabels.map(label =>
    summary.getByText(label, { selector: 'dt' }).parentElement?.querySelector('dd')?.textContent,
  )
}

describe('RunSummaryHeader runtime metadata', () => {
  it('shows each flat metadata value after the start time', () => {
    const deviceId = `device-${'a'.repeat(160)}`
    const summary = renderSummary({
      ...legacyRun, env: 'fixture-env', tag: 'fixture-tag',
      device_id: deviceId, bot_type: 'sample-bot', stage: 'sample-stage',
    })

    expect(summary.getAllByRole('term').map(term => term.textContent).slice(-7)).toEqual([
      '流量标识', '标签', '耗时', '开始时间', ...metadataLabels,
    ])
    expect(summary.getByText('fixture-env', { selector: 'dd' })).toBeInTheDocument()
    expect(summary.getByText('fixture-tag', { selector: 'dd' })).toBeInTheDocument()
    expect(metadataValues(summary)).toEqual([deviceId, 'sample-bot', 'sample-stage'])
    expect(summary.getByText('耗时', { selector: 'dt' }).parentElement).toHaveClass('lg:col-start-1')
    for (const label of metadataLabels) {
      expect(summary.getByText(label, { selector: 'dt' }).parentElement).toHaveClass('break-all')
    }
  })

  it.each([
    { kind: 'absent', metadata: {} },
    { kind: 'null', metadata: { device_id: null, bot_type: null, stage: null } },
  ])('shows empty values when metadata fields are $kind', ({ metadata }) => {
    expect(metadataValues(renderSummary({ ...legacyRun, ...metadata }))).toEqual(['', '', ''])
  })
})
