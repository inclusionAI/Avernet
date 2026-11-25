/** @jest-environment jsdom */
import { EvaluationHistoryCard } from '@/components/BotWorkshop/ServiceBotEvaluation/EvaluationHistoryCard';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

it('当前发布单和历史版本分成两个板块展示最终门禁结果和材料链接', () => {
  render(
    <EvaluationHistoryCard
      currentRelease={{
        id: '1',
        release_id: 'release-current',
        publish_id: 'publish-current',
        bot_version: '8',
        gate_status: 'PASSED',
        cert_id: 'cert-current',
        report_url: 'https://example.com/report-current',
        archive_download_url: 'https://example.com/data-current',
      }}
      records={[
        {
          id: '2',
          release_id: 'release-7',
          publish_id: 'publish-7',
          bot_version: '7',
          gate_status: 'PASSED',
          report_url: 'https://example.com/report-7',
          archive_download_url: 'https://example.com/data-7',
        },
        {
          id: '3',
          release_id: 'release-6',
          publish_id: 'publish-6',
          bot_version: '6',
          gate_status: 'BLOCKED',
        },
      ]}
    />,
  );

  expect(screen.getByText('当前 Publish ID 最终门禁结果')).toBeInTheDocument();
  expect(screen.getByText('历史版本最终门禁结果')).toBeInTheDocument();

  expect(screen.getByText('当前发布单 · 8')).toBeInTheDocument();
  expect(screen.getByText(/Publish ID：publish-current/)).toBeInTheDocument();
  expect(screen.getByText(/认证编号：cert-current/)).toBeInTheDocument();
  expect(screen.getAllByText('门禁通过')).toHaveLength(2);

  expect(screen.getByText('版本 7')).toBeInTheDocument();
  expect(screen.getByText('版本 6')).toBeInTheDocument();
  expect(screen.getByText('门禁未通过')).toBeInTheDocument();
  expect(screen.getAllByText('标准统一评测')).toHaveLength(2);
  expect(screen.getByText('极简统一评测')).toBeInTheDocument();

  expect(screen.getAllByRole('link', { name: /评测报告/ })[0]).toHaveAttribute(
    'href',
    'https://example.com/report-current',
  );
  expect(screen.getAllByRole('link', { name: /评测数据/ })[0]).toHaveAttribute(
    'href',
    'https://example.com/data-current',
  );
});

it('标准统一评测记录不会被旧 mode 字段覆盖', () => {
  render(
    <EvaluationHistoryCard
      currentRelease={{
        gate_status: 'PASSED',
        mode: '极简统一评测',
        task_type: 'STANDARD_EVAL',
      }}
      records={[
        {
          gate_status: 'PASSED',
          mode: '极简统一评测',
          decision_status: 'STANDARD_EVAL_FINISHED',
        },
        {
          gate_status: 'PASSED',
          mode: '极简统一评测',
          cert_id: 'standard_eval:uuid-1',
        },
      ]}
    />,
  );

  expect(screen.getAllByText('标准统一评测')).toHaveLength(3);
});

it('缺失评测模式时默认展示标准统一评测', () => {
  render(
    <EvaluationHistoryCard
      currentRelease={{ gate_status: 'BLOCKED', mode: '' }}
      records={[{ gate_status: 'BLOCKED' }]}
    />,
  );

  expect(screen.getAllByText('标准统一评测')).toHaveLength(2);
});

it('只有拿到极简评测标识时才展示极简统一评测', () => {
  render(
    <EvaluationHistoryCard
      currentRelease={{ gate_status: 'BLOCKED', task_type: 'SIMPLE_CERT_SUBMIT' }}
      records={[
        { gate_status: 'BLOCKED', decision_status: 'succeeded' },
        { gate_status: 'BLOCKED', cert_id: 'securityac-cert' },
      ]}
    />,
  );

  expect(screen.getAllByText('极简统一评测')).toHaveLength(3);
});

it('当前发布单或历史版本没有记录时展示各自空态', () => {
  render(<EvaluationHistoryCard currentRelease={null} records={[]} />);

  expect(screen.getByText('当前发布单暂无最终结论')).toBeInTheDocument();
  expect(screen.getByText('暂无历史版本结论')).toBeInTheDocument();
});
