/** @jest-environment jsdom */
import type { MetricsDashboardSpec } from '@/capabilities';
import { expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

let mockUrl: MetricsDashboardSpec['url'] = null;

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getMetricsDashboard: () => ({ status: 'available', value: { url: mockUrl } }),
  }),
}));

const noop = () => undefined;

const ANTMONITOR_URL =
  'https://metrics.example.com/dashboard/preview/example?fullScreen=true&hideHeader=true&viewMode=1';

const { PlatformMetricsPanel } =
  require('@/shell/PlatformMetricsPanel') as typeof import('@/shell/PlatformMetricsPanel');

it('URL 非空（internal overlay）：渲染 AntMonitor iframe + 加载骨架（对齐旧 ocb 指标大盘）', () => {
  mockUrl = ANTMONITOR_URL;
  render(<PlatformMetricsPanel open onClose={noop} />);

  const iframe = screen.getByTitle('AntMonitor 指标大盘') as HTMLIFrameElement;
  expect(iframe).toBeInTheDocument();
  expect(iframe.getAttribute('src')).toBe(ANTMONITOR_URL);
  expect(iframe.getAttribute('sandbox')).toBe('allow-scripts allow-same-origin allow-forms allow-popups');

  // iframe onLoad 在 jsdom 不触发 → 加载骨架持续展示
  expect(screen.getByText('加载指标大盘中...')).toBeInTheDocument();
  // title 旁外链图标新开 AntMonitor
  const external = screen.getByLabelText('在新窗口打开 AntMonitor 大盘');
  expect(external).toHaveAttribute('href', ANTMONITOR_URL);
  expect(external).toHaveAttribute('target', '_blank');

  // 不应渲染静态占位 4 区文案
  expect(screen.queryByText('图表占位（数据待接入）')).not.toBeInTheDocument();
});

it('URL 为 null（Open Core 回退）：渲染静态占位 4 区，不渲染 iframe', () => {
  mockUrl = null;
  render(<PlatformMetricsPanel open onClose={noop} />);

  expect(screen.getByText('平台指标大盘')).toBeInTheDocument();
  expect(screen.getByText('服务端接口调用成功率')).toBeInTheDocument();
  // 3 条成功率折线占位卡，每张含相同的占位文案 → 用 getAllByText 断言数量
  expect(screen.getAllByText('图表占位（数据待接入）')).toHaveLength(3);
  expect(screen.getByText('Arca-quota 使用')).toBeInTheDocument();
  expect(screen.queryByTitle('AntMonitor 指标大盘')).not.toBeInTheDocument();
  expect(screen.queryByText('加载指标大盘中...')).not.toBeInTheDocument();
});
