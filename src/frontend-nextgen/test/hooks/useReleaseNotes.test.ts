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
});
