import type { IdentityView } from '@/domain/collaboration';
import { DEFAULT_MESSAGE_VIEW_SCOPE, MESSAGE_VIEW_SCOPE_OPTIONS } from '@/domain/collaboration/messageViewScope';
import type { MessageViewScope } from '@/domain/collaboration/types';
import { useCallback, useEffect, useRef, useState } from 'react';
import { GroupLeaderSelect } from './GroupLeaderSelect';

export interface CreateGroupViewScopeProps {
  activeIdentity?: IdentityView | null;
  value: MessageViewScope;
  onChange: (scope: MessageViewScope) => void;
}

/**
 * 建群面板的「消息视角」下拉选择 —— 仅 human（kind==='user'）身份展示。
 * bot 身份建群不显示；后端默认会话视角仅对 human 建群链路生效。
 */
export function CreateGroupViewScope({ activeIdentity, value, onChange }: CreateGroupViewScopeProps) {
  if (activeIdentity?.kind !== 'user') return null;
  return (
    <GroupLeaderSelect
      id="create-group-view-scope"
      label="消息视角"
      value={value}
      options={MESSAGE_VIEW_SCOPE_OPTIONS.map((option) => ({
        id: option.value,
        name: option.label,
        description: option.description,
      }))}
      onChange={(scope) => onChange(scope as MessageViewScope)}
    />
  );
}

export interface UseCreateGroupViewScopeResult {
  /** 当前选择的默认会话消息视角（human 建群时挂到 participants 的 human 条目）。 */
  viewScope: MessageViewScope;
  /** radio 变更入口：清空 Modal inline 错误后更新视角。 */
  changeViewScope: (scope: MessageViewScope) => void;
  /** 提交组装：为 human（currentHuman）参与者条目附加 message_view_scope，其余条目原样。 */
  applyViewScope: (
    participantIds: string[],
    currentHuman: string | null,
  ) => Array<{ actor_id: string; message_view_scope?: MessageViewScope }>;
}

/**
 * 建群面板视角选择的状态管理：弹窗打开时重置为默认值（full，与其他入口一致，不做本地记忆）。
 * 从 CreateGroupModal 抽出以遵守 Component ≤300 行的文件体积门禁。
 */
export function useCreateGroupViewScope(open: boolean, clearError: () => void): UseCreateGroupViewScopeResult {
  const [viewScope, setViewScope] = useState<MessageViewScope>(DEFAULT_MESSAGE_VIEW_SCOPE);
  const wasOpenRef = useRef(false);

  useEffect(() => {
    const opening = open && !wasOpenRef.current;
    wasOpenRef.current = open;
    if (opening) setViewScope(DEFAULT_MESSAGE_VIEW_SCOPE);
  }, [open]);

  const changeViewScope = useCallback(
    (scope: MessageViewScope) => {
      clearError();
      setViewScope(scope);
    },
    [clearError],
  );

  const applyViewScope = useCallback(
    (participantIds: string[], currentHuman: string | null) =>
      participantIds.map((id) =>
        id === currentHuman ? { actor_id: id, message_view_scope: viewScope } : { actor_id: id },
      ),
    [viewScope],
  );

  return { viewScope, changeViewScope, applyViewScope };
}
