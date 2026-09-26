/**
 * Bot 头像生成 —— 纯 dicebear Bottts，无第三方/内部 UI 组件依赖。
 *
 * 单独成文件：仅保留生成逻辑，便于在不引入额外组件依赖的前提下被各处复用。
 */
import * as bottts from '@dicebear/bottts';
import { createAvatar } from '@dicebear/core';

export function generateBotAvatar(seed: string): string {
  return createAvatar(bottts, {
    seed,
    size: 80,
    backgroundColor: ['b6e3f4', 'c0aede', 'd1d4f9', 'ffd5dc', 'ffdfbf'],
  }).toDataUri();
}

export function generateBotAvatarVariants(seed: string, batchIndex = 0) {
  return Array.from({ length: 8 }, (_, i) => {
    const s = `${seed}-${batchIndex * 8 + i}`;
    return { seed: s, dataUri: generateBotAvatar(s) };
  });
}
