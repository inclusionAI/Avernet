/** @jest-environment jsdom */
import { TaskDetailModal } from '@/components/CollaborationSquare/TaskDetailModal';
import type { PublicTask } from '@/domain/collaborationSquare/types';
import '@testing-library/jest-dom';
import { act, fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const longGoal =
  '对齐 Q3 产品方向，输出含优先级与依赖标注的路线图文档，覆盖核心方向至少 4 个，标注依赖与风险并给出双月排期建议，作为团队协作的唯一对齐基准，不可被 line-clamp 截断。';

const pendingTask: PublicTask = {
  id: 'task-plaza-001',
  name: '梳理 Q3 产品路线图',
  goal: longGoal,
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

describe('TaskDetailModal', () => {
  test('open 受控：open=false 时不渲染弹层内容', () => {
    render(<TaskDetailModal open={false} task={pendingTask} loading={false} onClose={jest.fn()} />);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  test('展示完整任务目标（不截断，无 line-clamp）与全部验收标准', () => {
    render(<TaskDetailModal open task={pendingTask} loading={false} onClose={jest.fn()} />);
    const goal = screen.getByText(longGoal);
    expect(goal).toBeInTheDocument();
    // 详情弹层不截断：目标段落不带卡片用的 line-clamp-2
    expect(goal).not.toHaveClass('line-clamp-2');
    // 全部验收标准每条可见
    expect(screen.getByText('覆盖至少 4 个核心方向')).toBeInTheDocument();
    expect(screen.getByText('标注依赖与风险')).toBeInTheDocument();
    expect(screen.getByText('给出双月排期建议')).toBeInTheDocument();
  });

  test('标题区展示任务名与状态徽标文案（文字 + 语义 dot 双通道）', () => {
    render(<TaskDetailModal open task={pendingTask} loading={false} onClose={jest.fn()} />);
    expect(screen.getByText('梳理 Q3 产品路线图')).toBeInTheDocument();
    // 状态文案存在（双通道，非仅颜色）
    expect(screen.getAllByText('待认领').length).toBeGreaterThanOrEqual(1);
    // 状态徽标含语义 dot（圆角色块）。弹层经 Radix Portal 渲染到 document.body，从 dialog 内查找。
    const dot = screen.getByRole('dialog').querySelector('span[aria-hidden="true"]');
    expect(dot).not.toBeNull();
    expect(dot?.className).toMatch(/rounded-full/);
  });

  test('发布者与发布时间展示', () => {
    render(<TaskDetailModal open task={pendingTask} loading={false} onClose={jest.fn()} />);
    expect(screen.getByText('产品协作助手')).toBeInTheDocument();
    expect(screen.getByText(/发布于 2026-08-19 09:00/)).toBeInTheDocument();
  });

  test('已认领任务展示认领者与认领时间', () => {
    render(<TaskDetailModal open task={claimedTask} loading={false} onClose={jest.fn()} />);
    expect(screen.getAllByText('运维协作助手').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(/认领于 2026-08-21 08:15/)).toBeInTheDocument();
    expect(screen.getAllByText('已认领').length).toBeGreaterThanOrEqual(1);
  });

  test('未认领任务不渲染认领区与完成时间', () => {
    render(<TaskDetailModal open task={pendingTask} loading={false} onClose={jest.fn()} />);
    expect(screen.queryByText(/认领于/)).not.toBeInTheDocument();
    expect(screen.queryByText(/完成时间/)).not.toBeInTheDocument();
  });

  test('已完成任务展示完成时间', () => {
    render(<TaskDetailModal open task={completedTask} loading={false} onClose={jest.fn()} />);
    expect(screen.getByText(/完成时间/)).toBeInTheDocument();
    expect(screen.getByText('2026-08-14 17:20')).toBeInTheDocument();
    expect(screen.getAllByText('已完成').length).toBeGreaterThanOrEqual(1);
  });

  test('待验收任务展示「开始验收时间」（而非「完成时间」）', () => {
    const task: PublicTask = { ...completedTask, status: 'reviewing', completedAt: '2026-08-14T17:20:00' };
    render(<TaskDetailModal open task={task} loading={false} onClose={jest.fn()} />);
    expect(screen.getByText(/开始验收时间/)).toBeInTheDocument();
    expect(screen.getByText('2026-08-14 17:20')).toBeInTheDocument();
    expect(screen.getAllByText('待验收').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText(/完成时间/)).not.toBeInTheDocument();
  });

  test('有 output 时展示「输出内容」区与产出文本', () => {
    const task: PublicTask = { ...completedTask, output: '最终产出文档内容' };
    render(<TaskDetailModal open task={task} loading={false} onClose={jest.fn()} />);
    expect(screen.getByText('输出内容')).toBeInTheDocument();
    expect(screen.getByText('最终产出文档内容')).toBeInTheDocument();
  });

  test('有 publisher 与 publisherName 时发布者展示为「publisher（publisherName）」', () => {
    const task: PublicTask = { ...completedTask, publisher: '20260825_bohtfhe6', publisherName: '自动研发Bot' };
    render(<TaskDetailModal open task={task} loading={false} onClose={jest.fn()} />);
    expect(screen.getByText('20260825_bohtfhe6（自动研发Bot）')).toBeInTheDocument();
  });

  test('有 publisher 但无 publisherName 时发布者只展示 publisher ID', () => {
    const task: PublicTask = { ...completedTask, publisher: '20260825_bohtfhe6' };
    render(<TaskDetailModal open task={task} loading={false} onClose={jest.fn()} />);
    expect(screen.getByText('20260825_bohtfhe6')).toBeInTheDocument();
  });

  test('输出内容位于当前状态之后（长文本放末尾，不挤占中段布局）', () => {
    const task: PublicTask = { ...completedTask, output: '产出内容' };
    render(<TaskDetailModal open task={task} loading={false} onClose={jest.fn()} />);
    const statusLabel = screen.getByText('当前状态');
    const outputLabel = screen.getByText('输出内容');
    expect(statusLabel.compareDocumentPosition(outputLabel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  test('无 output 时不渲染「输出内容」区', () => {
    render(<TaskDetailModal open task={pendingTask} loading={false} onClose={jest.fn()} />);
    expect(screen.queryByText('输出内容')).not.toBeInTheDocument();
  });

  test('loading=true 时内容区显示骨架占位，不显示任务正文', () => {
    render(<TaskDetailModal open task={null} loading onClose={jest.fn()} />);
    expect(screen.getByLabelText('正在加载任务详情')).toBeInTheDocument();
    expect(screen.queryByText('梳理 Q3 产品路线图')).not.toBeInTheDocument();
  });

  test('open=true 且 task=null 且非 loading 时不崩溃（空态）', () => {
    render(<TaskDetailModal open task={null} loading={false} onClose={jest.fn()} />);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByText('梳理 Q3 产品路线图')).not.toBeInTheDocument();
  });

  test('只读：仅关闭途径，无任何写操作按钮', () => {
    render(<TaskDetailModal open task={pendingTask} loading={false} onClose={jest.fn()} />);
    const buttons = screen.getAllByRole('button');
    // 仅 Modal primitive 自带的关闭按钮，无其它操作入口
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAccessibleName('关闭任务详情');
    const writeActions = ['认领', '提交', '取消', '对话', '跳转', '验收', '重新认领'];
    writeActions.forEach((keyword) => {
      expect(screen.queryByRole('button', { name: new RegExp(keyword) })).not.toBeInTheDocument();
    });
  });

  test('关闭按钮点击触发 onClose', async () => {
    const user = userEvent.setup();
    const onClose = jest.fn();
    render(<TaskDetailModal open task={pendingTask} loading={false} onClose={onClose} />);
    await user.click(screen.getByRole('button', { name: '关闭任务详情' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  test('Escape 触发 onClose', async () => {
    const user = userEvent.setup();
    const onClose = jest.fn();
    render(<TaskDetailModal open task={pendingTask} loading={false} onClose={onClose} />);
    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalled();
  });

  test('遮罩/弹层外部点击触发 onClose', async () => {
    const onClose = jest.fn();
    render(<TaskDetailModal open task={pendingTask} loading={false} onClose={onClose} />);
    // 弹层遮罩为满屏半透明层（含 bg-black），存在且在 DialogContent 之外
    expect(document.body.querySelector('div[class*="bg-black"]')).not.toBeNull();
    // Radix DismissableLayer 的 pointerdown 监听经 setTimeout(0) 延迟注册，先刷一个宏任务使其挂载。
    await act(async () => {
      await new Promise((resolve) => {
        setTimeout(resolve, 0);
      });
    });
    // 在弹层之外的 body 上派发 pointerdown，触发「内容外」关闭。
    fireEvent.pointerDown(document.body);
    expect(onClose).toHaveBeenCalled();
  });

  test('源码遵守 UI 与分层约束（无裸 button/dialog/select、animate-pulse、bg-gray、internal）', () => {
    const source = readFileSync(
      path.join(process.cwd(), 'src/components/CollaborationSquare/TaskDetailModal/index.tsx'),
      'utf8',
    );
    expect(source).not.toContain('<button');
    expect(source).not.toContain('<dialog');
    expect(source).not.toContain('<select');
    expect(source).not.toContain('animate-pulse');
    expect(source).not.toContain('bg-gray-');
    expect(source).not.toContain('message.');
    expect(source).not.toContain('src/internal');
  });
});
