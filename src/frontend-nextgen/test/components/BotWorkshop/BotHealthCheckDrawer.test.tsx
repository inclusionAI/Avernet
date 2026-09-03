/** @jest-environment jsdom */

import BotHealthCheckDrawer from '@/components/BotWorkshop/BotHealthCheckDrawer';
import type { BotHealthCheckSummary, BotHealthDimensionKey } from '@/domain/botHealthCheck';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';

const noop = () => undefined;

function buildDimension(
  key: BotHealthDimensionKey,
  label: string,
  scanDim: string,
  score: number,
): NonNullable<BotHealthCheckSummary['dimensions'][number]> {
  return {
    key,
    label,
    scanDim,
    score,
    status: 'passed',
    checkedCount: 2,
    passedCount: 2,
    warningCount: 0,
    errorCount: 0,
    checkItems: [
      {
        name: `${label} 检查项 A`,
        status: 'passed',
        result: 'pass',
        score: score,
      },
      {
        name: `${label} 检查项 B`,
        status: 'passed',
        result: 'pass',
        score: score,
      },
    ],
    patches: [],
    updatedAt: '2026-08-21T20:43:39Z',
  };
}

const summary: BotHealthCheckSummary = {
  botId: 'b1',
  entityId: 'u1',
  overallStatus: 'healthy',
  healthScore: 90,
  grade: 'good',
  latestAt: '2026-08-21T20:43:39Z',
  dimensions: [
    buildDimension('configuration', '配置健康度', 'full:L1', 85),
    buildDimension('taskUnderstanding', '任务理解力', 'full:L2', 92),
    buildDimension('planningExecution', '规划执行力', 'full:L3', 88),
    buildDimension('capabilityInvocation', '能力调用力', 'full:L4', 90),
    buildDimension('contextLearning', '上下文学习力', 'full:L5', 95),
    buildDimension('taskDelivery', '任务交付力', 'full:L6', 91),
  ],
  history: [
    {
      id: '1',
      scanId: 1,
      key: 'configuration',
      label: '配置健康度',
      scanDim: 'full:L1',
      score: 85,
      status: 'passed',
      checkedAt: '2026-08-21T20:43:39Z',
      dimension: buildDimension('configuration', '配置健康度', 'full:L1', 85),
    },
    {
      id: '2',
      scanId: 2,
      key: 'taskUnderstanding',
      label: '任务理解力',
      scanDim: 'full:L2',
      score: 92,
      status: 'passed',
      checkedAt: '2026-08-21T19:43:39Z',
      dimension: buildDimension('taskUnderstanding', '任务理解力', 'full:L2', 92),
    },
  ],
};

describe('BotHealthCheckDrawer', () => {
  test('renders all six dimension tabs and overview', () => {
    render(
      <BotHealthCheckDrawer
        open
        botName="墨韵预发测试3"
        summary={summary}
        loading={false}
        checking={false}
        onOpenChange={noop}
        onRefresh={noop}
        onRunDiagnose={noop}
      />,
    );

    expect(screen.getByText(/Bot 健康检查/)).toBeInTheDocument();
    expect(screen.getByText('墨韵预发测试3')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '配置健康度' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '任务理解力' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '规划执行力' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '能力调用力' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '上下文学习力' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '任务交付力' })).toBeInTheDocument();
  });

  test('active dimension panel shows check table and history', () => {
    render(
      <BotHealthCheckDrawer
        open
        botName="墨韵预发测试3"
        summary={summary}
        loading={false}
        checking={false}
        onOpenChange={noop}
        onRefresh={noop}
        onRunDiagnose={noop}
      />,
    );

    expect(screen.getByText('配置健康度体检')).toBeInTheDocument();
    expect(screen.getAllByText('检测项目')[0]).toBeInTheDocument();
    expect(screen.getByText('历史体检记录')).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: '查看详情' }).length).toBeGreaterThan(0);
  });

  test('switching dimension tab updates panel', () => {
    render(
      <BotHealthCheckDrawer
        open
        botName="墨韵预发测试3"
        summary={summary}
        loading={false}
        checking={false}
        onOpenChange={noop}
        onRefresh={noop}
        onRunDiagnose={noop}
      />,
    );

    expect(screen.getByText('配置健康度体检')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '任务理解力' }));
    expect(screen.getByText('任务理解力体检')).toBeInTheDocument();
  });

  test('opens history detail drawer when clicking 查看详情', () => {
    render(
      <BotHealthCheckDrawer
        open
        botName="墨韵预发测试3"
        summary={summary}
        loading={false}
        checking={false}
        onOpenChange={noop}
        onRefresh={noop}
        onRunDiagnose={noop}
      />,
    );

    fireEvent.click(screen.getAllByRole('button', { name: '查看详情' })[0]);
    expect(screen.getByText('体检详情')).toBeInTheDocument();
  });

  test('completed low-score diagnosis shows its optimization instead of an unknown execution failure', () => {
    const dimension = buildDimension('configuration', '配置健康度', 'full:L1', 30);
    dimension.status = 'error';
    dimension.scanStatus = 'completed';
    dimension.failedReason = null;
    dimension.patches = [
      {
        patch_id: 7,
        name: '补充安全边界',
        description: '完善 AGENTS.md 的操作限制',
        is_applied: false,
      },
    ];

    render(
      <BotHealthCheckDrawer
        open
        botName="低分 Bot"
        summary={{
          botId: 'b1',
          entityId: 'u1',
          overallStatus: 'critical',
          healthScore: 30,
          dimensions: [dimension],
          history: [],
        }}
        loading={false}
        checking={false}
        onOpenChange={noop}
        onRefresh={noop}
        onRunDiagnose={noop}
      />,
    );

    expect(screen.getByText('异常')).toBeInTheDocument();
    expect(screen.getByText(/补充安全边界/)).toBeInTheDocument();
    expect(screen.queryByText('检测失败：未知原因')).not.toBeInTheDocument();
  });
});
