/** @jest-environment jsdom */
// collab 形态任务栏分支回归（独立文件 = 独立模块注册表）：先注册全 null 槽位再静态 import
// GroupChatCollabPanel（其模块作用域常量按求值一次性绑定 GroupTaskRail=null），覆盖：
// - 轨缺席 + 无活动内容时不渲染空 border-t 带边框容器（修复前残留一条游离分隔线，T6 评审 Minor）；
// - 轨缺席 + 有活动内容时活动条仍在原带边框容器内展示（run 状态指示不随形态精简消失）。
// Open（轨非空）恒渲染容器，DOM 恒等由既有 ManagerWorkerTasks 套件（registerOpenGroupTaskSlots）覆盖。
import '../../../../helpers/registerCollabNullSlots';
import type { GroupView, SessionView } from '@/domain/collaboration';
import { GroupChatCollabPanel } from '@/pages/Workspace/components/GroupChatPane/GroupChatCollabPanel';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';

const taskGroup = { groupId: 'g1', name: '主仆群', kind: 'task_master_slave' } as unknown as GroupView;

const managerSession = {
  sessionId: 's1',
  participants: [{ actorId: 'manager', kind: 'bot', name: 'Manager', role: 'manager', mode: 'auto' }],
} as unknown as SessionView;

const baseProps = {
  panel: { visible: false } as never,
  group: taskGroup,
  session: managerSession,
  abortingBotIds: new Set<string>(),
  abortBot: async () => undefined,
};

describe('GroupChatCollabPanel — collab 空槽位（任务栏轨缺席）', () => {
  it('无活动内容时不渲染空 border-t 容器（无游离分隔线、无任务栏）', () => {
    const { container } = render(<GroupChatCollabPanel {...baseProps} messages={[]} />);
    expect(container.querySelector('.border-t')).toBeNull();
    // 任务栏轨（「查看任务」入口）不渲染；活动条无内容自渲染 null。
    expect(screen.queryByRole('button', { name: /查看任务/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('群聊处理状态')).not.toBeInTheDocument();
  });

  it('有活动 run 时活动条仍在带边框容器内渲染（活动指示不随形态精简消失）', () => {
    const { container } = render(
      <GroupChatCollabPanel
        {...baseProps}
        messages={[
          {
            id: 'stream',
            role: 'assistant',
            status: 'streaming',
            content: '分发中',
            extra: { botUuid: 'manager', runId: 'run' },
          } as never,
        ]}
      />,
    );
    // dev #3550 后 task_master_slave 分支的 ActiveRuns 走 managerMode → ManagerActivityBar（aria-label=Manager 处理状态）。
    const strip = screen.getByLabelText('Manager 处理状态');
    expect(strip).toBeInTheDocument();
    // 活动条位于带回的带边框容器内（容器仅在有活动内容时渲染）。
    expect(container.querySelector('.border-t')).not.toBeNull();
    expect(container.querySelector('.border-t')?.contains(strip)).toBe(true);
    // 任务栏轨仍缺席：不渲染「查看任务」入口。
    expect(screen.queryByRole('button', { name: /查看任务/ })).not.toBeInTheDocument();
  });
});
