/** @jest-environment jsdom */
import type { OpenSourceExperienceNoticeSpec } from '@/capabilities';
import { act, renderHook } from '@testing-library/react';

let mockNotice: OpenSourceExperienceNoticeSpec | null = null;
const mockRead = jest.fn<string | null, []>();
const mockWrite = jest.fn<boolean, [string]>();

jest.mock('@/capabilities', () => ({
  getCapabilities: () => ({
    getOpenSourceExperienceNotice: () => ({ status: 'available', value: mockNotice }),
  }),
}));
jest.mock('@/services/experienceNotice/experienceNoticeStorage', () => ({
  readAcknowledgedExperienceNoticeVersion: () => mockRead(),
  writeAcknowledgedExperienceNoticeVersion: (version: string) => mockWrite(version),
}));

const { useOpenSourceExperienceNotice } =
  require('@/hooks/useOpenSourceExperienceNotice') as typeof import('@/hooks/useOpenSourceExperienceNotice');

const NOTICE: OpenSourceExperienceNoticeSpec = {
  version: 'open-source-experience-v1',
  message: '本环境仅供开源版本进行功能体验，不提供正式生产服务。请不要上传敏感数据。',
  acknowledgeLabel: '我已知悉',
};

describe('useOpenSourceExperienceNotice', () => {
  beforeEach(() => {
    mockNotice = NOTICE;
    mockRead.mockReset().mockReturnValue(null);
    mockWrite.mockReset().mockReturnValue(true);
  });

  it('当前版本未知悉时展示；同版本已知悉时隐藏', () => {
    const first = renderHook(() => useOpenSourceExperienceNotice());
    expect(first.result.current.notice).toEqual(NOTICE);
    expect(first.result.current.visible).toBe(true);
    first.unmount();

    mockRead.mockReturnValue(NOTICE.version);
    const second = renderHook(() => useOpenSourceExperienceNotice());
    expect(second.result.current.visible).toBe(false);
  });

  it('旧版本记录不隐藏新版本提示', () => {
    mockRead.mockReturnValue('open-source-experience-v0');
    const { result } = renderHook(() => useOpenSourceExperienceNotice());
    expect(result.current.visible).toBe(true);
  });

  it('确认后立即关闭；写入失败也不阻断当前页面关闭', () => {
    mockWrite.mockReturnValue(false);
    const { result } = renderHook(() => useOpenSourceExperienceNotice());

    act(() => result.current.acknowledge());

    expect(result.current.visible).toBe(false);
    expect(mockWrite).toHaveBeenCalledWith(NOTICE.version);
  });

  it('Internal capability 为 null 时不展示，也不访问 storage', () => {
    mockNotice = null;
    const { result } = renderHook(() => useOpenSourceExperienceNotice());

    expect(result.current.notice).toBeNull();
    expect(result.current.visible).toBe(false);
    expect(mockRead).not.toHaveBeenCalled();
    act(() => result.current.acknowledge());
    expect(mockWrite).not.toHaveBeenCalled();
  });
});
