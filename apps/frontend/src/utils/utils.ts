import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

/**
 * 将字符串编码为 URL-safe Base64
 * 用于 sessionId 等包含特殊字符的参数在 URL 中传递
 *
 * @param str 要编码的字符串
 * @returns URL-safe Base64 编码后的字符串
 */
export function encodeToUrlSafeBase64(str: string): string {
  // 先进行标准 Base64 编码
  const base64 = btoa(unescape(encodeURIComponent(str)));
  // 替换 URL 不安全的字符
  return base64
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, ''); // 移除末尾的填充字符
}

/**
 * 从 URL-safe Base64 解码字符串
 *
 * @param encoded URL-safe Base64 编码的字符串
 * @returns 解码后的原始字符串
 */
export function decodeFromUrlSafeBase64(encoded: string): string {
  // 恢复标准 Base64 格式
  let base64 = encoded.replace(/-/g, '+').replace(/_/g, '/');
  // 添加填充字符
  const pad = base64.length % 4;
  if (pad) {
    base64 += '='.repeat(4 - pad);
  }
  // 解码
  return decodeURIComponent(escape(atob(base64)));
}