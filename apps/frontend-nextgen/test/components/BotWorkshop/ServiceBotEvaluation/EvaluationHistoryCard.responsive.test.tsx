/** @jest-environment jsdom */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { EvaluationHistoryCard } from '@/components/BotWorkshop/ServiceBotEvaluation/EvaluationHistoryCard';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

const pageSource = readFileSync(
  path.join(process.cwd(), 'src/pages/BotWorkshop/Evaluation/index.tsx'),
  'utf8',
);

describe('service bot evaluation responsive layout', () => {
  it('evaluation page fills the browser width instead of capping at max-w-7xl', () => {
    expect(pageSource).toContain('<div className="w-full p-5 sm:p-6">');
    expect(pageSource).not.toContain('max-w-7xl');
  });

  it('history records stack on mobile and restore the row layout from sm upward', () => {
    render(
      <EvaluationHistoryCard
        records={[
          {
            id: 'eval-1',
            mode: '手动认证',
            status: 'PASS',
            summary: '这是一条超长评测摘要，验证在窄屏下不会撑开布局。',
            evaluated_at: '2026-11-11 19:56:02',
            report_url: 'https://example.com/report',
          },
        ]}
      />,
    );

    const content = screen.getByText('手动认证');
    const left = content.parentElement as HTMLElement;
    const row = left.parentElement as HTMLElement;
    expect(row).toHaveClass(
      'flex',
      'flex-col',
      'gap-3',
      'sm:flex-row',
      'sm:items-center',
      'sm:justify-between',
    );

    const summary = screen.getByText(/这是一条超长评测摘要/);
    expect(summary).toHaveClass('break-words');

    const actions = (screen.getByRole('link', { name: /查看报告/ }).parentElement as HTMLElement);
    expect(actions).toHaveClass('flex', 'shrink-0', 'items-center', 'gap-2');
  });
});
