import {
  EXPERIENCE_NOTICE_STORAGE_KEY,
  readAcknowledgedExperienceNoticeVersion,
  writeAcknowledgedExperienceNoticeVersion,
} from '@/services/experienceNotice/experienceNoticeStorage';

describe('experienceNoticeStorage', () => {
  it('未记录时返回 null，同版本写入后可读取', () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: jest.fn((key: string) => values.get(key) ?? null),
      setItem: jest.fn((key: string, value: string) => values.set(key, value)),
    };

    expect(readAcknowledgedExperienceNoticeVersion(storage)).toBeNull();
    expect(writeAcknowledgedExperienceNoticeVersion('open-source-experience-v1', storage)).toBe(true);
    expect(storage.setItem).toHaveBeenCalledWith(EXPERIENCE_NOTICE_STORAGE_KEY, 'open-source-experience-v1');
    expect(readAcknowledgedExperienceNoticeVersion(storage)).toBe('open-source-experience-v1');
  });

  it('读取或写入异常时安全降级，不向调用方抛错', () => {
    const readFailure = {
      getItem: jest.fn(() => {
        throw new DOMException('blocked', 'SecurityError');
      }),
      setItem: jest.fn(),
    };
    const writeFailure = {
      getItem: jest.fn(() => null),
      setItem: jest.fn(() => {
        throw new DOMException('full', 'QuotaExceededError');
      }),
    };

    expect(readAcknowledgedExperienceNoticeVersion(readFailure)).toBeNull();
    expect(writeAcknowledgedExperienceNoticeVersion('open-source-experience-v1', writeFailure)).toBe(false);
  });

  it('无 window/storage 时安全降级', () => {
    expect(readAcknowledgedExperienceNoticeVersion(null)).toBeNull();
    expect(writeAcknowledgedExperienceNoticeVersion('open-source-experience-v1', null)).toBe(false);
  });
});
