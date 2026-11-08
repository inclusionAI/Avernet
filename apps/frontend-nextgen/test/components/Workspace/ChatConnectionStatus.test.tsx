/** @jest-environment jsdom */
// 窄宽收纳矩阵（2026-10-19 用户反馈：窄视口「已连接」仅保绿点）。
// 收纳只作用于 success 正常态；警示/错误/中性态保留完整文案与语义色点。
// 悬停提示为 Radix Tooltip（TC-G006：禁用原生 title），交互按 SystemMessageItem 同款
// fake timers 口径驱动（不能依赖真实浏览器 hover/等待）。
import { ChatConnectionStatus } from '@/components/Workspace/ChatConnectionStatus';
import { describe, expect, it } from '@jest/globals';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, fireEvent, render, screen } from '@testing-library/react';

describe('ChatConnectionStatus 窄宽收纳', () => {
  it('success：文案 span 窄宽收纳（hidden + 视口可见类），读屏可访问名恒在', () => {
    render(<ChatConnectionStatus label="已连接" tone="success" />);

    const status = screen.getByRole('status', { name: '已连接' });
    expect(screen.getByText('已连接')).toHaveClass('hidden', 'lg:inline');
    expect(status).toHaveAttribute('aria-label', '已连接');
    expect(status.querySelector('span')).toHaveClass('bg-success');
  });

  it('success：labelVisibleClassName 支持命名容器并查（GroupHeader 主区被副屏挤压时同样仅绿点）', () => {
    render(
      <ChatConnectionStatus
        label="已连接"
        tone="success"
        labelVisibleClassName="lg:@min-[32.5rem]/group-header:inline"
      />,
    );

    expect(screen.getByText('已连接')).toHaveClass('hidden', 'lg:@min-[32.5rem]/group-header:inline');
  });

  it('警示/错误/中性态不收纳：任何宽度保留完整文案与语义色点（不全体绿点化）', () => {
    const { unmount } = render(<ChatConnectionStatus label="连接中" tone="warning" />);
    expect(screen.getByText('连接中')).not.toHaveClass('hidden');
    expect(screen.getByRole('status').querySelector('span')).toHaveClass('bg-warning');
    unmount();

    render(<ChatConnectionStatus label="连接失败" tone="error" />);
    expect(screen.getByText('连接失败')).not.toHaveClass('hidden');
    expect(screen.getByRole('status').querySelector('span')).toHaveClass('bg-destructive');
  });

  it('中性（已断开）态语义点保留弱灰中性色', () => {
    render(<ChatConnectionStatus label="已断开" tone="neutral" />);
    expect(screen.getByRole('status', { name: '已断开' }).querySelector('span')).toHaveClass('bg-muted-foreground/40');
    expect(screen.getByText('已断开')).not.toHaveClass('hidden');
  });
});

describe('ChatConnectionStatus 悬停提示（Radix Tooltip，非原生 title）', () => {
  it('悬停 200ms 后经 Portal 显示文案，锚点无原生 title；Escape 关闭', () => {
    jest.useFakeTimers();
    try {
      render(<ChatConnectionStatus label="已连接" tone="success" />);
      const status = screen.getByRole('status', { name: '已连接' });
      // 状态区为唯一锚点（TooltipTrigger asChild 零占位），可访问名不受影响。
      expect(status).toHaveAttribute('aria-label', '已连接');
      expect(status).not.toHaveAttribute('title');
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();

      fireEvent.pointerMove(status, { pointerType: 'mouse' });
      act(() => jest.advanceTimersByTime(200));
      expect(screen.getByRole('tooltip')).toHaveTextContent('已连接');

      fireEvent.keyDown(status, { key: 'Escape' });
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    } finally {
      jest.useRealTimers();
    }
  });

  it('收纳态（仅绿点）悬停同样能读到完整文案', () => {
    jest.useFakeTimers();
    try {
      render(
        <ChatConnectionStatus
          label="已连接"
          tone="success"
          labelVisibleClassName="lg:@min-[32.5rem]/group-header:inline"
        />,
      );
      const status = screen.getByRole('status', { name: '已连接' });

      fireEvent.pointerMove(status, { pointerType: 'mouse' });
      act(() => jest.advanceTimersByTime(200));
      expect(screen.getByRole('tooltip')).toHaveTextContent('已连接');
    } finally {
      jest.useRealTimers();
    }
  });
});