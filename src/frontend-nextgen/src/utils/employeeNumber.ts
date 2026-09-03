/**
 * 归一化协作权限页面使用的员工工号。
 *
 * 纯数字且不足六位时按员工工号补齐前导 0；其它格式按字符串保留，
 * 避免 Number/parseInt 等数值转换丢失前导 0。
 */
export function normalizeEmployeeNumber(value: string): string {
  const normalized = value.trim();

  if (/^\d{1,5}$/.test(normalized)) {
    return normalized.padStart(6, '0');
  }

  return normalized;
}
