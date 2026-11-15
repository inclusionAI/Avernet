import { formatAbsoluteTime, formatRelativeTime } from './format';

/** 通知列表：当天显示相对时间，一周内显示天数，更早显示本地完整日期时间。 */
export function formatNotificationTime(value?: string): string {
  if (!value) return '';
  const date = new Date(value);
  const timestamp = date.getTime();
  if (Number.isNaN(timestamp)) return value;
  const days = Math.floor((Date.now() - timestamp) / 86400000);
  if (days < 1) return formatRelativeTime(value);
  if (days < 7) return `${days} 天前`;
  return `${formatAbsoluteTime(value)}:${String(date.getSeconds()).padStart(2, '0')}`;
}
