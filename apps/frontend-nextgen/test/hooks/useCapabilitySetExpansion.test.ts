/** @jest-environment jsdom */
import type { BotCapabilitySet } from '@/domain/botEditor';
import { useCapabilitySetExpansion } from '@/hooks/useCapabilitySetExpansion';
import { act, renderHook } from '@testing-library/react';

const defaultSet: BotCapabilitySet = {
  id: 'default',
  name: '默认能力集',
  isDefault: true,
  active: true,
  skills: [],
  mcps: [],
  clis: [],
};
const customSet = { ...defaultSet, id: 'custom', isDefault: false };
beforeEach(() => {
  localStorage.clear();
});
afterEach(() => {
  jest.restoreAllMocks();
});
const useExpansion = ({ bot = 'bot-1', user = 'user-1' } = {}) =>
  useCapabilitySetExpansion([defaultSet, customSet], bot, user, 'owner-1');

test('首次默认收起，展开及收起均可跨页面重新挂载恢复', () => {
  let view = renderHook(() => useExpansion());
  expect(view.result.current.isExpanded(defaultSet)).toBe(false);
  act(() => view.result.current.toggle(defaultSet));
  act(() => view.result.current.toggle(customSet));
  view.unmount();
  view = renderHook(() => useExpansion());
  expect(view.result.current.isExpanded(defaultSet)).toBe(true);
  expect(view.result.current.isExpanded(customSet)).toBe(true);
  act(() => view.result.current.toggle(defaultSet));
  view.unmount();
  view = renderHook(() => useExpansion());
  expect(view.result.current.isExpanded(defaultSet)).toBe(false);
});

test('按登录用户及 Bot 隔离；切换回来恢复各自选择', () => {
  const view = renderHook((props) => useExpansion(props), { initialProps: { bot: 'bot-1', user: 'user-1' } });
  act(() => view.result.current.toggle(defaultSet));
  view.rerender({ bot: 'bot-2', user: 'user-1' });
  expect(view.result.current.isExpanded(defaultSet)).toBe(false);
  view.rerender({ bot: 'bot-1', user: 'user-2' });
  expect(view.result.current.isExpanded(defaultSet)).toBe(false);
  act(() => view.result.current.toggle(customSet));
  view.rerender({ bot: 'bot-1', user: 'user-1' });
  expect(view.result.current.isExpanded(defaultSet)).toBe(true);
  expect(view.result.current.isExpanded(customSet)).toBe(false);
});

test('异步加载及数据刷新不覆盖记忆，保留非默认首项的原展开规则', () => {
  const view = renderHook(({ sets }) => useCapabilitySetExpansion(sets, 'bot-1', 'user-1'), {
    initialProps: { sets: [] as BotCapabilitySet[] },
  });
  view.rerender({ sets: [customSet, defaultSet] });
  expect(view.result.current.isExpanded(customSet)).toBe(true);
  act(() => view.result.current.toggle(customSet));
  view.rerender({ sets: [{ ...customSet }, { ...defaultSet }] });
  expect(view.result.current.isExpanded(customSet)).toBe(false);
});

test('存储被禁用仍能在当前页面正常切换', () => {
  jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('blocked');
  });
  const view = renderHook(() => useExpansion());
  expect(view.result.current.isExpanded(defaultSet)).toBe(false);
  act(() => view.result.current.toggle(defaultSet));
  expect(view.result.current.isExpanded(defaultSet)).toBe(true);
});

test('忽略损坏或类型错误的历史记录', () => {
  const key = `bot-capability-expansion:v1:${JSON.stringify(['user-1', 'owner-1', 'bot-1'])}`;
  localStorage.setItem(key, '{bad json');
  const first = renderHook(() => useExpansion());
  expect(first.result.current.isExpanded(defaultSet)).toBe(false);
  first.unmount();
  localStorage.setItem(key, JSON.stringify({ default: 'true', custom: true }));
  const second = renderHook(() => useExpansion());
  expect(second.result.current.isExpanded(defaultSet)).toBe(false);
  expect(second.result.current.isExpanded(customSet)).toBe(true);
});
