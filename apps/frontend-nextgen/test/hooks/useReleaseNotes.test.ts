/** @jest-environment jsdom */
import type { ReleaseNotesCapability, ReleaseNotesData } from '@/capabilities';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook, waitFor } from '@testing-library/react';

// 最小 localStorage + window shim（node jest 无 DOM）
class LS {
  private m = new Map<string, string>();
  getItem(k: string) {
    return this.m.has(k) ? this.m.get(k)! : null;
  }
  setItem(k: string, v: string) {
    this.m.set(k, v);
  }
  removeItem(k: string) {
    this.m.delete(k);
  }
  clear() {
    this.m.clear();
  }
}

let mockCap: ReleaseNotesCapability | null = null;
jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getReleaseNotesCapability: () => ({ status: 'available', value: mockCap }),
  }),
}));

// 动态 import 以确保 mock 生效后再拉 hook
const { useReleaseNotes } = require('@/hooks/useReleaseNotes') as typeof import('@/hooks/useReleaseNotes');

describe('useReleaseNotes', () => {
  beforeEach(() => {
    (globalThis as any).window = globalThis;
    (window as any).localStorage = new LS();
    mockCap = null;
  });

  it('capability null → supported:false', () => {
    mockCap = null;
    const { result } = renderHook(() => useReleaseNotes());
    expect(result.current.supported).toBe(false);
    expect(result.current.data).toBeNull();
  });

  it('load 成功 → ready + data', async () => {
    const data: ReleaseNotesData = { version: '1.0', date: '2026-08-21', releaseNoteHtml: '<p>new</p>' };
    mockCap = {
      load: jest.fn<() => Promise<ReleaseNotesData | null>>().mockResolvedValue(data),
      markSeen: jest.fn(),
      getSeenDate: jest.fn<() => string | null>().mockReturnValue(null),
    };
    const { result } = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.data).toEqual(data);
    expect(result.current.supported).toBe(true);
  });

  it('新发布日期首次加载 → 自动打开 Modal、标记已读且同一日期不重复打开', async () => {
    const data: ReleaseNotesData = { version: '1.1', date: '2026-09-05', releaseNoteHtml: '<p>new</p>' };
    let seenDate: string | null = null;
    const markSeen = jest.fn((date: string) => {
      seenDate = date;
    });
    mockCap = {
      load: jest.fn<() => Promise<ReleaseNotesData | null>>().mockResolvedValue(data),
      markSeen,
      getSeenDate: jest.fn(() => seenDate),
    };

    const first = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(first.result.current.status).toBe('ready'));
    expect(first.result.current.modalOpen).toBe(true);
    expect(first.result.current.hasNew).toBe(false);
    expect(markSeen).toHaveBeenCalledTimes(1);
    expect(markSeen).toHaveBeenCalledWith('2026-09-05');
    first.unmount();

    const second = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(second.result.current.status).toBe('ready'));
    expect(second.result.current.modalOpen).toBe(false);
    expect(second.result.current.hasNew).toBe(false);
    expect(markSeen).toHaveBeenCalledTimes(1);
  });

  it('load 返回 null → error', async () => {
    mockCap = {
      load: jest.fn<() => Promise<ReleaseNotesData | null>>().mockResolvedValue(null),
      markSeen: jest.fn(),
      getSeenDate: jest.fn<() => string | null>().mockReturnValue(null),
    };
    const { result } = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(result.current.status).toBe('error'));
    expect(result.current.data).toBeNull();
  });

  it('markSeenAndClose 调 capability.markSeen + 关闭 modal', async () => {
    const data: ReleaseNotesData = { version: '1.0', date: '2026-08-21' };
    const markSeen = jest.fn();
    mockCap = {
      load: jest.fn<() => Promise<ReleaseNotesData | null>>().mockResolvedValue(data),
      markSeen,
      getSeenDate: jest.fn<() => string | null>().mockReturnValue(null),
    };
    const { result } = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(result.current.status).toBe('ready'));
    act(() => {
      result.current.open();
    });
    expect(result.current.modalOpen).toBe(true);
    act(() => {
      result.current.markSeenAndClose();
    });
    expect(markSeen).toHaveBeenCalledWith('2026-08-21');
    expect(result.current.modalOpen).toBe(false);
  });

  it('菜单手动打开 → 立即标记当前发布日期已读并清红点', async () => {
    const data: ReleaseNotesData = { version: '1.0', date: '2026-08-21' };
    const markSeen = jest.fn();
    mockCap = {
      load: jest.fn<() => Promise<ReleaseNotesData | null>>().mockResolvedValue(data),
      markSeen,
      getSeenDate: jest.fn<() => string | null>().mockReturnValue('2026-08-21'),
    };
    const { result } = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(result.current.status).toBe('ready'));

    act(() => {
      result.current.open();
    });

    expect(markSeen).toHaveBeenCalledWith('2026-08-21');
    expect(result.current.modalOpen).toBe(true);
    expect(result.current.hasNew).toBe(false);
  });
});
