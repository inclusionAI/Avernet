/** @jest-environment jsdom */
import type { BenchRun } from '@/domain/evolution/types';
import { DomainRuns } from '@/pages/EvolutionFactory/DomainRuns';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { history } from '@umijs/max';
jest.mock('@umijs/max', () => ({ history: { push: require('@jest/globals').jest.fn() } }));
it('shows both scopes with existing run identifiers, fields and real server pagination', () => {
  const onPage = jest.fn();
  const base = {
    domainId: 'domain',
    ownerUserId: 'owner',
    status: 'succeeded',
    score: 0.9,
    maxScore: 1,
    passRate: 0.9,
    model: 'provider/model',
    startedAt: 1000,
    completedAt: 61000,
    summary: null,
    errorText: null,
    tokenUsage: { totalTokens: 1234 },
    suite: 'all',
    scene: 'bench',
  };
  const runs: BenchRun[] = [
    { ...base, benchRunId: 'run/template', runScope: 'template', templateName: 'case', templateVersion: 3 },
    {
      ...base,
      benchRunId: 'run/domain',
      runScope: 'domain',
      templateName: '__domain__',
      templateVersion: 0,
      templateCount: 18,
    },
  ];
  render(
    <DomainRuns data={{ runs, total: 35 }} loading={false} error="" reload={jest.fn()} page={1} onPage={onPage} />,
  );
  expect(screen.getByText('Domain Run（18 个模板）')).toBeInTheDocument();
  expect(screen.getByText('v3')).toBeInTheDocument();
  expect(screen.getAllByText('1,234')).toHaveLength(2);
  expect(screen.getAllByText('1m 0s')).toHaveLength(2);
  fireEvent.click(screen.getByRole('button', { name: 'run/template' }));
  expect(history.push).toHaveBeenCalledWith('/evolution-factory/bench/runs/run%2Ftemplate');
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  expect(onPage).toHaveBeenCalledWith(2);
});
it('shows genuine empty and error states, not an undeveloped placeholder', () => {
  const reload = jest.fn();
  const props = { loading: false, error: '没有权限', reload, page: 1, onPage: jest.fn() };
  const { rerender } = render(<DomainRuns {...props} />);
  expect(screen.getByRole('alert')).toHaveTextContent('没有权限');
  fireEvent.click(screen.getByRole('button', { name: '重新加载' }));
  expect(reload).toHaveBeenCalled();
  rerender(<DomainRuns {...props} error="" data={{ runs: [], total: 0 }} />);
  expect(screen.getByText('暂无关联的 Bench 运行')).toBeInTheDocument();
});
