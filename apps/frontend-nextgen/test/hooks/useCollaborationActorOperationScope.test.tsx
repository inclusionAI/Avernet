/** @jest-environment jsdom */
// bcs-standalone-friend-discovery Task 4：所选角色写操作的代次守卫（captureOperation）。
// 角色切换（A→B）、A→B→A、卸载与重新挂载都必须使旧代次 guard 立即失效，
// 保护不能只来自 AbortSignal 或 actor id 比对（epoch 也参与判定）。
import { useCollaborationActorOperationScope } from '@/hooks/useCollaborationActorOperationScope';
import { act, renderHook } from '@testing-library/react';

describe('useCollaborationActorOperationScope 代次守卫', () => {
  function renderScope(initialKey: string) {
    const hook = renderHook(({ key }: { key: string }) => useCollaborationActorOperationScope(key), {
      initialProps: { key: initialKey },
    });
    return { hook };
  }

  it('挂载后的 guard 有效，同一代次内多次捕获返回独立且都有效的 guard', () => {
    const { hook } = renderScope('["900003","bot","human","900003"]');
    let first: () => boolean = () => true;
    let second: () => boolean = () => true;

    act(() => {
      first = hook.result.current();
      second = hook.result.current();
    });

    expect(first()).toBe(true);
    expect(second()).toBe(true);
  });

  it('scopeKey 变化（A→B）后旧 guard 同步失效，新代次 guard 有效', () => {
    const { hook } = renderScope('["900003","bot","bot","bot-a"]');
    let stale: () => boolean = () => true;
    let fresh: () => boolean = () => true;
    act(() => {
      stale = hook.result.current();
    });

    act(() => {
      hook.rerender({ key: '["900003","bot","bot","bot-b"]' });
    });
    // rerender 的 layout effect 已同步把旧代次置为失效；新 guard 在新代次提交后捕获。
    act(() => {
      fresh = hook.result.current();
    });

    expect(stale()).toBe(false);
    expect(fresh()).toBe(true);
  });

  it('A→B→A 不复活 A 的旧 guard：相同 key 下旧代次依旧失效，新 guard 独立有效', () => {
    const keyA = '["900003","bot","human","900003"]';
    const keyB = '["900003","bot","bot","bot-a"]';
    const { hook } = renderScope(keyA);
    let guardA1: () => boolean = () => true;
    let guardA2: () => boolean = () => true;
    act(() => {
      guardA1 = hook.result.current();
    });

    act(() => {
      hook.rerender({ key: keyB });
    });
    act(() => {
      hook.rerender({ key: keyA });
    });
    act(() => {
      guardA2 = hook.result.current();
    });

    // A 的旧 guard（epoch 更旧）不得因 key 再次相等而复活。
    expect(guardA1()).toBe(false);
    expect(guardA2()).toBe(true);
  });

  it('卸载后 guard 失效，重新挂载得到全新代次 guard，且旧 guard 不复活', () => {
    const keyA = '["900003","bot","human","900003"]';
    const first = renderScope(keyA);
    let guardOld: () => boolean = () => true;
    act(() => {
      guardOld = first.hook.result.current();
    });

    act(() => {
      first.hook.unmount();
    });
    expect(guardOld()).toBe(false);

    const second = renderScope(keyA);
    let guardNew: () => boolean = () => true;
    act(() => {
      guardNew = second.hook.result.current();
    });
    expect(guardNew()).toBe(true);
    // 旧实例的 guard 仍失效（epoch + active 双重判定）。
    expect(guardOld()).toBe(false);
  });

  it('guard 判定含 epoch：仅 key 相同不足以复活旧结果', () => {
    const keyA = '["900003","bot","human","900003"]';
    const keyB = '["900003","bot","bot","bot-a"]';
    const { hook } = renderScope(keyA);
    let guardA: () => boolean = () => true;
    act(() => {
      guardA = hook.result.current();
    });
    act(() => {
      hook.rerender({ key: keyB });
    });
    act(() => {
      hook.rerender({ key: keyA });
      // 回到同 key：若实现只比较 key，这里会错误放行旧结果。
      expect(guardA()).toBe(false);
    });
  });

  it('captureOperation 引用稳定，不随 scopeKey 变化重建', () => {
    const { hook } = renderScope('["900003","bot","human","900003"]');
    const initial = hook.result.current;
    act(() => {
      hook.rerender({ key: '["900003","bot","bot","bot-a"]' });
    });
    expect(hook.result.current).toBe(initial);
  });
});
