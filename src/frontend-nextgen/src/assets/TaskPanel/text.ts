// @asset-migrated: teamclaw 自研资产
/** 任务副屏文本展示工具。 */

/** 按 Unicode 字符截断文本，省略号计入最大展示长度。 */
export function truncateText(value: string, maxLength: number): string {
  if (maxLength <= 0) return '';
  const characters = Array.from(value);
  if (characters.length <= maxLength) return value;
  return `${characters.slice(0, Math.max(0, maxLength - 1)).join('')}…`;
}
