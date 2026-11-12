/** @jest-environment jsdom */

import { renderHook, act } from '@testing-library/react';
import { useLocalSkillUploadError } from '@/hooks/useLocalSkillUploadError';
import { BackendRequestError } from '@/services/botWorkshop';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';

describe('local Skill upload error handling', () => {
  beforeEach(() => useErrorNotifyStore.getState().reset());
  it('preserves authentication silence', () => {
    const { result } = renderHook(() => useLocalSkillUploadError());
    act(() => result.current.handleError(new BackendRequestError('internal', { status: 500, apiPath: '/skills', silent: true })));
    expect(result.current.message).toBe('');
  });
  it.each([
    [404, '当前 Bot 不存在或无访问权限'],
    [409, '当前无法上传 Skill'],
    [422, '上传文件信息不正确'],
    [500, '上传结果可能未确认'],
    [413, '上传包超过服务允许的大小'],
  ])('explains HTTP %s and cancels the default notification', (status, expected) => {
    useErrorNotifyStore.getState().enqueue({ toastKey: 'upload', message: 'error' });
    const { result } = renderHook(() => useLocalSkillUploadError());
    act(() => result.current.handleError(new BackendRequestError('internal', { status, data: null, apiPath: '/skills', toastKey: 'upload' })));
    expect(result.current.message).toContain(expected);
    expect(useErrorNotifyStore.getState().queue[0].cancelled).toBe(true);
  });
});
