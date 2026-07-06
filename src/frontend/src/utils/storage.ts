/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * localStorage 统一工具
 * 所有 key 统一前缀 `oc_`，按模块命名：oc_{module}_{key}
 */

const PREFIX = 'oc_';

/**
 * 生成带前缀的 key
 */
export function storageKey(module: string, key: string): string {
  return `${PREFIX}${module}_${key}`;
}

/**
 * 预定义的 Storage Key 常量
 * 新增 localStorage key 时在此统一注册
 */
export const STORAGE_KEYS = {
  // Bot 模块
  BOT_PREFERENCE: storageKey('bot', 'preference'),

  // Auth 模块
  AUTH_DEV_USER_INFO: storageKey('auth', 'dev_user_info'),

  // Skill 模块
  SKILL_ACTIVE_SET_ID: storageKey('skill', 'active_set_id'),

  // Market 模块（动态 key）
  skillBasicInfo: (skillId: string) => storageKey('market', `skill_basic_info_${skillId}`),

  // Onboarding 模块
  onboardingCompleted: (userId: string) => storageKey('onboarding', `completed_${userId}`),
} as const;
