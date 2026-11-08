/** @jest-environment jsdom */
// 时间窗消息定位(P4 Ruling 4:纯函数 + DOM best-effort,未命中优雅降级)。
import {
  locateMessageWindowTimestamp,
  scrollToMessageTimestampWindow,
} from '@/assets/TaskPanel/messageWindowLocate';
import { describe, expect, it, jest } from '@jest/globals';

describe('locateMessageWindowTimestamp', () => {
  const msgs = [10, 20, 30, 40, 50].map((ts) => ({ ts }));

  it('窗口命中 → 返回窗口内第一条的 ts', () => {
    expect(locateMessageWindowTimestamp(msgs, 20, 45)).toBe(20);
    expect(locateMessageWindowTimestamp(msgs, 30, 30)).toBe(30);
  });

  it('窗口未命中 → 定位到时间窗之后的第一条消息', () => {
    // (25, 28) 窗口内无消息,任务开始处应取 30,而不是上一条 20。
    expect(locateMessageWindowTimestamp(msgs, 25, 28)).toBe(30);
  });

  it('窗口全在最早消息之前 → 定位到最早消息', () => {
    expect(locateMessageWindowTimestamp(msgs, 5, 8)).toBe(10);
  });

  it('无时间窗(均为 null)→ null', () => {
    expect(locateMessageWindowTimestamp(msgs, null, null)).toBeNull();
  });

  it('只有 endTime → 定位到 endTime 后的第一条消息', () => {
    expect(locateMessageWindowTimestamp(msgs, null, 35)).toBe(40);
  });

  it('空消息 → null', () => {
    expect(locateMessageWindowTimestamp([], 1, 2)).toBeNull();
  });
});

describe('scrollToMessageTimestampWindow(DOM best-effort)', () => {
  const setupDom = (tss: number[]): HTMLElement[] => {
    document.body.innerHTML = '';
    return tss.map((ts) => {
      const el = document.createElement('div');
      el.setAttribute('data-ts', String(ts));
      document.body.appendChild(el);
      return el;
    });
  };

  it('命中 [data-ts] 元素并 scrollIntoView,返回 true', () => {
    const els = setupDom([10, 20, 30, 40]);
    const spyScroll = jest.fn();
    els.forEach((el) => (el.scrollIntoView = spyScroll));
    const ok = scrollToMessageTimestampWindow(document, 20, 35);
    expect(ok).toBe(true);
    expect(spyScroll).toHaveBeenCalledTimes(1);
  });

  it('兼容 [data-timestamp] / [data-time] 属性', () => {
    document.body.innerHTML = '';
    const a = document.createElement('div');
    a.setAttribute('data-timestamp', '100');
    document.body.appendChild(a);
    const b = document.createElement('div');
    b.setAttribute('data-time', '200');
    document.body.appendChild(b);
    a.scrollIntoView = jest.fn();
    b.scrollIntoView = jest.fn();
    const ok = scrollToMessageTimestampWindow(document, 100, 150);
    expect(ok).toBe(true);
  });

  it('无消息 DOM 命中 → false 不抛错(降级由调用方记录)', () => {
    setupDom([]);
    expect(scrollToMessageTimestampWindow(document, 1, 2)).toBe(false);
  });

  it('无时间窗 → false 不查询 DOM', () => {
    expect(scrollToMessageTimestampWindow(document, null, null)).toBe(false);
  });
});