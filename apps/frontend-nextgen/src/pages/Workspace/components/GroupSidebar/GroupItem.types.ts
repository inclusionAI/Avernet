import type { GroupView, IdentityView, MessageViewScope, SessionView } from '@/domain/collaboration/types';
import type { DomainResult } from '@/services/workspace/identityService';

export type SessionTab = 'all' | 'favorite';

export interface GroupItemProps {
  group: GroupView;
  expanded: boolean;
  sessions: SessionView[] | undefined;
  sessionTab: SessionTab;
  onSessionTabChange: (t: SessionTab) => void;
  favoriteSessionIds: string[];
  selectedGroupId: string | null;
  selectedSessionId: string | null;
  onSelectGroup: (groupId: string) => void;
  onToggleGroupExpanded: (groupId: string) => void;
  onSelectSession: (groupId: string, sessionId: string) => void;
  onToggleFavorite: (sessionId: string) => void;
  onCreateSession: (groupId: string, scope?: MessageViewScope) => void;
  /** 当前登录身份类型：human 点「+」弹视角菜单，bot 直接创建会话。 */
  viewerKind: 'user' | 'bot';
  onManageGroup: (groupId: string) => void;
  onManageSession: (groupId: string, sessionId: string) => void;
  /** 会话列表菜单：重命名会话（能力对齐管理面板，按 sessionId 参数化）。 */
  onRenameSession?: (sessionId: string, title: string) => Promise<boolean>;
  /** 会话列表菜单：删除会话。 */
  onDeleteSession?: (sessionId: string) => Promise<boolean>;
  /** 会话列表菜单：生成会话邀请链接。 */
  onShareSession?: (sessionId: string) => Promise<DomainResult<{ invitationUrl: string }>>;
  /** 当前身份（会话编辑/删除权限判断：创建者或 driver/manager）。 */
  activeIdentity?: IdentityView | null;
  onShareGroup: (groupId: string) => Promise<DomainResult<{ invitationUrl: string }>>;
  onDissolveGroup: (groupId: string) => void;
  totalSessionCount?: number;
  hasMoreSessions: boolean;
  isLoadingMoreSessions: boolean;
  onLoadMoreSessions: () => Promise<void>;
  error?: string;
  loadMoreError?: string;
  onRetrySessions?: () => Promise<void>;
}

export const KIND_LABEL: Record<GroupView['kind'], string> = {
  free_chat: '自由聊天',
  task_master_slave: '任务协作',
  task_dag: '自定义协同',
};

export const MEMBERSHIP_LABEL: Record<NonNullable<GroupView['membership']>, string> = {
  direct: '固定群成员',
  session_only: '仅参与临时会话',
};

/** 侧栏条目元信息标签统一样式（群 tab / bot tab 共用）。 */
export const SIDEBAR_TAG_CLASS = 'shrink-0 rounded px-1.5 py-0 text-[10px] leading-4';
