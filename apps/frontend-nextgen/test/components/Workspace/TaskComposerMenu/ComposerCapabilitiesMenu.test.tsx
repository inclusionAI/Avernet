/** @jest-environment jsdom */
/** ComposerCapabilitiesMenu 双区拆分回归（add-composer-capability-set-viewer）：
 * ＋ 区维持原混排菜单行为、「通用协作能力」文案区按 capabilitySets 装配渲染/隐藏、
 * 多胶囊 chips 并列叠加与逐 chip x 清除。 */
import { ComposerCapabilitiesMenu } from '@/components/Workspace/TaskComposerMenu';
import type { ComposerCapabilitySetsState } from '@/components/Workspace/CapabilitySetViewer/types';
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

/** 能力集装配桩（单聊 我的/团队 Bot 场景传入）。 */
function capabilitySets(overrides: Partial<ComposerCapabilitySetsState> = {}): ComposerCapabilitySetsState {
  return {
    sets: [{ id: 'set-1', name: '默认能力集', isDefault: true, mcps: [] }],
    isLoading: false,
    load: jest.fn(),
    fetchSetSkills: jest.fn().mockResolvedValue({ skills: [], failed: false }),
    ...overrides,
  };
}

describe('ComposerCapabilitiesMenu（胶囊双区拆分）', () => {
  it('可供调试会话仅复用附件和能力集入口，不显示任务创建项', async () => {
    const user = userEvent.setup();
    render(
      <ComposerCapabilitiesMenu
        selections={[]}
        onUpload={jest.fn()}
        capabilitySets={capabilitySets()}
        showTaskEntries={false}
      />,
    );

    await user.click(screen.getByRole('button', { name: '添加附件' }));
    expect(screen.getByRole('button', { name: '上传文件' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '任务' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '工作流任务' })).not.toBeInTheDocument();
  });

  it('未装配 capabilitySets（好友Bot/协作群形态）：仅渲染 ＋ 区，「通用协作能力」不渲染', () => {
    render(<ComposerCapabilitiesMenu execution={execution()} selections={[]} enableWorkflow />);
    expect(screen.getByRole('button', { name: '添加附件' })).toHaveAttribute('aria-haspopup', 'menu');
    expect(screen.queryByRole('button', { name: '通用协作能力' })).not.toBeInTheDocument();
  });

  it('装配 capabilitySets（我的/团队Bot）：＋ 区与「通用协作能力」区并列渲染', () => {
    render(
      <ComposerCapabilitiesMenu
        execution={execution()}
        selections={[]}
        enableWorkflow
        capabilitySets={capabilitySets()}
      />,
    );
    const attachTrigger = screen.getByRole('button', { name: '添加附件' });
    expect(attachTrigger).toHaveAttribute('aria-haspopup', 'menu');
    expect(attachTrigger).toHaveAttribute('aria-expanded', 'false');
    const capabilityTrigger = screen.getByRole('button', { name: '通用协作能力' });
    expect(capabilityTrigger).toHaveAttribute('aria-haspopup', 'menu');
    expect(capabilityTrigger).toHaveAttribute('aria-expanded', 'false');
  });

  it('＋ 区维持原混排菜单：附件行与任务行同在，选动态任务回调生效', async () => {
    const onDynamicSelected = jest.fn();
    const onUpload = jest.fn();
    const user = userEvent.setup();
    render(
      <ComposerCapabilitiesMenu
        execution={execution()}
        selections={[]}
        onUpload={onUpload}
        onDynamicSelected={onDynamicSelected}
        capabilitySets={capabilitySets()}
      />,
    );
    await user.click(screen.getByRole('button', { name: '添加附件' }));
    expect(screen.getByRole('button', { name: '上传文件' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '任务' })).toBeInTheDocument();
    expect(screen.queryByText('能力集')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '任务' }));
    expect(onDynamicSelected).toHaveBeenCalledTimes(1);
    // 「通用协作能力」触发器仍常驻（未连带触发生效）。
    expect(screen.getByRole('button', { name: '通用协作能力' })).toBeInTheDocument();
  });

  it('「通用协作能力」区打开能力集列表并触发 load（不牵连 ＋ 区气泡）', async () => {
    const set = capabilitySets();
    const user = userEvent.setup();
    render(
      <ComposerCapabilitiesMenu
        execution={execution()}
        selections={[]}
        enableWorkflow
        capabilitySets={set}
      />,
    );
    await user.click(screen.getByRole('button', { name: '通用协作能力' }));
    expect(set.load).toHaveBeenCalledTimes(1);
    expect(screen.getByText('默认能力集')).toBeInTheDocument();
    expect(screen.getByText('查看明细')).toBeInTheDocument();
    // ＋ 区气泡未被连带打开。
    expect(screen.queryByRole('button', { name: '上传文件' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '任务' })).not.toBeInTheDocument();
  });

  it('选中后胶囊常驻、chip 并列展示，可继续添加（原单选取代形态回归）', () => {
    const selections: ComposerSelection[] = [
      { kind: 'workflow', workflow: { workflowId: 'wf-1', title: '工作流A' } },
      { kind: 'dynamic' },
    ];
    render(
      <ComposerCapabilitiesMenu
        execution={execution()}
        selections={selections}
        onRemoveSelection={() => {}}
        capabilitySets={capabilitySets()}
      />,
    );
    // 双触发器常驻：不因存在选中态而消失。
    expect(screen.getByRole('button', { name: '添加附件' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '通用协作能力' })).toBeInTheDocument();
    // 两枚 chip 并列展示，各带 x 清除钮。
    const clears = screen.getAllByRole('button', { name: '取消选择' });
    expect(clears).toHaveLength(2);
    expect(screen.getByText('工作流A')).toBeInTheDocument();
    expect(screen.getByText('任务')).toBeInTheDocument();
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
    await user.click(screen.getAllByRole('button', { name: '取消选择' })[0]);
    expect(onRemoveSelection).toHaveBeenCalledWith('wf-1');
  });
});
