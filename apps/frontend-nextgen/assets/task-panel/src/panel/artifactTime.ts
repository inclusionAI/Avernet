// @asset-migrated: teamclaw 自研资产
/**
 * 产物时间统一按北京时间展示。
 * Descriptor 契约给 epoch -> ISO(UTC)；legacy 契约给无时区后缀的数据库时间串。
 * 因此无时区后缀时显式按 Asia/Shanghai 解释，有时间后缀时按真实时间点转换。
 */
const BEIJING_FORMATTER = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

function parseArtifactDate(value: string): Date | null {
  const raw = value.trim();
  if (!raw) return null;
  const hasExplicitZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw);
  const date = hasExplicitZone
    ? new Date(raw)
    : new Date(`${raw.includes(' ') ? raw.replace(' ', 'T') : raw}+08:00`);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatArtifactUpdatedAt(value: string): string {
  const date = parseArtifactDate(value);
  if (!date) return '';
  const parts = BEIJING_FORMATTER.formatToParts(date);
  const values = new Map(parts.map((part) => [part.type, part.value]));
  const year = values.get('year');
  const month = values.get('month');
  const day = values.get('day');
  const hour = values.get('hour');
  const minute = values.get('minute');
  if (!year || !month || !day || !hour || !minute) return '';
  return `${year}-${month}-${day} ${hour}:${minute}`;
}
