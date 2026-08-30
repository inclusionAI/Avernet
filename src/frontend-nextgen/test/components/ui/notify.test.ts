/** @jest-environment node */
import { beforeEach, describe, expect, it, jest } from '@jest/globals';

jest.mock('sonner', () => ({
  __esModule: true,
  toast: {
    error: jest.fn(),
    success: jest.fn(),
    warning: jest.fn(),
    info: jest.fn(),
  },
}));

import { toast } from 'sonner';
import { notifyError, notifySuccess } from '@/components/ui/notify';

const mockedError = toast.error as unknown as jest.Mock;
const mockedSuccess = toast.success as unknown as jest.Mock;

describe('notify helper', () => {
  beforeEach(() => {
    mockedError.mockClear();
    mockedSuccess.mockClear();
  });

  it('notifyError 单行:以 message 为 toast.error 第一参,duration 6000', () => {
    notifyError('Skill Center team creation failed');
    expect(mockedError).toHaveBeenCalledTimes(1);
    expect(mockedError).toHaveBeenCalledWith('Skill Center team creation failed', { duration: 6000 });
  });

  it('notifyError 结构化:title + description(message + trace),duration 6000', () => {
    notifyError('Skill Center team creation failed', { title: '创建团队失败', requestId: 'rid-502' });
    expect(mockedError).toHaveBeenCalledWith('创建团队失败', {
      description: 'Skill Center team creation failed\ntrace: rid-502',
      duration: 6000,
    });
  });

  it('notifyError 仅 title 无 requestId:description 仅 message', () => {
    notifyError('boom', { title: '操作失败' });
    expect(mockedError).toHaveBeenCalledWith('操作失败', { description: 'boom', duration: 6000 });
  });

  it('notifySuccess:duration 4000', () => {
    notifySuccess('空间创建成功');
    expect(mockedSuccess).toHaveBeenCalledTimes(1);
    expect(mockedSuccess).toHaveBeenCalledWith('空间创建成功', { duration: 4000 });
  });
});
