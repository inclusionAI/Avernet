/** @jest-environment node */
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { beforeEach, describe, expect, it } from '@jest/globals';

describe('errorNotifyStore', () => {
  beforeEach(() => useErrorNotifyStore.getState().reset());

  it('初始队列为空', () => {
    expect(useErrorNotifyStore.getState().queue).toEqual([]);
  });

  it('enqueue 追加记录并注入 ts', () => {
    useErrorNotifyStore.getState().enqueue({ toastKey: 'k1', message: 'm1' });
    const [item] = useErrorNotifyStore.getState().queue;
    expect(item.toastKey).toBe('k1');
    expect(item.message).toBe('m1');
    expect(typeof item.ts).toBe('number');
    expect(item.cancelled).toBeUndefined();
  });

  it('cancel 标记对应 toastKey 的记录为取消,不影响其它', () => {
    useErrorNotifyStore.getState().enqueue({ toastKey: 'k1', message: 'm1' });
    useErrorNotifyStore.getState().enqueue({ toastKey: 'k2', message: 'm2' });

    useErrorNotifyStore.getState().cancel('k1');

    const queue = useErrorNotifyStore.getState().queue;
    expect(queue[0].cancelled).toBe(true);
    expect(queue[1].cancelled).toBeUndefined();
  });

  it('drain 返回当前队列并清空', () => {
    useErrorNotifyStore.getState().enqueue({ toastKey: 'k1', message: 'm1' });
    useErrorNotifyStore.getState().enqueue({ toastKey: 'k2', message: 'm2' });

    const drained = useErrorNotifyStore.getState().drain();

    expect(drained).toHaveLength(2);
    expect(drained[0].toastKey).toBe('k1');
    expect(drained[1].toastKey).toBe('k2');
    expect(useErrorNotifyStore.getState().queue).toEqual([]);
  });

  it('drain 返回被 cancel 的记录(由观察者按 cancelled 跳过)', () => {
    useErrorNotifyStore.getState().enqueue({ toastKey: 'k1', message: 'm1' });
    useErrorNotifyStore.getState().cancel('k1');

    const [item] = useErrorNotifyStore.getState().drain();
    expect(item.cancelled).toBe(true);
  });

  it('reset 清空队列', () => {
    useErrorNotifyStore.getState().enqueue({ toastKey: 'k1', message: 'm1' });
    useErrorNotifyStore.getState().reset();
    expect(useErrorNotifyStore.getState().queue).toEqual([]);
  });
});
