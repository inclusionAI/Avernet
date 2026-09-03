/** @jest-environment node */
// safeReportError 守卫式提示:alreadyHandled 跳过(防重复);有 toastKey 经去重入口发起;否则直接发起。
import { jest } from '@jest/globals';

jest.mock('@/components/ui/notify');

import { notifyError } from '@/components/ui/notify';
import { safeReportError } from '@/utils/errorToast';
import { beforeEach, describe, expect, it } from '@jest/globals';

const mockedNotifyError = notifyError as jest.MockedFunction<typeof notifyError>;

beforeEach(() => {
  mockedNotifyError.mockClear();
});

describe('safeReportError', () => {
  it('alreadyHandled 为真 → 跳过,不发起 notifyError(协议层已默认提示,防重复)', () => {
    safeReportError({ alreadyHandled: true, toastKey: 'k', message: 'm' });
    expect(mockedNotifyError).not.toHaveBeenCalled();
  });

  it('有 toastKey → 经带 id 的 notifyError 发起(受冷静窗/sonner 去重保护)', () => {
    safeReportError({ message: '创建失败', toastKey: 'k' });
    expect(mockedNotifyError).toHaveBeenCalledTimes(1);
    expect(mockedNotifyError).toHaveBeenCalledWith('创建失败', { id: 'k', title: undefined });
  });

  it('提供 title → 透传给 notifyError 双行展示', () => {
    safeReportError({ message: '后端 message', toastKey: 'k' }, { title: '创建团队失败' });
    expect(mockedNotifyError).toHaveBeenCalledWith('后端 message', { id: 'k', title: '创建团队失败' });
  });

  it('无 toastKey → 直接 notifyError(message) 不带 id(向后兼容纯前端抛错)', () => {
    safeReportError({ message: '前端校验失败' });
    expect(mockedNotifyError).toHaveBeenCalledTimes(1);
    expect(mockedNotifyError).toHaveBeenCalledWith('前端校验失败', { title: undefined });
  });

  it('Error 实例 → 读其 message', () => {
    safeReportError(new Error('boom'));
    expect(mockedNotifyError).toHaveBeenCalledWith('boom', { title: undefined });
  });

  it('无可读 message → 回退通用文案', () => {
    safeReportError({ message: undefined });
    expect(mockedNotifyError).toHaveBeenCalledWith('操作失败，请重试', { title: undefined });
  });

  it('null/undefined 错误 → 回退通用文案、不抛错', () => {
    safeReportError(null);
    safeReportError(undefined);
    expect(mockedNotifyError).toHaveBeenCalledTimes(2);
    expect(mockedNotifyError).toHaveBeenCalledWith('操作失败，请重试', { title: undefined });
  });
});
