import type { GroupKind, IdentityView } from '@/domain/collaboration';
import type { WorkspaceView } from '@/domain/collaboration/availableViews';

export type { WorkspaceView } from '@/domain/collaboration/availableViews';
export type GroupKindFilter = 'all' | GroupKind;
export type GroupSortMode = 'lastActivity' | 'createdAt';
export type SessionTab = 'all' | 'favorite';
export type ConnectionState = 'connected' | 'reconnecting' | 'disconnected';
export type ActivePanel = 'none' | 'members' | 'manage' | 'resources' | 'history' | 'pin';
export type GroupMembership = 'direct' | 'session_only';

export interface IdentityMemo {
  view?: WorkspaceView;
  groupId?: string | null;
  groupSessionId?: string | null;
  expandedGroupId?: string | null;
  membership?: GroupMembership;
  botId?: string | null;
  botSessionId?: string | null;
  expandedBotId?: string | null;
}

export interface WorkspaceState {
  activeIdentityId: string | null;
  activeTargetId: string | null;
  search: string;
  view: WorkspaceView;
  collapsedGroups: string[];
  setActiveIdentityId: (id: string) => void;
  setActiveTargetId: (id: string | null) => void;
  setSearch: (search: string) => void;
  setView: (view: WorkspaceView) => void;
  toggleGroup: (group: string) => void;
  identities: IdentityView[];
  selectedGroupId: string | null;
  selectedSessionId: string | null;
  groupSearchText: string;
  groupKindFilter: GroupKindFilter;
  groupSortMode: GroupSortMode;
  membership: GroupMembership;
  expandedGroupIds: Record<string, true>;
  bcsGroupIds: Record<string, true>;
  expandedBotIds: Record<string, true>;
  /** 记录每个 botId 是被哪个 section（mine / friend）展开的，用于区分同名 bot 的展开归属。 */
  expandedBotSectionKey: Record<string, string>;
  selectedBotSessionId: string | null;
  /** 点击会话时递增的计数器;chat hooks 监听变化以强制重新拉取历史消息。 */
  historyRefreshNonce: number;
  lastSessionByIdentity: Record<string, IdentityMemo>;
  sessionTabsByGroup: Record<string, SessionTab>;
  sessionSearchText: string;
  connectionState: ConnectionState;
  activePanel: ActivePanel;
  isGroupsLoading: boolean;
  isSessionsLoading: boolean;
  setIdentities: (items: IdentityView[], activeId: string | null) => void;
  setActiveIdentity: (id: string | null) => void;
  selectGroup: (groupId: string | null) => void;
  markBcsGroup: (id: string) => void;
  selectSession: (sessionId: string | null) => void;
  setGroupSearchText: (v: string) => void;
  setGroupKindFilter: (k: GroupKindFilter) => void;
  setGroupSortMode: (m: GroupSortMode) => void;
  setMembership: (m: GroupMembership) => void;
  toggleGroupExpanded: (groupId: string) => void;
  toggleBotExpanded: (botId: string) => void;
  setBotExpandedSection: (botId: string, sectionKey: string) => void;
  selectBotSession: (sessionId: string | null) => void;
  bumpHistoryRefresh: () => void;
  setSessionTabForGroup: (groupId: string, tab: SessionTab) => void;
  setSessionSearchText: (v: string) => void;
  setConnectionState: (s: ConnectionState) => void;
  setActivePanel: (p: ActivePanel) => void;
  setIsGroupsLoading: (v: boolean) => void;
  setIsSessionsLoading: (v: boolean) => void;
  reset: () => void;
  resetWorkspace: () => void;
}

export const topLevelState = {
  activeIdentityId: null as string | null,
  activeTargetId: 'teamclaw-support' as string | null,
  search: '',
  view: 'chat' as WorkspaceView,
  collapsedGroups: [] as string[],
  historyRefreshNonce: 0,
  lastSessionByIdentity: {} as Record<string, IdentityMemo>,
} satisfies Record<string, unknown>;

export const groupViewState = {
  identities: [] as IdentityView[],
  selectedGroupId: null as string | null,
  selectedSessionId: null as string | null,
  groupSearchText: '',
  groupKindFilter: 'all' as GroupKindFilter,
  groupSortMode: 'lastActivity' as GroupSortMode,
  membership: 'direct' as GroupMembership,
  expandedGroupIds: {} as Record<string, true>,
  bcsGroupIds: {} as Record<string, true>,
  expandedBotIds: {} as Record<string, true>,
  expandedBotSectionKey: {} as Record<string, string>,
  selectedBotSessionId: null as string | null,
  sessionTabsByGroup: {} as Record<string, SessionTab>,
  sessionSearchText: '',
  connectionState: 'disconnected' as ConnectionState,
  activePanel: 'none' as ActivePanel,
  isGroupsLoading: false,
  isSessionsLoading: false,
} satisfies Record<string, unknown>;

export const initialState = { ...topLevelState, ...groupViewState };
export const groupFields = groupViewState;

export {
  rememberLastSession,
  restoreIdentitySelection,
  toggleArrayItem,
  toggleRecordExclusive,
} from './workspaceStoreHelpers';
