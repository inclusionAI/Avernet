/** @jest-environment jsdom */
/** CapabilitySetViewer 组件单测（add-composer-capability-set-viewer）：
 * 列表 ⇄ 明细双页导航（头部换题 + 返回）、整行可点开明细、明细只读（无任何编辑
 * 入口）、空/加载/失败-重试态。 */
import { CapabilitySetViewer } from '@/components/Workspace/CapabilitySetViewer';
import type { CapabilitySetView, ComposerCapabilitySetsState } from '@/components/Workspace/CapabilitySetViewer/types';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

function makeSet(overrides: Partial<CapabilitySetView> = {}): CapabilitySetView {
  return {
    id: 'set-1',
    name: '默认能力集',
    isDefault: true,
    mcps: [{ serverCode: 'mcp-a', name: 'MCP A' }],
    ...overrides,
  };
}

function state(overrides: Partial<ComposerCapabilitySetsState> = {}): ComposerCapabilitySetsState {
  return {
    sets: [makeSet()],
    isLoading: false,
    load: jest.fn(),
    fetchSetSkills: jest.fn().mockResolvedValue({
      skills: [{ skillId: 'sk-1', name: '技能一', description: 'd' }],
      failed: false,
    }),
    ...overrides,
  };
}

describe('CapabilitySetViewer', () => {
  it('空态：暂无已激活的能力集', () => {
    render(<CapabilitySetViewer state={state({ sets: [] })} />);
    expect(screen.getByText('暂无已激活的能力集')).toBeInTheDocument();
  });

  it('加载中骨架', () => {
    render(<CapabilitySetViewer state={state({ isLoading: true })} />);
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText('加载中…')).toBeInTheDocument();
  });

  it('渲染能力集行 + 查看明细', () => {
    render(
      <CapabilitySetViewer
        state={state({
          sets: [makeSet(), makeSet({ id: 'set-b', name: '调研', isDefault: false })],
        })}
      />,
    );
    expect(screen.getByText('已激活的能力集')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /默认能力集/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /调研/ })).toBeInTheDocument();
    expect(screen.getAllByText('查看明细')).toHaveLength(2);
  });

  it('明细展开拉取 Skills/MCP 且纯只读（无任何编辑入口）', async () => {
    const fetchSetSkills = jest.fn().mockResolvedValue({
      skills: [{ skillId: 'sk-1', name: '技能一' }],
      failed: false,
    });
    const user = userEvent.setup();
    render(<CapabilitySetViewer state={state({ sets: [makeSet()], fetchSetSkills })} />);
    await user.click(screen.getByRole('button', { name: /默认能力集/ }));
    await waitFor(() => expect(screen.getByText('技能一')).toBeInTheDocument());
    expect(screen.getByText('MCP A')).toBeInTheDocument();
    expect(fetchSetSkills).toHaveBeenCalledWith('set-1');
    // 双页导航：头部换题为集名 + 数量，返回回到列表。
    expect(screen.queryByText('已激活的能力集')).not.toBeInTheDocument();
    expect(screen.getByText('共 1 个 Skill · 1 个 MCP')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /返回/ }));
    expect(screen.getByText('已激活的能力集')).toBeInTheDocument();
    expect(screen.getAllByText('查看明细')).toHaveLength(1);
    // 只读性：明细/列表中不出现任何编辑动作入口。
    for (const forbidden of ['新建', '添加', '移除', '删除', '启用', '停用']) {
      expect(screen.queryByRole('button', { name: forbidden })).not.toBeInTheDocument();
    }
  });

  it('明细拉取失败 → 错误提示 + 重试恢复', async () => {
    const fetchSetSkills = jest
      .fn()
      .mockResolvedValueOnce({ skills: [], failed: true })
      .mockResolvedValueOnce({ skills: [{ skillId: 'sk-2', name: '技能二' }], failed: false });
    const user = userEvent.setup();
    render(<CapabilitySetViewer state={state({ sets: [makeSet()], fetchSetSkills })} />);
    await user.click(screen.getByRole('button', { name: /默认能力集/ }));
    await waitFor(() => expect(screen.getByText('明细加载失败')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: /重试/ }));
    await waitFor(() => expect(screen.getByText('技能二')).toBeInTheDocument());
    expect(fetchSetSkills).toHaveBeenCalledTimes(2);
  });
});