/** @jest-environment jsdom */
import type { HelpLink } from '@/capabilities';
import { describe, expect, it, jest } from '@jest/globals';
import { fireEvent, render, screen } from '@testing-library/react';

let mockLinks: HelpLink[] = [];
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getHelpLinks: () => ({ status: 'available', value: mockLinks }),
    getReleaseNotesCapability: () => ({ status: 'available', value: null }),
    // PlatformMetricsPanel 顶层会调用 getMetricsDashboard；Open Core 回退 url=null
    getMetricsDashboard: () => ({ status: 'available', value: { url: null } }),
  }),
}));

const { HelpMenu } = require('@/shell/HelpMenu') as typeof import('@/shell/HelpMenu');

describe('HelpMenu', () => {
  it('Open Core（links=[] + release unsupported）: 仍渲染问号 trigger', () => {
    mockLinks = [];
    render(<HelpMenu />);
    const trigger = screen.getByRole('button', { name: '帮助' });
    expect(trigger).toBeTruthy();
    fireEvent.click(trigger);
    expect(screen.getByText('暂无帮助入口')).toBeTruthy();
    expect(screen.queryByText('版本发布说明')).toBeNull();
  });

  it('有外链: 一级行统一图标+文字，产品获取为 hover 子菜单', () => {
    mockLinks = [
      { label: '用户手册', href: 'https://manual', icon: 'manual', group: 'manual' },
      { label: '答疑机器人', href: 'dingtalk://x', icon: 'robot', group: 'robot' },
      { label: 'TUI', href: 'https://tui', description: '命令行', icon: 'tui', group: 'product' },
      { label: '移动端', href: 'https://m', icon: 'mobile', group: 'product' },
      { label: '桌面端', href: 'https://d', icon: 'desktop', group: 'product' },
    ];
    render(<HelpMenu />);
    fireEvent.click(screen.getByRole('button', { name: '帮助' }));
    // 一级行统一图标+文字
    expect(screen.getByText('用户手册')).toBeTruthy();
    expect(screen.getByText('答疑机器人')).toBeTruthy();
    expect(screen.getByText('产品获取')).toBeTruthy();
  });
});
