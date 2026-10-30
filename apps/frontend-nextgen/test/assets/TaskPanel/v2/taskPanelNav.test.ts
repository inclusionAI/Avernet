/** @jest-environment jsdom */
// 本地导航降级：默认选中、下钻去重/封顶与返回语义。
import { useLocalTaskPanelNav, type ResolvedSessionTarget } from '@/assets/TaskPanel/v2/taskPanelNav';
import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from '@jest/globals';

const target: ResolvedSessionTarget = { sessionId: 's-1', kind: 'bot' };

describe('useLocalTaskPanelNav', () => {
  it('记录默认选中且不重复更新相同节点', () => {
    const { result } = renderHook(() => useLocalTaskPanelNav());

    act(() => result.current.selectDefaultNode('n1'));
    expect(result.current.selectedNodeId).toBe('n1');

    act(() => result.current.selectDefaultNode('n1'));
    act(() => result.current.selectDefaultNode('n2'));
    expect(result.current.selectedNodeId).toBe('n2');
  });

  it('下钻去重并封顶两层，返回只清栈', () => {
    const { result } = renderHook(() => useLocalTaskPanelNav());

    act(() => result.current.selectNode('n1', target));
    act(() => result.current.selectNode('n2', null));
    expect(result.current.selectedNodeId).toBe('n2');

    act(() => result.current.drillInto('n1', target));
    expect(result.current.drillStack).toEqual(['n1']);

    act(() => result.current.drillInto('n1', null));
    expect(result.current.drillStack).toEqual(['n1']);

    act(() => result.current.drillInto('n2', target));
    expect(result.current.drillStack).toEqual(['n1', 'n2']);

    act(() => result.current.drillInto('n3', target));
    expect(result.current.drillStack).toEqual(['n1', 'n2']);

    act(() => result.current.backToOverview());
    expect(result.current.drillStack).toEqual([]);
    expect(result.current.selectedNodeId).toBe('n2');
  });
});
