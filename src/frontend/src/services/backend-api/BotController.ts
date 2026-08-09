/* eslint-disable */
/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Bot Controller - Bot 管理相关 API
 * 文档: docs/bot-api.md
 *
 * 设计原则：Controller 只做接口透传，不做协作场景下 owner_id 的反查。
 * Hook 层调用方负责使用 `resolveBotOwnerId(botId)` 显式传入 owner_id。
 */

import { request } from '@umijs/max';
import type { LockInfo, ReadOnlyRule } from './ServiceBotController';

// ======================== 类型定义 ========================

/**
 * Bot 状态枚举
 */
export type BotStatus =
  | 'PENDING'
  | 'ACTIVE'
  | 'FAILED'
  | 'RELEASED'
  | 'OFFLINE';

export const BOT_STATUS: Record<string, BotStatus> = {
  OFFLINE: 'OFFLINE',
  PENDING: 'PENDING',
  ACTIVE: 'ACTIVE',
  FAILED: 'FAILED',
  RELEASED: 'RELEASED',
} as const;

/**
 * Bot 类型枚举
 */
export type BotType = 'personal' | 'service' | 'desktop';

export const BOT_TYPE: Record<string, BotType> = {
  PERSONAL: 'personal',
  SERVICE: 'service',
  DESKTOP: 'desktop',
} as const;

/**
 * Entity 类型枚举
 */
export type EntityType = 'staff' | 'proj' | 'team';

export const ENTITY_TYPE: Record<string, EntityType> = {
  STAFF: 'staff',
  TEAM: 'team',
  PROJECT: 'proj',
} as const;

/**
 * 引擎类型
 */
export type EngineType =
  | 'moltis'
  | 'openclaw'
  | 'hermes'
  | 'aicoding'
  | 'claude_code'
  | 'teclaw';

export const ENGINE_TYPE: Record<string, EngineType> = {
  MOLTIS: 'moltis',
  OPENCLAW: 'openclaw',
  HERMES: 'hermes',
  AICODING: 'aicoding',
  CLAUDECODE: 'claude_code',
  TECLAW: 'teclaw',
} as const;

/**
 * 审批状态枚举
 */
export type ApprovalStatus = 'PROCESSING' | 'AGREE' | 'DISAGREE' | 'CANCEL';

export const APPROVAL_STATUS: Record<string, ApprovalStatus> = {
  PROCESSING: 'PROCESSING',
  AGREE: 'AGREE',
  DISAGREE: 'DISAGREE',
  CANCEL: 'CANCEL',
} as const;

/**
 * 权限控制类型
 */
export type PermissionOwner = 'caller' | 'owner';

export const PERMISSION_OWNER: Record<string, PermissionOwner> = {
  CALLER: 'caller',
  OWNER: 'owner',
} as const;

/**
 * 公开状态枚举
 */
export type PublicStatus = '0' | '1';

export const PUBLIC_STATUS: Record<string, PublicStatus> = {
  PRIVATE: '0', // 私有
  PUBLIC: '1', // 公开
} as const;

/**
 * 审批信息对象
 */
export interface PublicApproval {
  /** 审批流程唯一 ID */
  puid: string;
  /** 审批页面 URL */
  approval_url: string;
  /** 申请设置的公开状态: '0' 或 '1' */
  public: PublicStatus;
  /** 申请人 ID */
  applicant: string;
  /** 审批状态 */
  status: ApprovalStatus;
  /** 处理完成时间 (ISO 8601)，审批结束后填充 */
  processed_at?: string;
}

/**
 * Bot 扩展字段
 */
export interface BotExt {
  /** 权限控制: caller=直接更新, owner=需要审批 */
  permission_owner?: PermissionOwner;
  /** 当前审批信息 */
  public_approval?: PublicApproval | null;
  /** 历史审批记录数组，最多保留最近 5 条 */
  public_approval_history?: PublicApproval[];
  /** 头像 URL（by-conditions 和 by-owner 接口返回） */
  avatar_url?: string;
  /** 好友审批: '0'=不需要审批, '1'=需要审批 */
  friend_approval?: '0' | '1';
  /** 桌面 Bot 本地磁盘挂载路径 */
  mount_path?: string;
  /** 沙箱只读规则（GET /api/bots/{bot_id} 返回） */
  read_only_rules?: ReadOnlyRule[];
  /** 模板配置（应用 Coding 等引擎的专属配置） */
  template_config?: TemplateConfig;
}

/**
 * 域架构师 Bot 列表响应
 * GET /api/bots/search/domain-bots
 */
export interface DomainBotsResponse {
  success: boolean;
  message?: string;
  error_code?: number;
  data: {
    total: number;
    items: Bot[];
  };
}

// getBotAvatarUrl 已迁移至 @/utils/bot，此处保留重导出保持向后兼容
export { getBotAvatarUrl } from '@/utils/bot';

/**
 * Bot 对象
 */
export interface Bot {
  id?: number;
  bot_id: string;
  bot_name: string;
  bot_desc: string | null;
  /** 头像 URL */
  avatar_url?: string | null;
  entity_id: string;
  entity_type: EntityType;
  creator_id: string;
  owner_id: string;
  engine_types: EngineType[];
  status: BotStatus;
  binding_id: number | null;
  gmt_create: string;
  gmt_modified: string;
  modifier_id?: string;
  share_policy?: any | null;
  is_delete: number;
  active_engine?: EngineType;
  device_id?: string | null;
  /** 环境标识，如 dev */
  env?: string;
  owner_name?: string | null;
  /** 公开状态: '0'=私有, '1'=公开 */
  public?: PublicStatus;
  /** 扩展字段 */
  ext?: BotExt;
  // 扩展字段（仅在 by-owner 接口返回）
  engine_paths?: {
    moltis?: string;
    openclaw?: string;
  };
  bot_work_dir?: string;
  /** Bot 类型: 'personal'=个人型, 'service'=服务型 */
  bot_type?: BotType;
  /** 模板类型: 'applicationCoding'=应用Coding, 'personalCoding'=个人Coding */
  template_type?: string;
  /** 是否可编辑 Bot（仅 owner 返回 true） */
  can_edit_bot?: boolean;
  /**
   * 当前用户对此 Bot 的角色（服务 Bot 协作场景）
   * - 'owner': Bot 所有者
   * - 'collaborator': 协作者
   * - null/undefined: 无关系（仅 owner_id === currentUser 的反推回退）
   */
  user_role?: 'owner' | 'collaborator' | null;
  /**
   * 协作锁信息（服务 Bot 专属）
   * lock_holder 为当前用户 ID 表示自己持锁；为 null 表示无人持锁
   */
  lock_info?: LockInfo;
}

/**
 * 创建 Bot 请求参数
 */
export interface CreateBotRequest {
  /** Bot 名称 */
  bot_name?: string;
  bot_desc?: string;
  /** 头像 URL */
  avatar_url?: string;
  entity_id?: string;
  entity_type?: EntityType;
  share_policy?: any;
  engine_type?: string; // 引擎类型，如 'openclaw' 或 'moltis'
  /** Bot 类型: 'personal'=个人型, 'service'=服务型，默认 'personal' */
  bot_type?: BotType;
  /** 模板类型，如 'applicationCoding' */
  template_type?: string;
  /** 模板配置（应用 Coding 等引擎的专属配置，JSON 结构） */
  template_config?: TemplateConfig;
}

/**
 * 桌面设备信息
 */
export interface DesktopDevice {
  /** 设备唯一 ID */
  machine_id: string;
  /** 设备名称 */
  machine_name: string;
  /** 设备状态: PENDING / ACTIVE / OFFLINE / RELEASED */
  status: 'PENDING' | 'ACTIVE' | 'OFFLINE' | 'RELEASED';
  /** 最后在线时间 */
  last_alive_at?: string;
  /** 创建时间 */
  created_at?: string;
}

/**
 * 桌面设备列表响应
 */
export interface DesktopDeviceListResponse {
  total: number;
  items: DesktopDevice[];
}

/**
 * 设备文件树节点
 */
export interface DeviceFileNode {
  /** 文件或目录名 */
  name: string;
  /** 子节点（存在则表示为目录） */
  children?: DeviceFileNode[];
}

/**
 * 设备目录列表响应
 */
export interface ListDeviceFilesResponse {
  success: boolean;
  data: {
    /** 根目录绝对路径 */
    absolute_path: string;
    /** 目录树 */
    dirs: DeviceFileNode;
  };
}

/**
 * 创建桌面 Bot 请求
 */
export interface CreateDesktopBotRequest {
  /** Bot 名称 */
  bot_name: string;
  /** 主机设备 ID */
  machine_id: string;
  /** Bot 描述（可选） */
  bot_desc?: string;
  /** 挂载目录（可选） */
  mount_path?: string;
  /** 引擎类型（可选） */
  engine_type?: string;
}

/**
 * 创建桌面 Bot 响应
 */
export interface CreateDesktopBotResponse {
  success: boolean;
  data?: {
    /** BAAS Bot UUID，即 device_id */
    bot_uuid: string;
    /** AC 设备绑定记录 ID */
    binding_id: number;
    /** Bot ID */
    bot_id: string;
  };
}

/**
 * 重启桌面 Bot 响应
 */
export interface RestartDesktopBotResponse {
  success: boolean;
  data?: {
    device_id: string;
    bot_id: string;
    status: string;
  };
}

/**
 * 删除桌面 Bot 响应
 */
export interface DeleteDesktopBotResponse {
  success: boolean;
  data?: {
    device_id: string;
    bot_id: string;
    status: string;
  };
}

/**
 * 桌面 Bot 状态检查响应
 */
export interface CheckDesktopBotsStatusResponse {
  success: boolean;
  data?: any[];
}

/**
 * 桌面 Bot 授权状态查询请求
 */
export interface DesktopBotAuthStatusRequest {
  /** Bot ID（必填） */
  bot_id: string;
  /** Bot 名称 */
  bot_name?: string;
  /** Bot 描述 */
  bot_desc?: string;
  /** 头像 URL */
  avatar_url?: string;
  /** 设备 ID */
  machine_id?: string;
  /** 挂载目录 */
  mount_path?: string;
}

/**
 * 桌面 Bot 授权状态查询响应
 */
export interface DesktopBotAuthStatusResponse {
  success: boolean;
  message?: string;
  error_code?: number;
  data?: {
    /** 授权状态：PENDING / ISSUED */
    status: 'PENDING' | 'ISSUED';
    message?: string;
    /** Bot 信息，仅 ISSUED 时返回 */
    bot?: any;
  };
}

/**
 * 模板配置 - 应用 Coding 等引擎的专属配置
 */
export interface TemplateConfig {
  /** 语雀知识库仓库列表 */
  yuque_kb_repos?: YuqueKbRepo[];
  /** DevFlow 工作流完整信息 */
  devflow_workflow?: {
    name: string;
    description?: string;
    category?: string;
    domain?: string;
    path: string;
    title?: string;
    desc?: string;
    tags?: string[];
  };
  /** 二级域架构师 Bot ID */
  architect_bot_id?: string;
  /** 是否 7×24 托管：0-否，1-是 */
  is_hosted_24x7?: number;
  /** 触发频率：hourly/daily/weekly/custom */
  trigger_frequency?: string;
  /** 并发限制：同时运行的最大任务数 */
  concurrency_limit?: number;
  /** 成员配置：逗号分隔的成员标识列表 */
  members?: string;
  /** 前端代码仓库列表 */
  frontend_repo?: RepoItem[];
  /** 后端代码仓库列表 */
  backend_repo?: RepoItem[];
  /** lib 代码仓库列表 */
  lib_repo?: RepoItem[];
  /** 自定义沙箱镜像地址，如 "registry/repo/image:tag" */
  image?: string;
  /**
   * 沙箱资源规格（可选，仅在 URL 带 ?advanced=1 时允许前端编辑）
   * cpu / memory / disk 均为正整数；不填则走平台默认。
   * 校验规则：要么都不填，填一个则三个都要填。
   */
  resource_spec?: ResourceSpec;
  /** Dima 空间 ID（只读，由后端返回） */
  dima_space_id?: string;
}

/** 沙箱资源规格 */
export interface ResourceSpec {
  /** CPU 核数（正整数） */
  cpu?: number;
  /** 内存大小（正整数，单位由后端定义，通常为 GiB） */
  memory?: number;
  /** 磁盘大小（正整数，单位由后端定义，通常为 GiB） */
  disk?: number;
}

/** 语雀知识库仓库 */
export interface YuqueKbRepo {
  /** 空间 token */
  token?: string;
  /** 知识库访问地址 */
  url?: string;
}

/** 代码仓库项 */
export interface RepoItem {
  /** 仓库地址 */
  repo_url?: string;
}

/**
 * 创建 Bot 响应数据（需要授权场景）
 */
export interface CreateBotAuthData {
  /** 需要授权 */
  need_authorization: true;
  /** Bot ID */
  bot_id: string;
  /** 授权 iframe URL */
  iframe_url: string;
  /** 授权后跳转 URL */
  redirect_url: string;
}

/**
 * 创建 Bot 成功响应数据
 */
export interface CreateBotSuccessData {
  bot: Bot;
}

/**
 * 创建 Bot 响应
 */
export interface CreateBotResponse {
  success: boolean;
  message: string;
  error_code: number;
  data: CreateBotSuccessData | CreateBotAuthData;
}

/**
 * 授权状态
 */
export type AuthStatus = 'PENDING' | 'ISSUED';

/**
 * 查询 Bot 授权状态响应
 */
export interface BotAuthStatusResponse {
  success: boolean;
  message?: string;
  error_code?: number;
  data?: {
    /** 授权状态 */
    status: AuthStatus;
    /** Bot 信息，仅 ISSUED 时返回 */
    bot?: Bot;
  };
}

/**
 * Bot 列表响应（By Owner）
 */
export interface BotListByOwnerResponse {
  success: boolean;
  message: string;
  error_code: number;
  data: {
    total: number;
    items: Bot[];
    default_bot: {
      entity_id: string;
      bot_id: string;
      entity_type: EntityType;
      engine_type: EngineType;
      bot_work_dir: string;
      engine_paths: {
        moltis?: string;
        openclaw?: string;
      };
    } | null;
    engine_types: EngineType[];
  };
}

/**
 * Bot 列表响应（By Entity）
 */
export interface BotListByEntityResponse {
  success: boolean;
  message: string;
  error_code: number;
  data: {
    total: number;
    items: Bot[];
  };
}

/**
 * Bot 状态响应
 */
export interface BotStatusResponse {
  success: boolean;
  data: {
    bot_id: string;
    bot_status: BotStatus;
    binding_status: 'PENDING' | 'ACTIVE' | 'FAILED';
    device_id: string | null;
    device_provider: string | null;
    error_message: string | null;
    is_ready: boolean;
    /** 容器启动状态，容器成功后 Bot 才能 ACTIVE */
    ext?: {
      start_status: 'STARTING' | 'SUCCEEDED' | 'FAILED';
      start_message: string;
    };
  };
}

/**
 * 删除 Bot 响应
 */
export interface DeleteBotResponse {
  success: boolean;
  message: string;
  error_code: number;
  data: null;
}

/**
 * 更新 Bot 请求参数
 */
export interface UpdateBotRequest {
  /** Bot 名称 */
  bot_name?: string;
  search?: string;
  bot_desc?: string;
  /** 头像 URL */
  avatar_url?: string;
  share_policy?: any;
  /** 模板配置（应用 Coding 等引擎的专属配置，JSON 结构） */
  template_config?: TemplateConfig;
}

/**
 * 更新 Bot 响应
 */
export interface UpdateBotResponse {
  success: boolean;
  message: string;
  error_code: number;
  data: Bot;
}

/**
 * 重启 Bot 请求参数
 */
export interface RestartBotRequest {
  user_id: string;
  nick_name?: string;
}

/**
 * 重启 Bot 响应（重启后 Bot 进入 PENDING 状态，等待分配设备）
 */
export interface RestartBotResponse {
  success: boolean;
  message: string;
  error_code: number;
  data: Bot | null;
}

/**
 * 条件搜索 Bot 请求参数
 */
export interface SearchBotsRequest {
  /** 公开状态: '0'=私有, '1'=公开 */
  public?: PublicStatus;
  /** Bot 名称(支持模糊查询) */
  search?: string;
  /** 页码,从 1 开始 */
  page?: number;
  /** 每页数量,范围 1-100 */
  page_size?: number;
  /** Bot 类型过滤: 'personal'=个人型, 'service'=服务型 */
  bot_type?: BotType;
  /** 关键词（模糊搜索 bot_name 或 owner_name） */
  key?: string;
  /** Bot 状态过滤 */
  bot_status?: BotStatus;
  /** 引擎类型过滤 */
  active_engine?: EngineType;
  /** 所有者 ID 过滤 */
  owner_id?: string;
  /**
   * 协作者用户 ID 过滤
   * - 传入后，仅返回该用户作为协作者参与（非 owner）的 Bot
   * - 与 owner_id 互斥使用：'我协作的' Tab 走此字段，'我创建的' Tab 走 owner_id
   * - 文档：内部接口文档
   */
  collaborator_user_id?: string;
  /** 发布状态过滤 */
  service_status_list?: string[];
  /**
   * 角色筛选（服务 Bot 列表 Tab 用，保留兼容字段）
   * - 'owner': 仅返回当前用户是 owner 的 Bot
   * - 'collaborator': 仅返回当前用户是协作者（非 owner）的 Bot
   * - undefined: 返回全部（owner + 协作）
   * 注：新接口推荐使用 collaborator_user_id 显式传协作者 ID；
   *     role_filter 仍传可双供，等后端确认后再决定是否下线。
   */
  role_filter?: 'owner' | 'collaborator';
}

/**
 * 条件搜索 Bot 响应
 */
export interface SearchBotsResponse {
  success: boolean;
  message: string;
  error_code: number;
  data: {
    total: number;
    items: Bot[];
  };
}

/**
 * 设置 Bot 公开状态请求参数
 */
export interface SetBotPublicRequest {
  /** 公开状态: '0'=私有, '1'=公开 */
  public: PublicStatus;
  /** 权限控制: caller=直接更新, owner=需要审批后更新 */
  permission_owner: PermissionOwner;
  /** 好友审批: '0'=不需要审批, '1'=需要审批（公开时生效） */
  friend_approval?: '0' | '1';
}

/**
 * 设置 Bot 公开状态响应
 */
export interface SetBotPublicResponse {
  success: boolean;
  message: string;
  error_code: number;
  data: Bot;
}

/**
 * Bot Onboard 请求参数
 * POST /admin/bots/onboard
 */
export interface OnboardBotRequest {
  /** Bot 唯一标识（必须已存在） */
  bot_id: string;
  /** Bot 显示名称 */
  name: string;
  /** Bot 简介 */
  summary: string;
  /** Bot 擅长的领域 */
  domains?: string[];
  /** Bot 具备的技能 */
  skills?: string[];
  /** Bot 可访问的范围 */
  scopes?: string[];
  /** 渠道绑定配置 */
  binding_channels?: Record<string, { binding_key: string }>;
  /** 是否隐藏 Bot（true=移出协作网络，false=加入协作网络） */
  hidden?: boolean;
}

/**
 * Bot Onboard 绑定结果
 */
export interface BindingResult {
  status: string;
}

/**
 * Bot Onboard 响应
 */
export interface OnboardBotResponse {
  success: boolean;
  message: string;
  error_code?: number;
  data?: {
    bot_uuid: string;
    onboarded: boolean;
    name: string;
    binding_results?: Record<string, BindingResult>;
    unbound?: string[];
  };
}

/**
 * 触发数据初始化请求参数
 */
export interface TriggerBotDataInitRequest {
  /** 默认false。设为true时强制重新执行（即使已completed） */
  force?: boolean;
}

/**
 * 触发数据初始化响应数据
 */
export interface TriggerBotDataInitData {
  /** 初始化状态 */
  status: 'in_progress' | 'pending_init' | 'skipped' | string;
  /** 状态说明 */
  message: string;
}

/**
 * 触发数据初始化响应
 */
export interface TriggerBotDataInitResponse {
  success: boolean;
  data: TriggerBotDataInitData;
}

/**
 * MCP 信息
 */
export interface McpInfo {
  /** MCP 编码 */
  mcp_code: string;
  /** MCP 名称（可能为空） */
  mcp_name?: string;
  /** MCP 描述（可能为空） */
  mcp_desc?: string;
}

/**
 * Bot Agent Passport 响应
 * GET /api/bots/{bot_id}/passport
 */
export interface BotPassportResponse {
  success: boolean;
  message: string;
  error_code?: number;
  data?: {
    /** AI 工作台 botId（env|workno|botId 格式） */
    agent_id: string;
    /** Agent Hub 唯一 code */
    agent_code: string;
    /** 凭证 ID */
    credential_id: string;
    /** 过期时间，ISO 8601 格式 */
    expire_at: string;
    /** 关联的 MCP 列表 */
    mcps: McpInfo[];
    /** 证书 URL */
    certificate_url: string;
  };
}

/**
 * 检查 Bot 名称是否存在响应
 * GET /api/bots/check/name
 */
export interface CheckBotNameResponse {
  success: boolean;
  message: string;
  error_code: number;
  data: {
    /** 是否存在相同名称的 Bot */
    exists: boolean;
    /** 查询的 Bot 名称 */
    bot_name: string;
  };
}

// ======================== API 方法 ========================

const BotControllerImpl = {
  /**
   * 创建 Bot
   * POST /api/bots
   */
  createBot: async (
    body: CreateBotRequest,
    options?: { [key: string]: any },
  ): Promise<CreateBotResponse> => {
    return request<CreateBotResponse>('/api/bots', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      data: body,
      ...(options || {}),
    });
  },

  /**
   * 获取当前用户的 Bot 列表（管理页面用，含协作 Bot）
   * GET /api/bots/by-owner-or-collaborator?user_id=xxx
   *
   * 注：原 `/api/bots/by-owner` 仅返回 owner 名下的 Bot；
   * 新路径会一并返回当前用户被加为协作者的 Bot，响应结构与 by-owner 保持兼容
   * （含 default_bot、engine_types 等字段）。
   *
   * 仅传 `user_id` 后端即按"owner 或 协作者"取并集返回（返回项含各自真实 owner_id），
   * 写到 allBots 的全集会包含协作的 service bot，给 Hook 层 resolveBotOwnerId 兜底用。
   * `collaborator_user_id` 为可选参数，不传也能拿到协作 Bot。
   */
  getBotsByOwner: async (
    params: {
      user_id?: string;
      collaborator_user_id?: string;
      onboarded?: boolean;
    },
    options?: { [key: string]: any },
  ): Promise<BotListByOwnerResponse> => {
    return request<BotListByOwnerResponse>(
      '/api/bots/by-owner-or-collaborator',
      {
        method: 'GET',
        params: {
          ...params,
          ...(params.onboarded !== undefined && {
            onboarded: params.onboarded,
          }),
        },
        ...(options || {}),
      },
    );
  },

  /**
   * 按 Entity 筛选 Bot 列表
   * GET /api/bots?entity_id=xxx&entity_type=staff&page=1&page_size=20
   */
  getBotsByEntity: async (
    params: {
      entity_id?: string;
      entity_type?: EntityType;
      page?: number;
      page_size?: number;
    },
    options?: { [key: string]: any },
  ): Promise<BotListByEntityResponse> => {
    return request<BotListByEntityResponse>('/api/bots', {
      method: 'GET',
      params: {
        ...params,
      },
      ...(options || {}),
    });
  },

  /**
   * 获取 Bot 详情
   * GET /api/bots/{bot_id}
   */
  getBotDetail: async (
    params: { bot_id: string; owner_id?: string },
    options?: { [key: string]: any },
  ): Promise<CreateBotResponse> => {
    const { bot_id, owner_id } = params;
    return request<CreateBotResponse>(`/api/bots/${bot_id}`, {
      method: 'GET',
      ...(owner_id ? { params: { owner_id } } : {}),
      ...(options || {}),
    });
  },

  /**
   * 查询 Bot Agent Passport 信息
   * GET /api/bots/{bot_id}/passport
   */
  queryAgentPassport: async (
    params: { bot_id: string; owner_id?: string },
    options?: { [key: string]: any },
  ): Promise<BotPassportResponse> => {
    const { bot_id, owner_id } = params;
    return request<BotPassportResponse>(`/api/bots/${bot_id}/passport`, {
      method: 'GET',
      ...(owner_id ? { params: { owner_id } } : {}),
      ...(options || {}),
    });
  },

  /**
   * 查询 Bot 设备分配状态
   * GET /api/bots/{bot_id}/status
   */
  getBotStatus: async (
    params: { bot_id: string; owner_id?: string },
    options?: { [key: string]: any },
  ): Promise<BotStatusResponse> => {
    const { bot_id, owner_id } = params;
    return request<BotStatusResponse>(`/api/bots/${bot_id}/status`, {
      method: 'GET',
      ...(owner_id ? { params: { owner_id } } : {}),
      ...(options || {}),
    });
  },

  /**
   * 查询 Bot 授权状态
   * POST /api/bots/auth-status
   */
  getBotAuthStatus: async (
    params: {
      bot_id: string;
      bot_name: string;
      bot_desc: string;
      entity_id?: string;
      entity_type?: 'staff' | 'proj' | 'team';
      share_policy?: Record<string, any>;
      engine_type?: string;
      avatar_url?: string;
      bot_type?: BotType;
      template_type?: string;
      template_config?: TemplateConfig;
    },
    options?: { [key: string]: any },
  ): Promise<BotAuthStatusResponse> => {
    const {
      bot_id,
      bot_name,
      bot_desc,
      entity_id,
      entity_type,
      share_policy,
      engine_type,
      avatar_url,
      bot_type,
      template_type,
      template_config,
    } = params;
    return request<BotAuthStatusResponse>('/api/bots/auth-status', {
      method: 'POST',
      data: {
        bot_id,
        bot_name,
        bot_desc,
        entity_id,
        entity_type,
        share_policy,
        engine_type,
        avatar_url,
        bot_type,
        template_type,
        template_config,
      },
      headers: {
        'Content-Type': 'application/json',
      },
      ...(options || {}),
    });
  },

  /**
   * 更新 Bot
   * PUT /api/bots/{bot_id}
   */
  updateBot: async (
    params: { bot_id: string; owner_id?: string } & UpdateBotRequest,
    options?: { [key: string]: any },
  ): Promise<UpdateBotResponse> => {
    const { bot_id, owner_id, ...body } = params;
    return request<UpdateBotResponse>(`/api/bots/${bot_id}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      ...(owner_id ? { params: { owner_id } } : {}),
      data: body,
      ...(options || {}),
    });
  },

  /**
   * 释放/删除 Bot
   * DELETE /api/bots/{bot_id}
   */
  deleteBot: async (
    params: { bot_id: string; owner_id?: string },
    options?: { [key: string]: any },
  ): Promise<DeleteBotResponse> => {
    const { bot_id, owner_id } = params;
    return request<DeleteBotResponse>(`/api/bots/${bot_id}`, {
      method: 'DELETE',
      ...(owner_id ? { params: { owner_id } } : {}),
      ...(options || {}),
    });
  },

  // ======================== 保留的旧方法（兼容性）========================

  /**
   * @deprecated 使用 getBotsByOwner 替代
   */
  listBots: async (
    params: { user_id: string },
    options?: { [key: string]: any },
  ) => {
    const response = await BotControllerImpl.getBotsByOwner(
      { user_id: params.user_id },
      options,
    );
    return {
      success: response.success,
      data: response.data.items,
    };
  },

  /**
   * 重启 Bot
   * POST /api/bots/{bot_id}/restart
   * 重启后 Bot 进入 PENDING 状态，需轮询等待激活
   */
  restartBot: async (
    params: { bot_id: string; owner_id?: string } & RestartBotRequest,
    options?: { [key: string]: any },
  ): Promise<RestartBotResponse> => {
    const { bot_id, owner_id, ...body } = params;
    return request<RestartBotResponse>(`/api/bots/${bot_id}/restart`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      ...(owner_id ? { params: { owner_id } } : {}),
      data: body,
      ...options,
    });
  },

  /**
   * @deprecated 使用 getBotStatus 替代
   */
  resetBot: async (
    data: { bot_id: string; user_id: string },
    options?: { [key: string]: any },
  ) => {
    // 重置Bot的逻辑需要后端支持，暂时返回错误
    console.warn(
      '[BotController] resetBot is deprecated and not supported by backend',
    );
    return {
      success: false,
      message: 'resetBot is deprecated, please use getBotStatus',
    };
  },

  /**
   * @deprecated 旧版切换 Bot（前端状态管理）
   */
  switchBot: async (
    data: { bot_id: string; user_id: string },
    options?: { [key: string]: any },
  ) => {
    // 切换Bot是前端状态管理，只需验证Bot存在且可用
    const response = await BotControllerImpl.getBotDetail(
      { bot_id: data.bot_id },
      options,
    );

    if (!response.success) {
      return {
        success: false,
        message: 'Bot 不存在',
      };
    }

    const bot = response.data as unknown as Bot;
    if (bot.status !== 'ACTIVE') {
      return {
        success: false,
        message: 'Bot 状态不是 ACTIVE，无法切换',
      };
    }

    return {
      success: true,
      data: bot,
    };
  },

  // 没有 switchBotEngine：Bot 的引擎在创建时固定（inclusionAI/Avernet#914），
  // 后端已不再提供 POST /api/bots/switch-engine。换引擎意味着新建一个 Bot。

  /**
   * 获取 Bot 连接信息
   * GET /api/v1/devices/{binding_id}/connection
   *
   * 协作场景：后端会校验"非公开 Bot 只能获取本人设备的连接信息"。
   * 协作者操作他人 Bot 时，Hook 层需通过 resolveBotOwnerId(bot_id) 显式传入 owner_id，
   * 让后端按 Bot 归属鉴权。
   */
  getBotConnection: async (
    params: { binding_id: number; bot_id?: string; owner_id?: string },
    options?: { [key: string]: any },
  ): Promise<{
    success: boolean;
    data: {
      type?: 'local' | 'remote';
      target: string;
      token: string;
    };
    message?: string;
  }> => {
    const { binding_id, owner_id } = params;
    return request<{
      success: boolean;
      data: {
        type?: 'local' | 'remote';
        target: string;
        token: string;
      };
    }>(`/api/v1/devices/${binding_id}/connection`, {
      method: 'GET',
      ...(owner_id ? { params: { owner_id } } : {}),
      ...(options || {}),
    });
  },

  /**
   * 关键词搜索 Bot
   * POST /api/bots/search
   */
  searchBotsByConditions: async (
    params: SearchBotsRequest,
    options?: { [key: string]: any },
  ): Promise<SearchBotsResponse> => {
    return request<SearchBotsResponse>('/api/bots/search', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      data: params,
      ...(options || {}),
    });
  },

  /**
   * 公开/取消公开 Bot
   * POST /api/bots/{bot_id}/public
   *
   * 协作场景下需要传 owner_id（注入 body），让后端按 bot 归属鉴权。Hook 层调用方
   * 负责使用 `resolveBotOwnerId(botId)` 显式传入。
   */
  setBotPublic: async (
    params: { bot_id: string; owner_id?: string } & SetBotPublicRequest,
    options?: { [key: string]: any },
  ): Promise<SetBotPublicResponse> => {
    const { bot_id, owner_id, ...body } = params;
    return request<SetBotPublicResponse>(`/api/bots/${bot_id}/public`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      data: { ...body, ...(owner_id ? { owner_id } : {}) },
      ...(options || {}),
    });
  },

  /**
   * 查询二级域架构师 Bot 列表
   * GET /api/bots/search/domain-bots
   */
  searchDomainBots: async (
    params: { page?: number; page_size?: number } = {},
    options?: { [key: string]: any },
  ): Promise<DomainBotsResponse> => {
    return request<DomainBotsResponse>('/api/bots/search/domain-bots', {
      method: 'GET',
      params: {
        page: params.page || 1,
        page_size: params.page_size || 100,
      },
      ...(options || {}),
    });
  },

  /**
   * 检查 Bot 名称是否已存在
   * GET /api/bots/check/name
   */
  checkBotName: async (
    params: { bot_name: string },
    options?: { [key: string]: any },
  ): Promise<CheckBotNameResponse> => {
    return request<CheckBotNameResponse>('/api/bots/check/name', {
      method: 'GET',
      params: {
        bot_name: params.bot_name,
      },
      ...(options || {}),
    });
  },

  /**
   * Bot Onboard / 加入或移出协作网络
   * POST /bcnproxy/admin/bots/onboard
   * - hidden=false: 加入协作网络（取消隐藏）
   * - hidden=true: 移出协作网络（隐藏）
   */
  onboardBot: async (
    body: OnboardBotRequest,
    options?: { [key: string]: any },
  ): Promise<OnboardBotResponse> => {
    return request<OnboardBotResponse>('/bcnproxy/admin/bots/onboard', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      data: body,
      skipErrorHandler: true,
      ...(options || {}),
    });
  },

  /**
   * 触发 Bot 数据初始化
   * POST /api/bots/{bot_id}/data-init
   */
  triggerBotDataInit: async (
    params: {
      bot_id: string;
      owner_id?: string;
    } & TriggerBotDataInitRequest,
    options?: { [key: string]: any },
  ): Promise<TriggerBotDataInitResponse> => {
    const { bot_id, owner_id, ...body } = params;
    return request<TriggerBotDataInitResponse>(
      `/api/bots/${bot_id}/data-init`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        ...(owner_id ? { params: { owner_id } } : {}),
        data: body,
        ...(options || {}),
      },
    );
  },

  /**
   * 获取桌面设备列表
   * GET /api/desktop/devices
   */
  getDesktopDevices: async (
    params?: { page?: number; page_size?: number; status?: string },
    options?: { [key: string]: any },
  ): Promise<DesktopDeviceListResponse> => {
    return request<DesktopDeviceListResponse>('/api/desktop/devices', {
      method: 'GET',
      params,
      ...(options || {}),
    });
  },

  /**
   * 创建桌面 Bot
   * POST /api/desktop/bots
   */
  createDesktopBot: async (
    body: CreateDesktopBotRequest,
    options?: { [key: string]: any },
  ): Promise<CreateDesktopBotResponse> => {
    return request<CreateDesktopBotResponse>('/api/desktop/bots', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      data: body,
      ...(options || {}),
    });
  },

  /**
   * 重启桌面 Bot
   * POST /api/desktop/bots/{bot_id}/restart
   */
  restartDesktopBot: async (
    botId: string,
    options?: { [key: string]: any },
  ): Promise<RestartDesktopBotResponse> => {
    return request<RestartDesktopBotResponse>(
      `/api/desktop/bots/${botId}/restart`,
      {
        method: 'POST',
        ...(options || {}),
      },
    );
  },

  /**
   * 删除桌面 Bot
   * DELETE /api/desktop/bots/{bot_id}
   */
  deleteDesktopBot: async (
    botId: string,
    options?: { [key: string]: any },
  ): Promise<DeleteDesktopBotResponse> => {
    return request<DeleteDesktopBotResponse>(`/api/desktop/bots/${botId}`, {
      method: 'DELETE',
      ...(options || {}),
    });
  },

  /**
   * 检查桌面 Bot 设备状态
   * POST /api/desktop/bots/status-check
   */
  checkDesktopBotsStatus: async (options?: {
    [key: string]: any;
  }): Promise<CheckDesktopBotsStatusResponse> => {
    return request<CheckDesktopBotsStatusResponse>(
      '/api/desktop/bots/status-check',
      {
        method: 'POST',
        ...(options || {}),
      },
    );
  },

  /**
   * 查询桌面 Bot 授权状态（两段式第二步）
   * POST /api/desktop/bots/auth-status
   */
  getDesktopBotAuthStatus: async (
    params: DesktopBotAuthStatusRequest,
    options?: { [key: string]: any },
  ): Promise<DesktopBotAuthStatusResponse> => {
    return request<DesktopBotAuthStatusResponse>(
      '/api/desktop/bots/auth-status',
      {
        method: 'POST',
        data: params,
        headers: {
          'Content-Type': 'application/json',
        },
        ...(options || {}),
      },
    );
  },

  /**
   * 获取设备目录树
   * GET /api/desktop/devices/{machine_id}/files
   */
  listDeviceFiles: async (
    machineId: string,
    options?: { [key: string]: any },
  ): Promise<ListDeviceFilesResponse> => {
    return request<ListDeviceFilesResponse>(
      `/api/desktop/devices/${machineId}/files`,
      {
        method: 'GET',
        ...(options || {}),
      },
    );
  },

  /**
   * 创建 Dima 空间
   * POST /api/aicoding/bot/{bot_id}/dima-workspace?user_id={user_id}
   *
   * 为应用 Bot 创建 Dima 协作空间，创建后需刷新配置以获取新的 dima_space_id。
   */
  createDimaWorkspace: async (
    params: { bot_id: string; user_id: string },
    options?: { [key: string]: any },
  ): Promise<{ success: boolean; message?: string; data?: any }> => {
    const { bot_id, user_id } = params;
    return request<{ success: boolean; message?: string; data?: any }>(
      `/api/aicoding/bot/${bot_id}/dima-workspace`,
      {
        method: 'POST',
        params: { user_id },
        ...(options || {}),
      },
    );
  },

  /**
   * 在本地资源管理器中打开桌面 Bot 挂载目录
   * POST /api/bots/{bot_id}/open-folder
   */
  openBotFolder: async (
    params: { bot_id: string },
    options?: { [key: string]: any },
  ): Promise<{ success: boolean; message?: string; data?: any }> => {
    const { bot_id } = params;
    return request<{ success: boolean; message?: string; data?: any }>(
      `/api/desktop/bots/${bot_id}/open-folder`,
      {
        method: 'POST',
        ...(options || {}),
      },
    );
  },

  /**
   * 获取当前用户的 BOT 数量上限
   * GET /api/bots/ceiling
   */
  async getBotsCeiling(options?: { skipErrorHandler?: boolean }): Promise<{
    success: boolean;
    data?: { ceiling: number };
    message?: string;
  }> {
    return request('/api/bots/ceiling', {
      method: 'GET',
      skipErrorHandler: options?.skipErrorHandler ?? true,
    });
  },
};

// ======================== 导出 ========================

// 导出所有方法（兼容 import * as BotController 语法）
export const updateBot = BotControllerImpl.updateBot;
export const createBot = BotControllerImpl.createBot;
export const getBotsByOwner = BotControllerImpl.getBotsByOwner;
export const getBotsByEntity = BotControllerImpl.getBotsByEntity;
export const getBotDetail = BotControllerImpl.getBotDetail;
export const getBotStatus = BotControllerImpl.getBotStatus;
export const deleteBot = BotControllerImpl.deleteBot;
export const listBots = BotControllerImpl.listBots;
export const restartBot = BotControllerImpl.restartBot;
export const resetBot = BotControllerImpl.resetBot;
export const switchBot = BotControllerImpl.switchBot;
export const getBotConnection = BotControllerImpl.getBotConnection;
export const searchBotsByConditions = BotControllerImpl.searchBotsByConditions;
export const setBotPublic = BotControllerImpl.setBotPublic;
export const checkBotName = BotControllerImpl.checkBotName;
export const searchDomainBots = BotControllerImpl.searchDomainBots;
export const onboardBot = BotControllerImpl.onboardBot;
export const queryAgentPassport = BotControllerImpl.queryAgentPassport;
export const getBotAuthStatus = BotControllerImpl.getBotAuthStatus;
export const triggerBotDataInit = BotControllerImpl.triggerBotDataInit;
export const getDesktopDevices = BotControllerImpl.getDesktopDevices;
export const createDesktopBot = BotControllerImpl.createDesktopBot;
export const restartDesktopBot = BotControllerImpl.restartDesktopBot;
export const deleteDesktopBot = BotControllerImpl.deleteDesktopBot;
export const checkDesktopBotsStatus = BotControllerImpl.checkDesktopBotsStatus;
export const getDesktopBotAuthStatus =
  BotControllerImpl.getDesktopBotAuthStatus;
export const listDeviceFiles = BotControllerImpl.listDeviceFiles;
export const openBotFolder = BotControllerImpl.openBotFolder;
export const createDimaWorkspace = BotControllerImpl.createDimaWorkspace;
export const getBotsCeiling = BotControllerImpl.getBotsCeiling;

// 也导出整个对象（兼容直接导入）
export const BotController = BotControllerImpl;

// ======================== 工具函数 ========================

/**
 * Bot 连接信息（标准化结构）
 */
export interface BotConnectionInfo {
  target: string;
  token: string;
  type: 'local' | 'remote';
}

/**
 * 获取 Bot 连接信息（标准化封装）
 * 统一处理 API 调用 + 成功检查 + 字段标准化，调用方只需处理业务逻辑。
 * 失败时抛出 Error，调用方按需 catch。
 *
 * 协作场景：后端校验「非公开 Bot 只能获取本人设备的连接信息」，协作者操作他人 Bot
 * 时需传 ownerId（来自 bot.owner_id 或 resolveBotOwnerId(botId)），让后端按 Bot 归属鉴权。
 */
export async function fetchBotConnection(
  bindingId: number,
  options?: { timeout?: number; botId?: string; ownerId?: string },
): Promise<BotConnectionInfo> {
  const { botId, ownerId, ...restOptions } = options || {};
  const res = await BotControllerImpl.getBotConnection(
    { binding_id: bindingId, bot_id: botId, owner_id: ownerId },
    { skipErrorHandler: true, ...restOptions },
  );
  if (!res.success || !res.data) {
    throw new Error(res?.message || '获取连接信息失败');
  }
  return {
    target: res.data.target,
    token: res.data.token,
    type: res.data.type || 'remote',
  };
}
