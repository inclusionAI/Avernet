/**
 * 引擎 → 徽章语义色（presentation helper，对话侧 Bot 行徽章使用；
 * Bot 管理「标签」列已统一中性底色，不再消费本映射）：
 * OpenClaw 蓝、TEClaw 绿、Hermes 黄、ClaudeCode 紫（claude_code/claude-code 两种拼写）；
 * 未知/缺省引擎一律中性兜底。
 */
import type { ComponentProps } from 'react';
import type { Badge } from '@/components/ui/Badge';

/** 徽章 tone 取值直接从 Badge 组件派生，不在本文件再造 UI 色表。 */
type BadgeTone = NonNullable<ComponentProps<typeof Badge>['tone']>;

export function getBotEngineBadgeTone(engine?: string): BadgeTone {
  switch (engine?.toLowerCase()) {
    case 'openclaw':
      return 'primary';
    case 'teclaw':
      return 'success';
    case 'hermes':
      return 'warning';
    case 'claude_code':
    case 'claude-code':
      return 'purple';
    default:
      return 'neutral';
  }
}