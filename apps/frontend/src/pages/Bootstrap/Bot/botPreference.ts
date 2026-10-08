/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bot Preference - Bot 用户偏好存储（localStorage）
 */

import { STORAGE_KEYS } from '@/utils/storage';

const STORAGE_KEY = STORAGE_KEYS.BOT_PREFERENCE;

interface BotPreference {
  userId: string;
  botId: string;
  timestamp: number;
}

/**
 * 保存用户偏好的 Bot
 */
export function savePreferredBot(userId: string, botId: string): void {
  try {
    const preference: BotPreference = {
      userId,
      botId,
      timestamp: Date.now(),
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(preference));
  } catch {
    // 保存失败时静默
  }
}

/**
 * 清除用户偏好
 */
export function clearPreferredBot(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // 清除失败时静默
  }
}

/**
 * 获取用户偏好的 Bot ID
 */
export function getPreferredBotId(userId: string): string | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (!stored) return null;

    const preference: BotPreference = JSON.parse(stored);

    // 验证是否是同一个用户
    if (preference.userId !== userId) {
      clearPreferredBot();
      return null;
    }

    // 验证偏好是否过期（7天）
    const MAX_AGE = 7 * 24 * 60 * 60 * 1000; // 7天
    if (Date.now() - preference.timestamp > MAX_AGE) {
      clearPreferredBot();
      return null;
    }

    return preference.botId;
  } catch {
    return null;
  }
}
