import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import IssueSummary from '../IssueSummary';

describe('IssueSummary', () => {
  it('shows distinct causes with their own source runs and does not invent a shared suggestion', () => {
    render(<IssueSummary group={{ aggregationStatus: 'completed', stale: false,
      summary: { summary: 'Different timeout causes', unknowns: ['Need traces'], causes: [
        { title: 'Network', conclusion: 'Slow endpoint', certainty: 'hypothesis', sourceIds: ['a'] },
        { title: 'Approval', conclusion: 'Waiting for a person', certainty: 'supported', sourceIds: ['b'] },
      ] }, sources: [{ sourceId: 'a', flowId: 'run-a' }, { sourceId: 'b', flowId: 'run-b' }] } as never} />);
    expect(screen.getByText('Network')).toBeTruthy();
    expect(screen.getByText('Approval')).toBeTruthy();
    expect(screen.getByText('run-a')).toBeTruthy();
    expect(screen.getByText('run-b')).toBeTruthy();
    expect(screen.queryByRole('button', { name: /应用/ })).toBeNull();
  });
  it('shows missing and failed aggregation explicitly instead of labeling the latest diagnosis a summary', () => {
    render(<IssueSummary group={{ aggregationStatus: 'failed', summary: null, stale: false, sources: [] } as never} />);
    expect(screen.getByRole('status')).toHaveTextContent('聚合失败');
  });
  it('distinguishes a retained summary scope from the current issue and collapses long prose', () => {
    render(<IssueSummary group={{ aggregationStatus: 'too_large', stale: true,
      flowIds: ['run-a', 'run-b', 'run-c'], sources: [{ sourceId: 'new', flowId: 'run-c' }],
      summarySources: [{ sourceId: 'old', flowId: 'run-a' }],
      summary: { summary: '历史摘要'.repeat(60), causes: [], unknowns: ['仍需核对超时机制'] },
    } as never} />);
    expect(screen.getByText(/摘要依据 1 个运行 · 当前关联 3 个运行/)).toBeTruthy();
    expect(screen.getByRole('status')).toHaveTextContent('摘要待更新');
    expect(screen.getByText('展开完整摘要').closest('details')).not.toHaveAttribute('open');
    expect(screen.getByText('待确认（1 项）').closest('details')).not.toHaveAttribute('open');
  });
  it('keeps stale and failed status together without promising an automatic retry', () => {
    render(<IssueSummary group={{ aggregationStatus: 'failed', stale: true, flowIds: ['new'],
      summarySources: [{ sourceId: 'old', flowId: 'old' }], sources: [],
      summary: { summary: 'Retained conclusion', causes: [], unknowns: [] },
    } as never} />);
    expect(screen.getAllByRole('status')).toHaveLength(1);
    expect(screen.getByRole('status')).toHaveTextContent('聚合失败或等待超时');
    expect(screen.getByRole('status')).toHaveTextContent('摘要待更新');
    expect(screen.queryByText(/下次分析时会重试/)).toBeNull();
    expect(screen.getByText('Retained conclusion')).toBeTruthy();
  });
});
