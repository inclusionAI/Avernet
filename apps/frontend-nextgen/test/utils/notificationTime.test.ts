import { formatNotificationTime } from '@/utils/notificationTime';

describe('notification time', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    jest.setSystemTime(new Date('2026-11-10T12:00:00'));
  });
  afterEach(() => jest.useRealTimers());
  it('formats recent hours', () => expect(formatNotificationTime('2026-11-10T10:00:00')).toBe('2 小时前'));
  it('formats days within a week', () => expect(formatNotificationTime('2026-11-05T12:00:00')).toBe('5 天前'));
  it('keeps full local time for older notifications', () =>
    expect(formatNotificationTime('2026-10-23T16:53:48')).toBe('2026-10-23 16:53:48'));
  it('handles empty and invalid timestamps', () => {
    expect(formatNotificationTime()).toBe('');
    expect(formatNotificationTime('invalid')).toBe('invalid');
  });
});
