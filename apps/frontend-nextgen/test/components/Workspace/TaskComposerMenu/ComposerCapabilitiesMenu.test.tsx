/** @jest-environment jsdom */
/** ComposerCapabilitiesMenu 多胶囊回归：＋触发器常驻不因选中态消失、chips 并列叠加、逐 chip x 清除。 */
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import type { UseTaskExecutionResult } from '@/hooks/useTaskExecution';
import type { ComposerSelection } from '@/services/tasks/taskMapper';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

function execution(overrides: Partial<UseTaskExecutionResult> = {}): UseTaskExecutionResult {
  return {
    submitting: false,
    error: null,
    lastTaskId: null,
    workflows: [],
    workflowsLoading: false,
    loadWorkflows: jest.fn().mockResolvedValue(undefined),
    selections: [],
    selectWorkflow: jest.fn(),
    selectDynamic: jest.fn(),
    removeSelection: jest.fn(),
    clearSelection: jest.fn(),
    submit: jest.fn(),
    validate: jest.fn(() => null),
    ...overrides,
  };
}

describe('ComposerCapabilitiesMenu（多胶囊叠加）', () => {
  it('默认态渲染常驻胶囊触发器（＋|通用协作能力▾）', () => {
    render(<ComposerCapabilitiesMenu execution={execution()} selections={[]} enableWorkflow />);
    const trigger = screen.getByRole('button', { name: '通用协作能力' });
    expect(trigger).toHaveAttribute('aria-haspopup', 'menu');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
  });

  it('选中后触发器仍在、chip 并列展示，可继续添加（原单选取代形态回归）', () => {
    const selections: ComposerSelection[] = [
      { kind: 'workflow', workflow: { workflowId: 'wf-1', title: '工作流A' } },
      { kind: 'dynamic' },
    ];
    render(
      <ComposerCapabilitiesMenu
        execution={execution()}
        selections={selections}
        onRemoveSelection={() => {}}
      />,
    );
    // ＋触发器常驻：不因存在选中态而消失。
    const trigger = screen.getByRole('button', { name: '通用协作能力' });
    expect(trigger).toBeInTheDocument();
    // 两枚 chip 并列展示，各带 x 清除钮。
    const clears = screen.getAllByRole('button', { name: '取消选择' });
    expect(clears).toHaveLength(2);
    expect(screen.getByText('工作流A')).toBeInTheDocument();
    expect(screen.getByText('动态任务')).toBeInTheDocument();
  });

  it('点 chip 的 x 按 key 调用 onRemoveSelection', async () => {
    const onRemoveSelection = jest.fn();
    const selections: ComposerSelection[] = [{ kind: 'workflow', workflow: { workflowId: 'wf-1', title: '工作流A' } }];
    const user = userEvent.setup();
    render(
      <ComposerCapabilitiesMenu
        execution={execution()}
        selections={selections}
        onRemoveSelection={onRemoveSelection}
      />,
    );
    await user.click(screen.getByRole('button', { name: '取消选择' }));
    expect(onRemoveSelection).toHaveBeenCalledWith('wf-1');
  });

  it('菜单选动态任务走叠加回调 onDynamicSelected（不清空既有胶囊）', async () => {
    const onDynamicSelected = jest.fn();
    const selections: ComposerSelection[] = [{ kind: 'workflow', workflow: { workflowId: 'wf-1', title: 'A' } }];
    const user = userEvent.setup();
    render(
      <ComposerCapabilitiesMenu
        execution={execution()}
        selections={selections}
        onDynamicSelected={onDynamicSelected}
      />,
    );
    await user.click(screen.getByRole('button', { name: '通用协作能力' }));
    await user.click(screen.getByRole('button', { name: '动态任务' }));
    expect(onDynamicSelected).toHaveBeenCalledTimes(1);
    // 触发器仍在（同一 render 内未卸载）。
    expect(screen.getByRole('button', { name: '通用协作能力' })).toBeInTheDocument();
  });
});