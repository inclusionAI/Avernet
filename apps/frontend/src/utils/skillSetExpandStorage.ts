/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * 技能集展开状态本地存储管理
 */

const STORAGE_KEY = 'oc-skillset-expand';

interface BotSkillSetExpand {
  bot_id: string;
  expand_skillset_list: string[];
}

/**
 * 获取所有 Bot 的技能集展开状态
 */
export function getAllSkillSetExpands(): BotSkillSetExpand[] {
  try {
    const data = localStorage.getItem(STORAGE_KEY);
    if (data) {
      const parsed = JSON.parse(data);
      if (Array.isArray(parsed)) {
        return parsed;
      }
    }
  } catch {
    // ignore parse error
  }
  return [];
}

/**
 * 获取指定 Bot 的技能集展开状态
 * @returns { exists: boolean, list: string[] } - exists 表示该 bot 是否有记录，list 为展开列表
 */
export function getSkillSetExpands(
  botId: string,
): {
  exists: boolean;
  list: string[];
} {
  const allExpands = getAllSkillSetExpands();
  const botExpand = allExpands.find((item) => item.bot_id === botId);
  if (!botExpand) {
    return { exists: false, list: [] };
  }
  return { exists: true, list: botExpand.expand_skillset_list };
}

/**
 * 设置指定 Bot 的技能集展开状态
 */
export function setSkillSetExpands(botId: string, skillSetIds: string[]): void {
  const allExpands = getAllSkillSetExpands();
  const existingIndex = allExpands.findIndex((item) => item.bot_id === botId);

  if (existingIndex >= 0) {
    // 更新已有记录
    allExpands[existingIndex].expand_skillset_list = skillSetIds;
  } else {
    // 添加新记录
    allExpands.push({
      bot_id: botId,
      expand_skillset_list: skillSetIds,
    });
  }

  localStorage.setItem(STORAGE_KEY, JSON.stringify(allExpands));
}

/**
 * 删除指定 Bot 的技能集展开状态
 */
export function removeSkillSetExpands(botId: string): void {
  const allExpands = getAllSkillSetExpands();
  const filtered = allExpands.filter((item) => item.bot_id !== botId);
  localStorage.setItem(STORAGE_KEY, JSON.stringify(filtered));
}

/**
 * 清理已不存在的技能集 ID
 */
export function cleanupSkillSetExpands(
  botId: string,
  validSkillSetIds: string[],
): void {
  const { exists, list } = getSkillSetExpands(botId);
  if (!exists) return;

  const filtered = list.filter((id) => validSkillSetIds.includes(id));
  if (filtered.length !== list.length) {
    setSkillSetExpands(botId, filtered);
  }
}
