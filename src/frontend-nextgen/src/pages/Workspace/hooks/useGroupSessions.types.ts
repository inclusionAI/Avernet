import type { ParticipantMode, SessionView } from '@/domain/collaboration';

export interface UseGroupSessionsResult {
  sessions: SessionView[];
  /** 多群展开展示使用：每个已加载群的会话（已按 search 过滤；收藏过滤在 GroupItem 按群独立处理）。 */
  sessionsByGroupId: Record<string, SessionView[]>;
  hasMoreSessionsByGroupId: Record<string, boolean>;
  isLoadingMoreSessionsByGroupId: Record<string, boolean>;
  totalSessionsByGroupId: Record<string, number>;
  errorByGroupId: Record<string, string>;
  loadMoreErrorByGroupId: Record<string, string>;
  loadMoreSessions: (groupId: string) => Promise<void>;
  isSessionsLoading: boolean;
  selectedSessionId: string | null;
  selectedSession: SessionView | null;
  favoriteSessionIds: string[];
  sessionSearchText: string;
  setSessionSearchText: (v: string) => void;
  selectSession: (sessionId: string | null) => void;
  /** 打开某群下的会话：必要时先切换选中群（chat pane 跟随），再选中会话。 */
  openSession: (groupId: string, sessionId: string) => void;
  createSession: (title?: string, contextQuery?: string) => Promise<SessionView | null>;
  /** 在指定群内创建会话（侧栏每个群的「新建会话」入口）；创建后选中该群与这条新会话。 */
  createSessionIn: (groupId: string, title?: string, contextQuery?: string) => Promise<SessionView | null>;
  renameSession: (sessionId: string, title: string) => Promise<boolean>;
  deleteSession: (sessionId: string) => Promise<boolean>;
  /** 退出会话（移除自己）：从侧边栏移除该会话并清空选中。 */
  leaveSession: (sessionId: string, actorId: string) => Promise<boolean>;
  toggleFavorite: (sessionId: string) => Promise<void>;
  /** 更新会话成员姿态/发言模式（参考 open-claw 我的协作：bot auto↔muted、human present↔absent）。 */
  updateMemberMode: (sessionId: string, actorId: string, mode: ParticipantMode) => Promise<boolean>;
  /** 用后端返回的会话详情就地替换指定会话数据（成员增删后就地刷新）。 */
  applySessionUpdate: (sessionId: string, session: SessionView) => void;
  reloadSessions: () => Promise<void>;
  reloadGroup: (groupId: string) => Promise<void>;
}
