export type EngineType = 'OpenClaw' | 'ClaudeCode' | 'Hermes';

export type IdentityStatus = 'online' | 'hidden';
export type IdentityReachability = 'reachable' | 'unreachable';
export interface Identity {
  id: string;
  name: string;
  kind: 'user' | 'bot';
  avatar: string;
  status?: 'available' | 'unavailable';
  /** Bot 实例运行状态：online→在线，hidden→不在线。 */
  chatStatus?: IdentityStatus;
  /** Bot 所使用的引擎类型；后端未返回时保持缺省。 */
  engine?: string;
  /** Bot 类型原始枚举值：personal / service / desktop。 */
  botType?: string;
  /** Bot 群聊链路可达性；与运行状态分开表达。 */
  reachability?: IdentityReachability;
}

/**
 * 会话形态。本期仅用于副屏验证矩阵(单聊/群聊)的上下文标识。
 *
 * 注意：会话管理(列表/创建/切换/Session 生命周期/WebSocket)由另一同学负责，
 * 本期不实现；`kind`/`groupId`/`participants` 仅承载副屏验证所需的最小目标信息，
 * 会话层就绪后由真实 Provider/Session 替换。
 */
export type ConversationKind = 'single' | 'group';

/**
 * 群聊参与者元信息(副屏验证矩阵用；真实会话层由 GroupParticipant 域模型承载)。
 */
export interface GroupParticipant {
  id: string;
  name: string;
  avatar?: string;
  type: 'user' | 'bot';
  /** bot 参与者的引擎 Bot UUID(协作群 BCS 触发副屏时定位 bot)。 */
  botUuid?: string;
  role?: string;
}

export interface ConversationTarget {
  id: string;
  name: string;
  avatar: string;
  engine: EngineType;
  /** 分组(测试用户/客服场景可不传;留作未来好友 Bot 扩展)。 */
  group?: 'mine' | 'friend';
  status: 'available' | 'unavailable';
  summary: string;
  demoMode?: 'teamclaw-support';
  /** 会话形态标识：单聊('single')或协作群聊('group')。默认按是否存在 groupId 推断。 */
  kind?: ConversationKind;
  /** 协作群聊 ID（仅 kind='group' 时有意义）。 */
  groupId?: string;
  /** 协作群聊参与者列表（仅 kind='group' 时有意义）。 */
  participants?: GroupParticipant[];
}

export interface SupportChatState {
  phase: 'idle' | 'preparing' | 'loading-history' | 'ready' | 'error';
  error: string | null;
}
