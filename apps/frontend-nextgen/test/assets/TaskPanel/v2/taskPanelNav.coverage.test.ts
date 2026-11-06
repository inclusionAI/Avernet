/** @jest-environment jsdom */
// useLocalTaskPanelNav 覆盖补齐:selectDefaultNode 幂等分支 + drillInto 去重与两层层级封顶。
import type { ResolvedSessionTarget } from '@/assets/TaskPanel/sessionResolution';
import { useLocalTaskPanelNav } from '@/assets/TaskPanel/v2/taskPanelNav';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from '@jest/globals';

const target: ResolvedSessionTarget | null = { sessionId: 'sess-1', kind: 'bot' };

describe('useLocalTaskPanelNav 覆盖补齐', () => {
  it('selectDefaultNode:相同 nodeId 原样返回,不同则更新选中态', () => {
    const { result } = renderHook(() => useLocalTaskPanelNav());

    act(() => result.current.selectDefaultNode('n1'));
    expect(result.current.selectedNodeId).toBe('n1');

    // 相同 nodeId → fn 直接返回原 state(stable 引用路径)
    act(() => result.current.selectDefaultNode('n1'));
    expect(result.current.selectedNodeId).toBe('n1');

    act(() => result.current.selectDefaultNode('n2'));
    expect(result.current.selectedNodeId).toBe('n2');
  });

  it('drillInto:重复入栈被去重,超过 2 层封顶丢弃', () => {
    const { result } = renderHook(() => useLocalTaskPanelNav());

    act(() => result.current.drillInto('n1', target));
    expect(result.current.drillStack).toEqual(['n1']);

    // 已在栈中的节点不重复 push
    act(() => result.current.drillInto('n1', target));
    expect(result.current.drillStack).toEqual(['n1']);

    act(() => result.current.drillInto('n2', target));
    expect(result.current.drillStack).toEqual(['n1', 'n2']);

    // 封顶 2 层:第 3 层会被丢弃
    act(() => result.current.drillInto('n3', target));
    expect(result.current.drillStack).toEqual(['n1', 'n2']);
  });

  it('selectRootNode/selectNode 写选中态,backToOverview 清栈不清选中', () => {
    const { result } = renderHook(() => useLocalTaskPanelNav());

    act(() => result.current.selectRootNode('n-root'));
    act(() => result.current.selectNode('n9', target));
    expect(result.current.selectedNodeId).toBe('n9');

    act(() => result.current.backToOverview());
    expect(result.current.drillStack).toEqual([]);
    expect(result.current.selectedNodeId).toBe('n9');
  });
});
