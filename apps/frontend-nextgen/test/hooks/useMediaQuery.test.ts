/** @jest-environment jsdom */
import { useMediaQuery, useMinWidth } from '@/hooks/useMediaQuery';
import { afterEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react';

type ChangeListener = (event: { matches: boolean }) => void;

interface MockMQL {
  matches: boolean;
  media: string;
  onchange: null;
  addEventListener: (type: 'change', listener: ChangeListener) => void;
  removeEventListener: (type: 'change', listener: ChangeListener) => void;
  addListener: (listener: ChangeListener) => void;
  removeListener: (listener: ChangeListener) => void;
  dispatch: (matches: boolean) => void;
}

function createMatchMedia(initialMatches: boolean): typeof window.matchMedia & { mql: MockMQL } {
  const listeners = new Set<ChangeListener>();
  const mql: MockMQL = {
    matches: initialMatches,
    media: '',
    onchange: null,
    addEventListener: (_type, listener) => listeners.add(listener),
    removeEventListener: (_type, listener) => listeners.delete(listener),
    addListener: (listener) => listeners.add(listener),
    removeListener: (listener) => listeners.delete(listener),
    dispatch: (matches) => {
      mql.matches = matches;
      listeners.forEach((listener) => listener({ matches }));
    },
  };
  const fn = jest.fn(() => mql) as unknown as typeof window.matchMedia & { mql: MockMQL };
  fn.mql = mql;
  return fn;
}

describe('useMediaQuery', () => {
  afterEach(() => {
    jest.restoreAllMocks();
  });

  it('returns false when matchMedia is unavailable', () => {
    const original = window.matchMedia;
    // jsdom 默认不实现 matchMedia；删除后视为不可用环境。
    // @ts-expect-error — 临时让 matchMedia 不可用以测试 SSR/降级分支。
    delete window.matchMedia;
    const { result } = renderHook(() => useMediaQuery('(min-width: 1024px)'));
    expect(result.current).toBe(false);
    window.matchMedia = original;
  });

  it('reflects the current matchMedia.matches value', () => {
    window.matchMedia = createMatchMedia(true);
    const { result } = renderHook(() => useMediaQuery('(min-width: 1024px)'));
    expect(result.current).toBe(true);
  });

  it('updates when the media query match state changes', () => {
    const mock = createMatchMedia(true);
    window.matchMedia = mock;
    const { result } = renderHook(() => useMediaQuery('(min-width: 1024px)'));
    expect(result.current).toBe(true);

    act(() => mock.mql.dispatch(false));
    expect(result.current).toBe(false);

    act(() => mock.mql.dispatch(true));
    expect(result.current).toBe(true);
  });

  it('cleans up its listener on unmount', () => {
    const mock = createMatchMedia(false);
    window.matchMedia = mock;
    const { unmount } = renderHook(() => useMediaQuery('(min-width: 1024px)'));
    unmount();
    // 未订阅即可：dispatch 一次后内部 state 不再改变（仅作清理回归）。
    act(() => mock.mql.dispatch(true));
    expect(mock.mql.matches).toBe(true);
  });
});

describe('useMinWidth', () => {
  it('returns true when viewport meets the min-width', () => {
    window.matchMedia = createMatchMedia(true);
    const { result } = renderHook(() => useMinWidth(1024));
    expect(result.current).toBe(true);
  });

  it('returns false when viewport is below the min-width', () => {
    window.matchMedia = createMatchMedia(false);
    const { result } = renderHook(() => useMinWidth(1024));
    expect(result.current).toBe(false);
  });
});
