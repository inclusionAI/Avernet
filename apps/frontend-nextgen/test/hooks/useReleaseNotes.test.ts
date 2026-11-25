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

// 动态 import 以确保 mock 生效后再拉 hook 与 store
const { useReleaseNotes } = require('@/hooks/useReleaseNotes') as typeof import('@/hooks/useReleaseNotes');
const { useReleaseNotesStore } = require('@/stores/releaseNotesStore') as typeof import('@/stores/releaseNotesStore');

describe('useReleaseNotes', () => {
  beforeEach(() => {
    (globalThis as any).window = globalThis;
    (window as any).localStorage = new LS();
    mockCap = null;
    act(() => useReleaseNotesStore.getState().reset());
  });

  it('capability null → supported:false', () => {
    mockCap = null;
    const { result } = renderHook(() => useReleaseNotes());
    expect(result.current.supported).toBe(false);
    expect(result.current.data).toBeNull();
    expect(result.current.status).toBe('idle');
  });

  it('load 成功 → ready + data', async () => {
    const data: ReleaseNotesData = { version: '1.0', date: '2026-08-21', releaseNoteHtml: '<p>new</p>' };
    mockCap = {
      load: jest.fn<() => Promise<ReleaseNotesData | null>>().mockResolvedValue(data),
      markSeen: jest.fn(),
      getSeenDate: jest.fn<() => string | null>().mockReturnValue('2026-08-21'),
    };
    const { result } = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.data).toEqual(data);
    expect(result.current.supported).toBe(true);
    // 已读同一标识 → 不自动弹、无红点
    expect(result.current.modalOpen).toBe(false);
    expect(result.current.hasNew).toBe(false);
  });

  it('新发布首次加载 → 自动开 Modal + 红点，但不写已读；主动关闭才 markSeen；关闭后下次不重弹', async () => {
    const data: ReleaseNotesData = { version: '1.1', date: '2026-09-05', releaseNoteHtml: '<p>new</p>' };
    let seenStamp: string | null = null;
    const markSeen = jest.fn((stamp: string) => {
      seenStamp = stamp;
    });
    mockCap = {
      load: jest.fn<() => Promise<ReleaseNotesData | null>>().mockResolvedValue(data),
      markSeen,
      getSeenDate: jest.fn(() => seenStamp),
    };

    const first = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(first.result.current.status).toBe('ready'));
    // 自动弹出 + 红点亮；打开动作本身不写已读（2026-11-10 已读语义拍板）
    expect(first.result.current.modalOpen).toBe(true);
    expect(first.result.current.hasNew).toBe(true);
    expect(markSeen).not.toHaveBeenCalled();

    // 用户主动关闭（按钮/ESC/遮罩统一收口）→ 视为已读 + 清红点
    act(() => first.result.current.closeModal());
    expect(markSeen).toHaveBeenCalledTimes(1);
    expect(markSeen).toHaveBeenCalledWith('2026-09-05');
    expect(first.result.current.modalOpen).toBe(false);
    expect(first.result.current.hasNew).toBe(false);
    first.unmount();

    // 模拟刷新：store 复位（seen 已写入）→ 不再自动弹、无红点
    act(() => useReleaseNotesStore.getState().reset());
    const second = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(second.result.current.status).toBe('ready'));
    expect(second.result.current.modalOpen).toBe(false);
    expect(second.result.current.hasNew).toBe(false);
  });

  it('弹窗未主动关闭即中断（模拟刷新）→ 已读不写，下次重弹 + 红点保留', async () => {
    const data: ReleaseNotesData = { version: '1.1', date: '2026-09-05', releaseNoteHtml: '<p>new</p>' };
    let seenStamp: string | null = null;
    const markSeen = jest.fn((stamp: string) => {
      seenStamp = stamp;
    });
    mockCap = {
      load: jest.fn<() => Promise<ReleaseNotesData | null>>().mockResolvedValue(data),
      markSeen,
      getSeenDate: jest.fn(() => seenStamp),
    };

    const first = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(first.result.current.status).toBe('ready'));
    expect(first.result.current.modalOpen).toBe(true);
    expect(markSeen).not.toHaveBeenCalled(); // 未主动关闭 → 被动中断不写已读
    first.unmount();

    act(() => useReleaseNotesStore.getState().reset());
    const second = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(second.result.current.status).toBe('ready'));
    expect(second.result.current.modalOpen).toBe(true); // 重弹
    expect(second.result.current.hasNew).toBe(true); // 红点兜底保留
  });

  it('stamp 字段优先于 date 参与已读比对（内容更新但 date 未改也可感知）', async () => {
    const data: ReleaseNotesData = {
      version: '1.2',
      date: '2026-09-05',
      stamp: '1.2|2026-09-05|c0nt3nt',
      releaseNoteHtml: '<p>updated</p>',
    };
    mockCap = {
      load: jest.fn<() => Promise<ReleaseNotesData | null>>().mockResolvedValue(data),
      markSeen: jest.fn(),
      // seen 为旧 date（与 res.date 相同、与 res.stamp 不同）→ 以 stamp 判定仍应视为未读
      getSeenDate: jest.fn<() => string | null>().mockReturnValue('2026-09-05'),
    };
    const { result } = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(result.current.status).toBe('ready'));
    expect(result.current.hasNew).toBe(true);
    expect(result.current.modalOpen).toBe(true);
  });

  it('双实例共享单例状态：单飞 load 只拉一次，一端关闭另一端同步', async () => {
    const data: ReleaseNotesData = { version: '1.1', date: '2026-09-05', releaseNoteHtml: '<p>new</p>' };
    const load = jest.fn<() => Promise<ReleaseNotesData | null>>().mockResolvedValue(data);
    mockCap = {
      load,
      markSeen: jest.fn(),
      getSeenDate: jest.fn<() => string | null>().mockReturnValue(null),
    };

    const first = renderHook(() => useReleaseNotes());
    const second = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(first.result.current.status).toBe('ready'));
    await waitFor(() => expect(second.result.current.status).toBe('ready'));
    expect(load).toHaveBeenCalledTimes(1); // 单飞：共享实例不重复拉取
    expect(second.result.current.modalOpen).toBe(true);

    act(() => first.result.current.closeModal());
    expect(second.result.current.modalOpen).toBe(false); // 跨实例状态同步
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
    expect(result.current.modalOpen).toBe(false);
    expect(result.current.hasNew).toBe(false);
  });

  it('手动打开 → 不写已读、红点保留；markSeenAndClose 关闭 → 写已读并清红点', async () => {
    const data: ReleaseNotesData = { version: '1.0', date: '2026-08-21' };
    const markSeen = jest.fn();
    mockCap = {
      load: jest.fn<() => Promise<ReleaseNotesData | null>>().mockResolvedValue(data),
      markSeen,
      getSeenDate: jest.fn<() => string | null>().mockReturnValue('2026-08-20'), // 有未读
    };
    const { result } = renderHook(() => useReleaseNotes());
    await waitFor(() => expect(result.current.status).toBe('ready'));

    act(() => {
      result.current.open();
    });
    expect(result.current.modalOpen).toBe(true);
    expect(result.current.hasNew).toBe(true); // 打开动作不清红点
    expect(markSeen).not.toHaveBeenCalled(); // 也不写已读

    act(() => {
      result.current.markSeenAndClose();
    });
    expect(markSeen).toHaveBeenCalledWith('2026-08-21');
    expect(result.current.modalOpen).toBe(false);
    expect(result.current.hasNew).toBe(false);
  });
});
