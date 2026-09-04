/** @jest-environment jsdom */
import TaskCard, { formatTaskDate, TaskStatusBadge } from '@/components/CollaborationSquare/TaskCard';
import type { PublicTask } from '@/domain/collaborationSquare/types';
import '@testing-library/jest-dom';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const pendingTask: PublicTask = {
  id: 'task-plaza-001',
  name: '梳理 Q3 产品路线图',
  goal: '对齐 Q3 产品方向，输出含优先级与依赖标注的路线图文档。',
  acceptanceCriteria: ['覆盖至少 4 个核心方向', '标注依赖与风险', '给出双月排期建议'],
  status: 'pending_claim',
  publisherBotName: '产品协作助手',
  publishedAt: '2026-08-19T09:00:00',
};

const claimedTask: PublicTask = {
  ...pendingTask,
  id: 'task-plaza-003',
  name: '代码评审流程梳理',
  status: 'claimed',
  publisherBotName: '研发协作助手',
  publishedAt: '2026-08-18T14:00:00',
  claimedBotName: '运维协作助手',
  claimedAt: '2026-08-21T08:15:00',
};

const reviewingTask: PublicTask = {
  ...pendingTask,
  id: 'task-plaza-005',
  name: '需求评审纪要收敛',
  status: 'reviewing',
  publisherBotName: '产品协作助手',
  publishedAt: '2026-08-15T13:30:00',
  claimedBotName: '运维协作助手',
  claimedAt: '2026-08-16T09:10:00',
};

const completedTask: PublicTask = {
  ...pendingTask,
  id: 'task-plaza-006',
  name: '用户访谈洞察归档',
  status: 'completed',
  publisherBotName: '研究分析助手',
  publishedAt: '2026-08-05T10:00:00',
  claimedBotName: '效率协作助手',
  claimedAt: '2026-08-06T09:00:00',
  completedAt: '2026-08-14T17:20:00',
};

describe('formatTaskDate', () => {
  test('格式化本地 ISO 字符串为 YYYY-MM-DD HH:MM（24 小时制、零填充、到分钟）', () => {
    expect(formatTaskDate('2026-08-19T15:30:00')).toBe('2026-08-19 15:30');
    expect(formatTaskDate('2026-08-09T09:05:00')).toBe('2026-08-09 09:05');
    expect(formatTaskDate('2026-01-01T00:00:00')).toBe('2026-01-01 00:00');
  });

  test('带时区的 ISO 输入按本地时区格式化为 YYYY-MM-DD HH:MM（具体值随运行时区，形状恒定）', () => {
    const formatted = formatTaskDate('2026-08-09T00:00:00Z');
    expect(formatted).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
    expect(formatted).toHaveLength(16);
  });

  test('date-only 字符串格式化为 YYYY-MM-DD HH:MM（本地时区补全时分）', () => {
    expect(formatTaskDate('2026-08-09')).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
  });

  test('空字符串返回空串（保持不崩）', () => {
    expect(formatTaskDate('')).toBe('');
  });

  test('undefined 返回空串', () => {
    expect(formatTaskDate(undefined)).toBe('');
  });

  test('null 返回空串', () => {
    expect(formatTaskDate(null)).toBe('');
  });

  test('非法日期字符串返回空串', () => {
    expect(formatTaskDate('not-a-date')).toBe('');
  });
});

describe('TaskCard', () => {
  test('待认领任务展示发布者、任务名、目标、验收标准与状态文案', () => {
    render(<TaskCard task={pendingTask} onOpenDetail={jest.fn()} />);
    expect(screen.getByText('梳理 Q3 产品路线图')).toBeInTheDocument();
    expect(screen.getByText('发布者：产品协作助手')).toBeInTheDocument();
    expect(screen.getByText(/对齐 Q3 产品方向/)).toBeInTheDocument();
    expect(screen.getByText('待认领')).toBeInTheDocument();
    expect(screen.getByText('验收标准')).toBeInTheDocument();
    expect(screen.getByText('覆盖至少 4 个核心方向')).toBeInTheDocument();
    expect(screen.getByText('标注依赖与风险')).toBeInTheDocument();
    expect(screen.getByText('给出双月排期建议')).toBeInTheDocument();
    expect(screen.getByText(/发布于 2026-08-19 09:00/)).toBeInTheDocument();
    expect(screen.getByText('等待认领')).toBeInTheDocument();
  });

  test('有 publisherName 时卡片发布者优先展示 publisherName（不展示 publisherBotName）', () => {
    const withName: PublicTask = {
      ...pendingTask,
      publisherName: '自动研发Bot',
      publisherBotName: '产品协作助手',
    };
    render(<TaskCard task={withName} onOpenDetail={jest.fn()} />);
    expect(screen.getByText('发布者：自动研发Bot')).toBeInTheDocument();
    expect(screen.queryByText('发布者：产品协作助手')).not.toBeInTheDocument();
  });

  test('已认领任务展示认领者与认领时间，底部展示认领信息', () => {
    render(<TaskCard task={claimedTask} onOpenDetail={jest.fn()} />);
    expect(screen.getByText('已认领')).toBeInTheDocument();
    expect(screen.getByText('运维协作助手')).toBeInTheDocument();
    expect(screen.getByText(/认领于 2026-08-21 08:15/)).toBeInTheDocument();
    expect(screen.getByText(/认领：运维协作助手/)).toBeInTheDocument();
    // 待认领提示在已认领态下不应出现
    expect(screen.queryByText('等待认领')).not.toBeInTheDocument();
  });

  test('待验收任务展示待验收状态文案（文字 + 徽标双通道，非仅颜色）', () => {
    render(<TaskCard task={reviewingTask} onOpenDetail={jest.fn()} />);
    expect(screen.getByText('待验收')).toBeInTheDocument();
  });

  test('已完成任务展示完成时间与已完成状态文案', () => {
    render(<TaskCard task={completedTask} onOpenDetail={jest.fn()} />);
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.getByText(/完成时间：2026-08-14 17:20/)).toBeInTheDocument();
  });

  test('待验收任务（带 completedAt）展示「开始验收时间」而非「完成时间」', () => {
    const task: PublicTask = { ...reviewingTask, completedAt: '2026-08-18T12:00:00' };
    render(<TaskCard task={task} onOpenDetail={jest.fn()} />);
    expect(screen.getByText('待验收')).toBeInTheDocument();
    expect(screen.getByText(/开始验收时间：2026-08-18 12:00/)).toBeInTheDocument();
    expect(screen.queryByText(/完成时间/)).not.toBeInTheDocument();
  });

  test('点击 goal 调用 onOpenDetail 并传入完整任务', async () => {
    const user = userEvent.setup();
    const onOpenDetail = jest.fn();
    render(<TaskCard task={pendingTask} onOpenDetail={onOpenDetail} />);
    const goalTrigger = screen.getByRole('button', { name: /对齐 Q3 产品方向/ });
    await user.click(goalTrigger);
    expect(onOpenDetail).toHaveBeenCalledTimes(1);
    expect(onOpenDetail).toHaveBeenCalledWith(pendingTask);
  });

  test('键盘 Enter/Space 在 goal 上也能打开详情（hover/触屏的键盘可访问等价路径）', () => {
    const onOpenDetail = jest.fn();
    render(<TaskCard task={pendingTask} onOpenDetail={onOpenDetail} />);
    const goalTrigger = screen.getByRole('button', { name: /对齐 Q3 产品方向/ });
    fireEvent.keyDown(goalTrigger, { key: 'Enter' });
    fireEvent.keyDown(goalTrigger, { key: ' ' });
    expect(onOpenDetail).toHaveBeenCalledTimes(2);
    expect(onOpenDetail).toHaveBeenCalledWith(pendingTask);
  });

  test('任务卡只读：仅 goal 一个可点击入口（打开详情），无任何写操作按钮', () => {
    render(<TaskCard task={pendingTask} onOpenDetail={jest.fn()} />);
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAccessibleName(pendingTask.goal);
    // 常见写操作文案不应出现
    const writeActions = ['认领', '提交', '取消', '对话', '跳转', '验收', '重新认领'];
    writeActions.forEach((keyword) => {
      expect(screen.queryByRole('button', { name: new RegExp(keyword) })).not.toBeInTheDocument();
    });
  });

  test('任务卡不泄漏内部任务 ID、节点、DAG、日志或颜色 hex', () => {
    const { container } = render(<TaskCard task={pendingTask} onOpenDetail={jest.fn()} />);
    expect(container.textContent).not.toContain('task-plaza-001');
    expect(container.textContent).not.toMatch(/节点|DAG|日志|node|workflow/i);
    // 原始 hex 颜色不应出现在内联样式或 class 中
    expect(container.innerHTML).not.toMatch(/#[0-9a-fA-F]{6}\b/);
  });

  test('状态徽标含语义 dot 与文案 label（双通道）', () => {
    const { container } = render(<TaskStatusBadge status="pending_claim" />);
    expect(container.textContent).toBe('待认领');
    // dot 为带圆角的语义色块
    const dot = container.querySelector('span[aria-hidden="true"]');
    expect(dot).not.toBeNull();
    expect(dot?.className).toMatch(/rounded-full/);
    expect(dot?.className).toMatch(/bg-warning/);
  });
});
