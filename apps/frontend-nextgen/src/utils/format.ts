// 通用格式化方法。

export function trim(str: string) {
  return str.trim();
}

/**
 * 把 ISO 时间字符串格式化为相对时间(刚刚 / N 分钟前 / N 小时前 / 日期回落)。
 * 供通知、工单行等内联时间展示复用,统一口径。
 * - 入参为空 → 返回空串(调用方按需决定是否渲染)。
 * - 入参无法解析 → 原样返回,避免吞数据。
 */
export function formatRelativeTime(iso?: string): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return iso;
  const diff = Date.now() - t;
  const m = Math.floor(diff / 60000);
  if (m < 1) return '刚刚';
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  return new Date(iso).toLocaleDateString();
}

/**
 * 绝对时间格式化（YYYY-MM-DD HH:mm）。对齐 PRD 工单/消息列表与详情的 createdAt 展示。
 */
export function formatAbsoluteTime(iso?: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const y = d.getFullYear();
  const mo = String(d.getMonth() + 1).padStart(2, '0');
  const da = String(d.getDate()).padStart(2, '0');
  const h = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `${y}-${mo}-${da} ${h}:${mi}`;
}

/**
 * Skill 时间格式化（YYYY-MM-DD HH:mm:ss，含秒）。供详情「发布时间」/ 编辑器「更新时间」
 * （published_at / gmt_modified）展示。与 formatAbsoluteTime 的关键区别：**不做时区转换（不 +8）**，
 * 时区若有偏移是后端接口问题、由后端修；前端只取后端字面日期时间（剥离 T / Z / +offset 后缀）。
 * 入参为空或非预期格式 → 原样返回，避免吞数据。
 */
export function formatAbsoluteTimeWithSeconds(iso?: string): string {
  if (!iso) return '';
  const m = iso.match(/^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})/);
  return m ? `${m[1]} ${m[2]}` : iso;
}

/**
 * 聊天消息时间格式化：当天显示 HH:mm，非当天显示 MM-dd HH:mm。
 * 接受时间戳（number | string）或已格式化的 displayTime 字符串。
 * displayTime 形如 "14:30" 或 "08-19 14:30"，直接透传不重新格式化。
 */
export function formatChatTime(input?: number | string | null): string | undefined {
  if (input === null || input === undefined) return undefined;
  if (typeof input === 'string' && input.includes(':')) {
    return input;
  }
  const date = new Date(input);
  const ts = date.getTime();
  if (Number.isNaN(ts)) return undefined;
  const now = new Date();
  const isSameDay =
    date.getFullYear() === now.getFullYear() && date.getMonth() === now.getMonth() && date.getDate() === now.getDate();
  const hh = String(date.getHours()).padStart(2, '0');
  const mm = String(date.getMinutes()).padStart(2, '0');
  if (isSameDay) return `${hh}:${mm}`;
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${month}-${day} ${hh}:${mm}`;
}
