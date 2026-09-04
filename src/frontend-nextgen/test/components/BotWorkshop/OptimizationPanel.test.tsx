/** @jest-environment jsdom */

import { OptimizationPanel } from '@/components/BotWorkshop/BotHealthCheckDrawer/components/OptimizationPanel';
import type { BotHealthDimension } from '@/domain/botHealthCheck';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

function buildDimension(overrides: Partial<BotHealthDimension> = {}): BotHealthDimension {
  return {
    key: 'configuration',
    label: '配置健康度',
    scanDim: 'full:L1',
    description: 'Bot 是否有基础护栏',
    score: 100,
    scanStatus: 'completed',
    status: 'passed',
    checkedCount: 1,
    passedCount: 1,
    warningCount: 0,
    errorCount: 0,
    pendingCount: 0,
    checkItems: [{ name: 'AGENTS.md', status: 'passed', result: 'pass', score: 100 }],
    patches: [],
    ...overrides,
  };
}

describe('OptimizationPanel', () => {
  test('hides the section when diagnosis has issues but no suggestions', () => {
    render(
      <OptimizationPanel
        dimension={buildDimension({
          score: 30,
          status: 'error',
          passedCount: 0,
          errorCount: 3,
          checkItems: [{ name: 'AGENTS.md', status: 'error', result: 'fail', score: 25 }],
        })}
      />,
    );

    expect(screen.queryByText('优化建议')).not.toBeInTheDocument();
    expect(screen.queryByText('未发现需要优化的问题，Bot 状态良好！')).not.toBeInTheDocument();
  });

  test('keeps the healthy message for a passed diagnosis without issues', () => {
    render(<OptimizationPanel dimension={buildDimension()} />);

    expect(screen.getByText('优化建议')).toBeInTheDocument();
    expect(screen.getByText('未发现需要优化的问题，Bot 状态良好！')).toBeInTheDocument();
  });
});
