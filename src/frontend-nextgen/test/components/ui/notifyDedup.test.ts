/** @jest-environment node */
// 去重行为单测:notifyError 在提供稳定 id 时开启 3s 冷静窗 + sonner 稳定 id 物理合并;无 id 保持既有行为。
import { jest } from '@jest/globals';

jest.mock('sonner', () => ({
  __esModule: true,
  toast: {
    error: jest.fn(),
    success: jest.fn(),
    warning: jest.fn(),
    info: jest.fn(),
  },
}));

import { notifyError } from '@/components/ui/notify';
import { afterEach, beforeEach, describe, expect, it } from '@jest/globals';
import { toast } from 'sonner';

const mockedError = toast.error as unknown as jest.Mock;

describe('notifyError 去重(global-error-notify-dedup)', () => {
  beforeEach(() => {
    mockedError.mockClear();
    jest.useFakeTimers();
  });

  afterEach(() => {
    // afterEach 仅在本文件清理定时器,避免泄漏到其它套件。
    jest.useRealTimers();
  });

  it('同 id 在冷静窗内重复发起被抑制(toast.error 仅一次)', () => {
    notifyError('创建失败', { id: 'k-dup' });
    notifyError('创建失败', { id: 'k-dup' });
    notifyError('创建失败', { id: 'k-dup' });

    expect(mockedError).toHaveBeenCalledTimes(1);
  });

  it('冷静窗过期后允许再次发起', () => {
    notifyError('创建失败', { id: 'k-exp' });
    expect(mockedError).toHaveBeenCalledTimes(1);

    jest.advanceTimersByTime(3001);

    notifyError('创建失败', { id: 'k-exp' });
    expect(mockedError).toHaveBeenCalledTimes(2);
  });

  it('不同 id 互不抑制', () => {
    notifyError('创建失败', { id: 'k-a' });
    notifyError('编辑失败', { id: 'k-b' });

    expect(mockedError).toHaveBeenCalledTimes(2);
  });

  it('提供 id 时把 stable id 透传给 sonner(物理合并兜底)', () => {
    notifyError('创建失败', { id: 'k-id' });
    expect(mockedError).toHaveBeenCalledWith('创建失败', { duration: 6000, id: 'k-id' });
  });

  it('title + id 同时提供时双行展示并带 id', () => {
    notifyError('后端 message', { id: 'k-title', title: '创建团队失败' });
    expect(mockedError).toHaveBeenCalledWith('创建团队失败', {
      description: '后端 message',
      duration: 6000,
      id: 'k-title',
    });
  });

  it('不提供 id 时不参与去重且不透传 id(向后兼容)', () => {
    notifyError('创建失败');
    notifyError('创建失败');

    expect(mockedError).toHaveBeenCalledTimes(2);
    expect(mockedError).toHaveBeenLastCalledWith('创建失败', { duration: 6000 });
  });

  it('notifyError.cancel(id) 使随后该 id 的 notifyError 被抑制(静默)', () => {
    notifyError.cancel('k-cancel');
    notifyError('创建失败', { id: 'k-cancel' });

    expect(mockedError).not.toHaveBeenCalled();
  });
});
