import type { MessageViewScope } from './types';

export interface MessageViewScopeOption {
  value: MessageViewScope;
  label: string;
  description: string;
}

/** 各入口控件固定默认值（不做本地记忆）。 */
export const DEFAULT_MESSAGE_VIEW_SCOPE: MessageViewScope = 'full';

/** 两选项控件（下拉菜单 / radio）共用文案。 */
export const MESSAGE_VIEW_SCOPE_OPTIONS: MessageViewScopeOption[] = [
  { value: 'full', label: '完整视角', description: '显示本会话内的全部协作消息' },
  { value: 'participant', label: '参与者视角', description: '仅显示公共及与您相关的消息' },
];

/** 成员卡标签文案（full → 完整视角；participant → 参与者视角）。 */
export const MESSAGE_VIEW_SCOPE_LABEL: Record<MessageViewScope, string> = {
  full: '完整视角',
  participant: '参与者视角',
};

/** 「加入当前会话」checkbox 形态文案（视觉稿：加入当前会话按钮效果.png）。 */
export const PARTICIPANT_ONLY_CHECKBOX = {
  label: '只看公开及与我相关的消息',
  hint: '未勾选时，可看到会话内的全部消息。',
  tooltip: '勾选后将以参与者视角加入：仅接收公共消息、明确发送给你的消息与你自己的消息。',
} as const;
