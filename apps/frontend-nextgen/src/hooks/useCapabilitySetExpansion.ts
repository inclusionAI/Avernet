import type { BotCapabilitySet } from '@/domain/botEditor';
import { useMemo, useState } from 'react';

type Choices = Record<string, boolean>;
function readChoices(key: string | undefined): Choices {
  if (!key) return {};
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(key) ?? '{}');
    if (!value || typeof value !== 'object' || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter(([, expanded]) => typeof expanded === 'boolean'));
  } catch {
    return {};
  }
}

/** 只记忆显式选择；没有记录的默认能力集收起，其余沿用首项展开规则。 */
export function useCapabilitySetExpansion(sets: BotCapabilitySet[], botId?: string, userId?: string, ownerId?: string) {
  const key =
    botId && userId ? `bot-capability-expansion:v1:${JSON.stringify([userId, ownerId ?? '', botId])}` : undefined;
  const initial = useMemo(() => readChoices(key), [key]);
  const [state, setState] = useState({ key, choices: initial });
  const choices = state.key === key ? state.choices : initial;
  const isExpanded = (set: BotCapabilitySet) =>
    Object.prototype.hasOwnProperty.call(choices, set.id) ? choices[set.id] : !set.isDefault && sets[0]?.id === set.id;
  const toggle = (set: BotCapabilitySet) => {
    const next = { ...choices, [set.id]: !isExpanded(set) };
    setState({ key, choices: next });
    if (key) {
      try {
        window.localStorage.setItem(key, JSON.stringify(next));
      } catch {
        // 浏览器禁用存储时，本次编辑仍正常展开/收起。
      }
    }
  };
  return { isExpanded, toggle };
}
