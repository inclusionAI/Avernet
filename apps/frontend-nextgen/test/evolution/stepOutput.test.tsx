/** @jest-environment jsdom */
import { outputHighlights, StepOutput } from '@/pages/EvolutionFactory/StepOutput';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import { history } from '@umijs/max';

jest.mock('@umijs/max', () => ({ history: { push: require('@jest/globals').jest.fn() }, useParams: () => ({}) }));
it('presents baseline provenance and real scores with encoded Bench links', () => {
  const result = {
    producerStepId: 'plan',
    domainId: 'domain',
    benchRunId: 'run/1',
    metrics: { score: 0, maxScore: 1, passRate: 0, caseCount: 3 },
  };
  render(
    <StepOutput
      stepId="plan"
      output={{ baseline: { train: result, test: { ...result, producerStepId: 'older', benchRunId: 'run/2' } } }}
    />,
  );
  expect(screen.getByText('Train / Test Baseline')).toBeInTheDocument();
  expect(screen.getByText('本节点生成')).toBeInTheDocument();
  expect(screen.getByText('复用基线')).toBeInTheDocument();
  expect(screen.getByText('来源 Step：older')).toBeInTheDocument();
  expect(screen.getAllByText('0')).toHaveLength(4);
  fireEvent.click(screen.getByRole('button', { name: '评估结果 · run/1' }));
  expect(history.push).toHaveBeenLastCalledWith('/evolution-factory/bench/runs/run%2F1');
});
it('shows short Spec immediately and expands long Spec previews without changing the content', () => {
  const content = '# 验证目标\n' + '检查真实产出\n'.repeat(15);
  render(<StepOutput output={{ specV0: { version: 'v0', content_type: 'text', content } }} />);
  expect(screen.getByRole('heading', { name: '验证目标' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '展开完整 Spec' })).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(screen.getByRole('button', { name: '展开完整 Spec' }));
  expect(screen.getByRole('button', { name: '收起 Spec' })).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByText(/text · 17 行/)).toBeInTheDocument();
});
it('shows Case summaries, goals and train/test details without introducing placeholder data', () => {
  render(
    <StepOutput
      output={{
        diagnosis: { summary: '工具失效', issues: [{ title: '调用失败', severity: 'high', suggestion: '重试' }] },
        cases: {
          total: 4,
          goodCount: 1,
          badCount: 3,
          items: [1, 2, 3, 4].map((i) => ({ caseId: `case-${i}`, type: 'bad', summary: `摘要${i}` })),
        },
        goal: { title: '提升成功率', metrics: [{ name: '成功率', operator: '>=', target: 0.9 }] },
        benchCases: {
          trainCount: 2,
          testCount: 1,
          items: [{ split: 'train', sourceCaseId: 'source', template: { templateName: 'greeting', version: 2 } }],
        },
      }}
    />,
  );
  expect(screen.getByText('工具失效')).toBeInTheDocument();
  expect(screen.getByText('Good 1 / Bad 3')).toBeInTheDocument();
  expect(screen.getByText('提升成功率')).toBeInTheDocument();
  expect(screen.getByText('目标指标')).toBeInTheDocument();
  expect(screen.getByText('查看其余 1 个 Case').closest('details')).not.toHaveAttribute('open');
  expect(screen.getByText(/greeting · v2/)).toBeInTheDocument();
});
it('preserves metrics, review and round decisions and only formats numeric precision', () => {
  const comparison = { name: 'test_score', baseline: 0.73, candidate: 0.94, delta: 0.94 - 0.73 };
  render(
    <StepOutput
      output={{
        scoreComparison: comparison,
        benchDecision: 'passed',
        reviewStatus: 'accepted',
        roundDecision: { stop: false, reason: '还有改进空间' },
        metrics: [{ name: '完成率', value: 0, baselineValue: 0.1, delta: -0.1, target: 0.9, passed: false }],
      }}
    />,
  );
  expect(screen.getByText('+0.21')).toBeInTheDocument();
  expect(screen.getByText('未达标')).toBeInTheDocument();
  expect(screen.getByText('继续下一轮')).toBeInTheDocument();
  expect(screen.getByText('还有改进空间')).toBeInTheDocument();
  expect(comparison.delta).toBe(0.94 - 0.73);
});
it('renders direct Bench metrics as an object rather than losing them as a non-array', () => {
  render(
    <StepOutput
      output={{ benchRunId: 'bench-1', metrics: { score: 0.8, maxScore: 1, passRate: 0.75, caseCount: 4 } }}
    />,
  );
  expect(screen.getByText('0.8 / 1')).toBeInTheDocument();
  expect(screen.getByText('0.75')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '评估结果 · bench-1' })).toBeInTheDocument();
});
it('retains unknown output as collapsed raw data and does not invent scores', () => {
  render(<StepOutput output={{ customOutput: '真实结果' }} />);
  expect(screen.queryByText('Bench / Review')).not.toBeInTheDocument();
  expect(screen.getByText('原始产出数据 · 技术信息').closest('details')).not.toHaveAttribute('open');
});
it('builds compact entry summaries from real output only', () => {
  expect(outputHighlights({ spec: { version: 'v2' }, diff: { summary: '变更' } })).toEqual(['Spec v2', '变更 Diff']);
  expect(outputHighlights({})).toEqual([]);
});
