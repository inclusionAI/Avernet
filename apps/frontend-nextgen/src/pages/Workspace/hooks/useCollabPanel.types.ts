import type { ParticipantView } from '@/domain/collaboration';
import type { MessageViewScope } from '@/domain/collaboration/types';

export interface CollabPanelState {
  identityLocked?: boolean;
  /** 是否展示底部协作面板：bot 视角恒显；human 视角仅在 human 姿态为 absent 时显示加入条。 */
  visible: boolean;
  /** human 视角 absent 时直接渲染「未加入当前会话」条（无 tab）。 */
  humanAbsentOnly: boolean;
  /** 当前浏览身份（bot 发言控制对象）。 */
  botActorId: string | null;
  botMode: 'auto' | 'muted' | null;
  botName: string;
  /** 会话内 human 成员（当前查看用户身份对应的协作者）。 */
  human: ParticipantView | null;
  humanJoined: boolean;
  humanName: string;
  humanAvatarUrl?: string;
  /** 是否存在可切换的 human 身份（「去发言」按钮可用性）。 */
  canSwitchToHuman: boolean;
  switchingBotMode: boolean;
  joining: boolean;
  /** human 成员的消息可见域回显（后端未返回时为 null，UI 不渲染切换控件）。 */
  humanViewScope: MessageViewScope | null;
  /** 消息视角切换请求进行中（禁用 Switch）。 */
  switchingViewScope: boolean;
  setBotMode: (mode: 'auto' | 'muted') => Promise<void>;
  joinSession: (scope?: MessageViewScope) => Promise<boolean>;
  /** 退出当前会话（将 human mode 置为 absent）。 */
  leaveSession: () => Promise<boolean>;
  /** 切换到用户视角继续发言（对齐 open-claw「去发言」）。 */
  switchToHuman: () => void;
  /** 仅切换 human 成员消息可见域；成功后触发 ws 整体重连（重拉一次性 token）。 */
  setViewScope: (scope: MessageViewScope) => Promise<boolean>;
}
