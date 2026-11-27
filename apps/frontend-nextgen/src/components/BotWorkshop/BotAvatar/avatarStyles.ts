import { Avatar, Style } from '@dicebear/core';
import bottts from '@dicebear/styles/bottts.json';
import botttsNeutral from '@dicebear/styles/bottts-neutral.json';
import clay from '@dicebear/styles/clay.json';

// figma 稿 node 2017:23671 + 2017:19530:弹窗内「Bot 头像」按风格分组候选——
// 机器人(bottts)/博茨中立(bottts-neutral)/黏士(clay),自定义走上传。前端内置候选集
// (design.md 决策):不依赖后端下发;换一批通过 seed 偏移生成下一组。
export type StyleKey = 'bottts' | 'bottts-neutral' | 'clay';

const styleInstances: Record<StyleKey, Style> = {
  bottts: new Style(bottts),
  'bottts-neutral': new Style(botttsNeutral),
  clay: new Style(clay),
};
export const BOT_AVATAR_STYLE_OPTIONS: { key: StyleKey; label: string }[] = [
  { key: 'bottts', label: '机器人' },
  { key: 'bottts-neutral', label: '博茨中立' },
  { key: 'clay', label: '黏士' },
];

/** 每风格一批候选的数量;稿(node 2074:9156)候选行 = 10 个 48px 候选 + 行尾 48px「换一批」槽,不换行。 */
export const CANDIDATES_PER_BATCH = 10;

const CANDIDATE_BACKGROUND_COLORS = ['b6e3f4', 'c0aede', 'd1d4f9', 'ffd5dc', 'ffdfbf'];

export function generateStyleCandidates(style: StyleKey, seed: string, batch: number): string[] {
  const instance = styleInstances[style];
  return Array.from({ length: CANDIDATES_PER_BATCH }, (_, index) =>
    // 96px 生成、48px 展示,保证 2x 屏锐利;seed 与批次偏移与旧 generateBotAvatar 口径一致。
    new Avatar(instance, {
      seed: `${seed || 'bot'}-${batch * CANDIDATES_PER_BATCH + index}`,
      size: 96,
      backgroundColor: CANDIDATE_BACKGROUND_COLORS,
    }).toDataUri(),
  );
}
