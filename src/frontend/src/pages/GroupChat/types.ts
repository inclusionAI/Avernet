/**
 * 群聊相关类型定义
 * 基于 SDK 设计文档
 */

/**
 * Actor 类型 - Bot 或 Human
 */
export type ActorKind = 'bot' | 'human';

/**
 * 参与者协作姿态/模式
 * - present: Human 参与模式
 * - absent: Human 旁观模式
 * - auto: Bot 自动发言模式
 * - muted: Bot 禁言模式
 */
export type ParticipantMode = 'present' | 'absent' | 'auto' | 'muted';

/**
 * 群聊成员类型
 */
export type GroupMemberType = 'user' | 'bot';

/**
 * 群聊成员角色
 * - driver/consultant: 自由聊天群
 * - manager/worker/observer: 任务协作群（主从模式）
 */
export type GroupMemberRole =
  | 'driver'
  | 'consultant'
  | 'manager'
  | 'worker'
  | 'observer';

/**
 * 群聊成员信息
 */
export interface GroupMember {
  /** 成员ID */
  id: string;
  /** Bot UUID（BCS中的唯一标识） */
  botUuid?: string;
  /** 成员显示名称 */
  name: string;
  /** 成员类型 */
  type: GroupMemberType;
  /** 成员角色（Bot类型时必填） */
  role?: GroupMemberRole;
  /** Actor 类型（新增） */
  actorKind?: ActorKind;
  /** 协作姿态/发言模式（新增） */
  mode?: ParticipantMode;
  /** 头像URL */
  avatar?: string;
  /** 对应OpenClaw的sessionKey */
  sessionKey?: string;
  /** 扩展字段 */
  extra?: Record<string, any>;
}

/**
 * 群聊类型
 * - normal: 普通协作群
 * - dm: Bot↔Bot 1:1 私聊
 */
export type GroupKind = 'normal' | 'dm';

/**
 * 协作群策略
 * - chat: 自由聊天型
 * - manager_worker: 任务协作型-主从模式
 * - state_machine: 结构化协同型
 */
export type GroupStrategy = 'chat' | 'manager_worker' | 'state_machine';

/**
 * 群聊信息
 */
export interface GroupInfo {
  /** 群聊ID */
  id: string;
  /** 群聊主题/名称 */
  topic: string;
  /** 创建者ID */
  creatorId: string;
  /** 协调者Bot UUID */
  coordinatorBot?: string;
  /** 成员列表 */
  participants: GroupMember[];
  /** 群聊类型 */
  groupKind?: GroupKind;
  /** 协作群策略 - chat(自由聊天)、manager_worker(任务协作-主从模式) 或 state_machine(结构化协同) */
  groupStrategy?: GroupStrategy;
  /** 主节点 Bot UUID（仅 manager_worker 群） */
  masterBot?: string;
  /** 创建时间 */
  createdAt: number;
  /** 更新时间 */
  updatedAt?: number;
  /** 扩展字段 */
  extra?: Record<string, any>;
  /** 群可见性 */
  visibility?: 'public' | 'private';
}

/**
 * 群聊消息发送者
 */
export interface GroupMessageSender {
  /** 发送者ID */
  id: string;
  /** 发送者名称 */
  name: string;
  /** 发送者类型 */
  type: GroupMemberType;
  /** 头像URL */
  avatar?: string;
  /** Bot UUID（Bot发送时必填） */
  botUuid?: string;
}

/**
 * 群聊消息
 */
export interface GroupMessage {
  /** 消息ID */
  id: string;
  /** 消息内容 */
  content: string;
  /** 发送者 */
  sender: GroupMessageSender;
  /** 时间戳 */
  timestamp: number;
  /** @提及的Bot UUID列表 */
  mentions?: string[];
  /** 回复的消息ID */
  replyTo?: string;
  /** Bot UUID（Bot发送时） */
  botUuid?: string;
  /** 消息块（结构化内容） */
  blocks?: any[];
  /** 消息状态 */
  status?: 'pending' | 'sent' | 'failed' | 'streaming';
  /** 消息角色（用于展示区分） */
  role?: 'user' | 'assistant' | 'toolResult' | 'system';
  /** 对话轮次ID（用于聚合消息） */
  conversationRoundId?: string;
  /** 是否是工具调用结果消息 */
  isToolResult?: boolean;
  /** 同一轮 bot 回复共享的 run ID，用于消息聚合 fallback */
  runId?: string;
  /** 元数据（工具调用信息等） */
  metadata?: {
    stop_reason?: string;
    tool_calls?: Array<{
      arguments: Record<string, any>;
      tool_call_id: string;
      tool_name: string;
    }>;
    is_error?: boolean;
    result?: string;
    tool_call_id?: string;
    tool_name?: string;
  };
}

/**
 * 分页消息响应
 */
export interface PaginatedMessages {
  messages: GroupMessage[];
  nextCursor?: string;
  hasMore: boolean;
}

/**
 * 路由策略 - 控制 Bot 自动回复行为
 * - inject_observers: 关闭自动回复
 * - send_to_driver: 自动回复
 */
export type RoutingPolicy = 'inject_observers' | 'send_to_driver';

/**
 * 创建群组参数
 */
export interface CreateGroupParams {
  /** 协调者Bot ID */
  driver_bot: string;
  /** 参与者列表 */
  participants: Array<{
    bot_uuid: string;
    bot_name?: string;
    role?: GroupMemberRole;
  }>;
  /** 结构化协同 participant 逻辑角色绑定 */
  participant_bindings?: Record<
    string,
    {
      source: 'manual';
      bot_ids: string[];
    }
  >;
  /** 群组名称 */
  label: string;
  /** 协作目标/描述 */
  context?: string;
  /** 路由策略 - 控制 Bot 自动回复行为 */
  routing_policy?: {
    default_bot_final_delivery: RoutingPolicy;
  };
  /** 群聊类型 */
  group_kind?: GroupKind;
  /** 发起者 Bot（group_kind="dm" 时必填） */
  from_bot?: string;
  /** 发起者 Bot UUID（创建群的 Bot，不一定是 driver_bot） */
  originator?: string;
  /** 协作群策略 - chat(自由聊天)、manager_worker(任务协作-主从模式) 或 state_machine(结构化协同) */
  group_strategy?: GroupStrategy;
  /** 主节点 Bot UUID（manager_worker 群必填） */
  master_bot?: string;
  /** 服务调用时是否自动启动（state_machine 群默认 true） */
  auto_start_on_service_invocation?: boolean;
  /** 结构化协同定义 YAML（state_machine 群必填） */
  collaboration_definition_yaml?: string;
}

/**
 * 群聊视图模式
 */
export type GroupChatViewMode = 'list' | 'detail' | 'create';

// === WebSocket 相关类型 ===

/** WebSocket 请求消息 */
export interface WSRequest {
  type: 'req';
  id: string;
  method: string;
  params: Record<string, any>;
}

/** WebSocket 响应消息 */
export interface WSResponse {
  type: 'res';
  id: string;
  ok: boolean;
  payload?: Record<string, any>;
}

/** WebSocket 事件消息 */
export interface WSEvent {
  type: 'event';
  event: string;
  payload: Record<string, any>;
  group_id?: string;
  bot_uuid?: string;
}

/** chat.send 请求参数 */
export interface ChatSendParams {
  sessionKey: string;
  message: string;
  group_id: string;
  bot_uuid: string;
  /** 会话 ID，用于路由消息到特定会话 */
  session_id?: string;
  thinking?: 'enabled';
  idempotencyKey?: string;
  timeoutMs?: number;
  attachments?: Array<{
    type: string;
    mimeType: string;
    fileName: string;
    content: string;
  }>;
}

/** chat 事件 payload */
export interface ChatEventPayload {
  runId: string;
  sessionKey: string;
  seq: number;
  state: 'delta' | 'final';
  /** Omitted for terminal silent completions. */
  message?: {
    role: 'assistant';
    content: Array<{ type: 'text'; text: string }>;
    timestamp: number;
  };
  usage?: {
    input: number;
    output: number;
  };
  stopReason?: string;
}

/** agent 事件 payload (tool/thinking) */
export interface AgentEventPayload {
  runId: string;
  seq: number;
  stream: 'tool' | 'thinking';
  ts?: number;
  sessionKey?: string;
  payload?: {
    phase?: 'start' | 'result';
    name?: string;
    toolCallId?: string;
    args?: Record<string, any>;
    isError?: boolean;
    result?: any;
    text?: string;
    delta?: string;
  };
}

/** connect 请求参数 */
export interface ConnectParams {
  group_id: string;
  /** 会话 ID，建立会话级 WebSocket 连接时使用 */
  session_id?: string;
}

// === Bot Tab 相关类型 ===

/** Bot 可见性类型 */
export type BotVisibility = 'private' | 'protected' | 'public' | 'offline';

/**
 * Bot Tab 展示数据（外部传入）
 * 用于主应用通过 props 传入 Bot 列表
 */
export interface BotTabItem {
  /** BCN 网络中的 Bot 唯一标识，格式：bot_id:entity_id */
  bot_uuid: string;
  /** Bot 显示名称 */
  bot_name: string;
  /** Bot 头像 URL（可选） */
  avatar_url?: string;
  /** Bot 简介（可选） */
  summary?: string;
}

/**
 * Bot Tab 完整数据（包含 BCN 状态）
 * 用于 TopNavBar 渲染
 */
export interface BotTabData extends BotTabItem {
  /** 可见性状态 */
  visibility: BotVisibility;
  /** 是否在线 */
  is_online: boolean;
  /** 能力标签 */
  capabilities?: {
    domains?: string[];
    skills?: string[];
  };
}

// 重新导出 DriverBot 类型，方便使用
export type { DriverBot } from '@/stores/botNetworkStore';

// === Session 相关类型 ===

/** 会话类型 */
export type SessionKind = 'chat' | 'service_invocation';

/** 会话状态 */
export type SessionStatus = 'running' | 'completed';

/** 会话信息 */
export interface GroupSession {
  /** 会话 ID，格式: "{group_id}:{8_hex}" */
  sessionId: string;
  /** 会话类型 */
  sessionKind: SessionKind;
  /** 会话标题（首句消息前20字，可编辑） */
  sessionTitle: string;
  /** 所属群组 ID */
  groupId: string;
  /** 会话状态 */
  status: SessionStatus;
  /** 会话成员 */
  members: Array<{
    actorId: string;
    actorKind: ActorKind;
    name?: string;
    avatar?: string;
    mode?: ParticipantMode;
    role?: string;
    type?: string;
  }>;
  /** 创建时间 */
  createdAt: number;
  /** 更新时间 */
  updatedAt: number;
  /** 创建者标识 */
  createdBy?: string;
}
export interface CreateSessionParams {
  /** 会话类型，默认 chat */
  session_kind: SessionKind;
  /** 会话标题 */
  session_title?: string;
  /** 创建者标识（用户视角下传用户的 bot_uuid，即 human_{userId}） */
  created_by?: string;
  /** 服务调用输入（service_invocation 会话使用） */
  input?: {
    query: string;
  };
}

// === 常量 ===

/**
 * BCS 系统消息的固定 bot_uuid 标识
 *
 * 后端通过此 bot_uuid 标识"用户加入/退出协作群"等系统通知,
 * HTTP 和 WS 两条通道都用同一值。前端在以下三处用它来识别系统消息:
 * - HTTP transform (transformMessageData)
 * - WS provider.onMessage 拦截器
 * - subscribeToGroupResult 过滤假 Bot
 */
export const BCS_SYSTEM_MESSAGE_BOT_UUID = 'bcs-system-message';
