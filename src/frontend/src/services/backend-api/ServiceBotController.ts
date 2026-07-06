/* eslint-disable */
/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * ServiceBot Controller - 服务型 Bot 发布相关 API
 * 从 BotController 拆分出的服务 Bot 发布管理接口
 */

import { request } from '@umijs/max';

// ======================== ServiceBot 发布相关类型 ========================

/**
 * 发布单状态
 */
export type PublishStatus = 'draft' | 'built' | 'success' | 'failed';

/**
 * 发布单信息
 */
export interface PublishRecord {
  id: number;
  source_bot_pk: number;
  source_bot_id: string;
  publish_bot_id: string;
  name: string;
  owner_id: string;
  status: PublishStatus;
  version: number;
  last_pub_id?: number;
  ext?: Record<string, any>;
}

/**
 * 推进发布流程请求参数
 * POST /api/service-bot/publish/process
 */
export interface ProcessPublishRequest {
  /** 发布单 ID */
  publish_id: number;
  /** 设备数量，默认 1 */
  device_count?: number;
}

/**
 * 推进发布流程响应
 */
export interface ProcessPublishResponse {
  success: boolean;
  message: string;
  data: {
    publish_id: number;
    status: PublishStatus;
    message: string;
    build_path?: string;
    bot_uuid?: string;
    device_binding_id?: number;
    data?: Record<string, any>;
  };
}

/**
 * 升级发布单请求参数
 * POST /api/service-bot/publish/upgrade
 */
export interface UpgradePublishRequest {
  /** 原发布单 ID */
  publish_id: number;
}

/**
 * 升级发布单响应
 */
export interface UpgradePublishResponse {
  success: boolean;
  message: string;
  data: PublishRecord;
}

/**
 * 查询发布记录详情响应
 * GET /api/service-bot/publish/{publish_id}
 */
export interface GetPublishDetailResponse {
  success: boolean;
  message: string;
  data: PublishRecord;
}

/**
 * 发布进度整体统计
 */
export interface PublishOverallProgress {
  total_devices: number;
  processed_devices: number;
  failed_devices: number;
  progress_percentage: number;
}

/**
 * BaaS 发布详情数据
 */
export interface BaasPublishData {
  publish_id: number;
  status: string;
  current_stage: string;
  overall_progress: PublishOverallProgress;
  device_details: any[];
  failed_devices: any[];
}

/**
 * 同步发布流程状态响应
 * POST /api/service-bot/publish/{publish_id}/sync
 */
export interface SyncPublishStatusResponse {
  success: boolean;
  message: string;
  error_code: null | number;
  data: {
    publish_id: number;
    status: string;
    message: string;
    build_path: string | null;
    bot_uuid: string;
    baas_publish_id: string;
    device_binding_id: number;
    data: BaasPublishData;
  };
}

// ======================== API 方法 ========================

/**
 * 推进发布流程
 * POST /api/service-bot/publish/process
 * 根据发布单当前状态执行对应阶段：
 * - DRAFT → 执行构建阶段 → BUILT
 * - BUILT → 执行发布阶段 → SUCCESS
 */
export async function processPublish(
  body: ProcessPublishRequest,
  options?: { [key: string]: any },
): Promise<ProcessPublishResponse> {
  return request<ProcessPublishResponse>('/api/service-bot/publish/process', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  });
}

/**
 * 升级发布单
 * POST /api/service-bot/publish/upgrade
 * 基于已成功的版本创建新草稿
 */
export async function upgradePublish(
  body: UpgradePublishRequest,
  options?: { [key: string]: any },
): Promise<UpgradePublishResponse> {
  return request<UpgradePublishResponse>('/api/service-bot/publish/upgrade', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    ...(options || {}),
  });
}

/**
 * 查询发布记录详情
 * GET /api/service-bot/publish/{publish_id}
 */
export async function getPublishDetail(
  params: { publish_id: number },
  options?: { [key: string]: any },
): Promise<GetPublishDetailResponse> {
  const { publish_id } = params;
  return request<GetPublishDetailResponse>(
    `/api/service-bot/publish/${publish_id}`,
    {
      method: 'GET',
      ...(options || {}),
    },
  );
}

/**
 * 同步发布流程状态
 * POST /api/service-bot/publish/{publish_id}/sync
 * 刷新发布流程状态，获取最新的发布进度
 */
export async function syncPublishStatus(
  params: { publish_id: number },
  options?: { [key: string]: any },
): Promise<SyncPublishStatusResponse> {
  const { publish_id } = params;
  return request<SyncPublishStatusResponse>(
    `/api/service-bot/publish/${publish_id}/sync`,
    {
      method: 'POST',
      ...(options || {}),
    },
  );
}

/**
 * 下线发布单
 * POST /api/service-bot/publish/{publish_id}/offline
 * 将已发布的 Bot 下线，退回到草稿状态
 */
export async function offlinePublish(
  params: { publish_id: number },
  options?: { [key: string]: any },
): Promise<any> {
  const { publish_id } = params;
  return request<any>(`/api/service-bot/publish/${publish_id}/offline`, {
    method: 'POST',
    ...(options || {}),
  });
}

/**
 * 重试发布流程
 * POST /api/service-bot/publish/{publish_id}/status
 * 失败状态下重试发布流程
 */
export async function retryPublish(
  params: { publish_id: number; target_status: string; source_status: string },
  options?: { [key: string]: any },
): Promise<any> {
  const { publish_id, ...body } = params;
  return request<any>(`/api/service-bot/publish/${publish_id}/status`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body,
    ...(options || {}),
  });
}

/**
 * 重试发布响应
 * POST /api/service-bot/publish/{publish_id}/retry
 */
export interface RetryPublishResponse {
  success: boolean;
  message: string;
  data: {
    publish_id: number;
    /** 重试后需要执行的操作: restart → 轮询 restart_status, sync → 轮询 sync */
    action: 'restart' | 'sync';
  };
}

/**
 * 重试发布（失败状态）
 * POST /api/service-bot/publish/{publish_id}/retry
 */
export async function retryPublishSimple(
  params: { publish_id: number },
  options?: { [key: string]: any },
): Promise<RetryPublishResponse> {
  const { publish_id } = params;
  return request<RetryPublishResponse>(
    `/api/service-bot/publish/${publish_id}/retry`,
    {
      method: 'POST',
      ...(options || {}),
    },
  );
}

/**
 * 重启发布单
 * POST /api/service-bot/publish/{publish_id}/restart
 */
export async function restartPublish(
  params: { publish_id: number },
  options?: { [key: string]: any },
): Promise<any> {
  const { publish_id } = params;
  return request<any>(`/api/service-bot/publish/${publish_id}/restart`, {
    method: 'POST',
    ...(options || {}),
  });
}

/**
 * 查询重启发布状态
 * POST /api/service-bot/publish/{publish_id}/restart_status
 */
export async function getRestartPublishStatus(
  params: { publish_id: number },
  options?: { [key: string]: any },
): Promise<any> {
  const { publish_id } = params;
  return request<any>(`/api/service-bot/publish/${publish_id}/restart_status`, {
    method: 'POST',
    ...(options || {}),
  });
}

/**
 * 升级 Bot 为服务型
 * POST /api/service-bot/publish/upgrade_bot_type
 */
export interface UpgradeBotTypeRequest {
  /** Bot ID */
  bot_id: string;
}

/**
 * 升级后的 Bot 信息
 */
export interface UpgradedBotInfo {
  bot_id: string;
  bot_type: 'service';
}

/**
 * 升级后创建的发布单（可能为 null）
 */
export interface UpgradePublishRecord {
  id: number;
  bot_id: string;
  owner_id: string;
  name: string;
  status: PublishStatus;
}

/**
 * 升级 Bot 为服务型响应
 */
export interface UpgradeBotTypeResponse {
  success: boolean;
  message: string;
  error_code: number | null;
  data: {
    bot: UpgradedBotInfo;
    publish_record: UpgradePublishRecord | null;
  } | null;
}

/**
 * 升级 Bot 为服务型
 * POST /api/service-bot/publish/upgrade_bot_type
 * 将个人 Bot 升级为服务型 Bot
 */
export async function upgradeBotType(
  body: UpgradeBotTypeRequest,
  options?: { [key: string]: any },
): Promise<UpgradeBotTypeResponse> {
  return request<UpgradeBotTypeResponse>(
    '/api/service-bot/publish/upgrade_bot_type',
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      data: body,
      ...(options || {}),
    },
  );
}

/**
 * 删除服务型 Bot 响应
 */
export interface DeleteServiceBotResponse {
  success: boolean;
  message: string;
  error_code: number | null;
  data: {
    deleted: boolean;
  } | null;
}

/**
 * 删除服务型 Bot
 * POST /api/service-bot/publish/{publish_id}/delete
 * 仅限草稿状态且无其他成功发布记录的服务 Bot
 */
export async function deleteServiceBot(
  params: { publish_id: number },
  options?: { [key: string]: any },
): Promise<DeleteServiceBotResponse> {
  const { publish_id } = params;
  return request<DeleteServiceBotResponse>(
    `/api/service-bot/publish/${publish_id}/delete`,
    {
      method: 'POST',
      ...(options || {}),
    },
  );
}

// ======================== 协作 & 用户权限相关类型 ========================
// 文档：内部接口文档

/**
 * 协作者角色
 * 后端 admin/member 二级角色；前端当前按二元处理（添加时统一传 admin）
 */
export type CollaboratorRole = 'admin' | 'member';

/**
 * 权限级别（用于 check_permission）
 */
export type PermissionLevel = 'NONE' | 'ADMIN' | 'OWNER';

/**
 * 协作者信息
 * 注意：update/remove 操作必须使用 `id`（主键），不能用 user_id
 */
export interface CollaboratorInfo {
  id: number;
  bot_pk: number;
  bot_id: string;
  owner_id: string;
  user_id: string;
  user_name: string | null;
  role: CollaboratorRole;
  operator_id: string;
  gmt_create: string;
  gmt_modified?: string;
}

/**
 * 锁信息
 * 后端已直接返回持锁人花名 holder_name；展示时优先用 holder_name，缺失时降级为 holder_user_id
 */
export interface LockInfo {
  id: number;
  /** 形如 "bot_id:owner_id" */
  lock_key: string;
  holder_user_id: string;
  /** 持锁人花名（后端返回；老数据可能缺失，展示时降级到 holder_user_id） */
  holder_name?: string | null;
  gmt_create: string;
}

/** 统一响应 */
interface ApiResponse<T> {
  success: boolean;
  message: string;
  error_code: number;
  data: T | null;
}

// ----- 协作者管理 -----

export interface AddCollaboratorRequest {
  bot_id: string;
  owner_id: string;
  user_id: string;
  user_name?: string;
  role?: CollaboratorRole;
}
export type AddCollaboratorResponse = ApiResponse<CollaboratorInfo>;

export interface ListCollaboratorsParams {
  bot_id: string;
  owner_id: string;
  role?: CollaboratorRole;
}
export type ListCollaboratorsResponse = ApiResponse<{
  collaborators: CollaboratorInfo[];
}>;

export interface UpdateCollaboratorRequest {
  id: number;
  user_id?: string;
  user_name?: string;
  role?: CollaboratorRole;
}
export type UpdateCollaboratorResponse = ApiResponse<CollaboratorInfo>;

export interface RemoveCollaboratorRequest {
  id: number;
}
export type RemoveCollaboratorResponse = ApiResponse<{ deleted: boolean }>;

export interface CheckPermissionRequest {
  bot_id: string;
  owner_id: string;
  user_id?: string;
  required_level?: PermissionLevel;
}
export type CheckPermissionResponse = ApiResponse<{
  has_permission: boolean;
  level: PermissionLevel;
  level_value: number;
}>;

// ----- 协作锁 -----

export interface AcquireLockRequest {
  bot_id: string;
  owner_id: string;
}
/**
 * 抢锁响应：
 * - success=true & data.acquired=true：抢锁成功（自己持锁）
 * - success=true & data.acquired=false：锁被他人持有（data.lock 含持锁人）
 * 重入：同一用户重复 acquire 也返回 acquired=true
 */
export type AcquireLockResponse = ApiResponse<{
  acquired: boolean;
  lock: LockInfo | null;
}>;

export interface ReleaseLockRequest {
  bot_id: string;
  owner_id: string;
  /** 强制释放（管理员场景），默认 false */
  force?: boolean;
}
export type ReleaseLockResponse = ApiResponse<{ released: boolean }>;

export interface LockInfoParams {
  bot_id: string;
  owner_id: string;
}
export type GetLockInfoResponse = ApiResponse<{
  locked: boolean;
  lock: LockInfo | null;
  has_collaborators: boolean;
  is_owner: boolean;
  need_lock: boolean;
}>;

export interface StealLockRequest {
  bot_id: string;
  owner_id: string;
}
export type StealLockResponse = ApiResponse<{
  stolen: boolean;
  lock: LockInfo | null;
}>;

// ----- 沙箱只读规则 -----

export type ReadOnlyRuleType = 'file' | 'directory' | 'glob';

/** 只读规则项（用户自定义 / 系统默认） */
export interface ReadOnlyRule {
  path: string; // 绝对路径
  rule_type: ReadOnlyRuleType;
}

/** 目录树节点 */
export interface ReadOnlyTreeNode {
  name: string;
  /** 相对 base_path 的相对路径 */
  path: string;
  is_dir: boolean;
  is_readonly_default: boolean;
  is_readonly_custom: boolean;
}

export interface GetReadOnlyTreeParams {
  bot_id: string;
  /** 协作场景下按 owner 归属鉴权 */
  owner_id?: string;
  /** 子目录相对路径；不传或空字符串表示根目录 */
  path?: string;
  /** 递归查询全量，默认 false */
  recursive?: boolean;
}
export type GetReadOnlyTreeResponse = ApiResponse<{
  base_path: string;
  items: ReadOnlyTreeNode[];
  default_rules: ReadOnlyRule[];
  custom_rules: ReadOnlyRule[];
}>;

// ======================== 协作 & 用户权限 API 方法 ========================

/**
 * 添加协作者
 * POST /api/bot/collaborator/add
 */
export async function addCollaborator(
  body: AddCollaboratorRequest,
  options?: { [key: string]: any },
): Promise<AddCollaboratorResponse> {
  return request<AddCollaboratorResponse>('/api/bot/collaborator/add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    data: body,
    ...(options || {}),
  });
}

/**
 * 协作者列表
 * GET /api/bot/collaborator/list?bot_id=&owner_id=&role=
 */
export async function listCollaborators(
  params: ListCollaboratorsParams,
  options?: { [key: string]: any },
): Promise<ListCollaboratorsResponse> {
  return request<ListCollaboratorsResponse>('/api/bot/collaborator/list', {
    method: 'GET',
    params,
    ...(options || {}),
  });
}

/**
 * 更新协作者
 * POST /api/bot/collaborator/update
 */
export async function updateCollaborator(
  body: UpdateCollaboratorRequest,
  options?: { [key: string]: any },
): Promise<UpdateCollaboratorResponse> {
  return request<UpdateCollaboratorResponse>('/api/bot/collaborator/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    data: body,
    ...(options || {}),
  });
}

/**
 * 移除协作者
 * POST /api/bot/collaborator/remove
 */
export async function removeCollaborator(
  body: RemoveCollaboratorRequest,
  options?: { [key: string]: any },
): Promise<RemoveCollaboratorResponse> {
  return request<RemoveCollaboratorResponse>('/api/bot/collaborator/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    data: body,
    ...(options || {}),
  });
}

/**
 * 检查用户权限
 * POST /api/bot/collaborator/check_permission
 */
export async function checkPermission(
  body: CheckPermissionRequest,
  options?: { [key: string]: any },
): Promise<CheckPermissionResponse> {
  return request<CheckPermissionResponse>(
    '/api/bot/collaborator/check_permission',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: body,
      ...(options || {}),
    },
  );
}

/**
 * 抢锁
 * POST /api/bot/collaborator/lock/acquire
 * 注意：锁被他人持有时仍返回 success=true，需通过 data.acquired 判断
 */
export async function acquireLock(
  body: AcquireLockRequest,
  options?: { [key: string]: any },
): Promise<AcquireLockResponse> {
  return request<AcquireLockResponse>('/api/bot/collaborator/lock/acquire', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    data: body,
    ...(options || {}),
  });
}

/**
 * 释放锁
 * POST /api/bot/collaborator/lock/release
 */
export async function releaseLock(
  body: ReleaseLockRequest,
  options?: { [key: string]: any },
): Promise<ReleaseLockResponse> {
  return request<ReleaseLockResponse>('/api/bot/collaborator/lock/release', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    data: body,
    ...(options || {}),
  });
}

/**
 * 获取锁信息
 * GET /api/bot/collaborator/lock/info?bot_id=&owner_id=
 */
export async function getLockInfo(
  params: LockInfoParams,
  options?: { [key: string]: any },
): Promise<GetLockInfoResponse> {
  return request<GetLockInfoResponse>('/api/bot/collaborator/lock/info', {
    method: 'GET',
    params,
    ...(options || {}),
  });
}

/**
 * 强制抢锁
 * POST /api/bot/collaborator/lock/steal
 * 需要 ADMIN 权限或 Owner 身份
 */
export async function stealLock(
  body: StealLockRequest,
  options?: { [key: string]: any },
): Promise<StealLockResponse> {
  return request<StealLockResponse>('/api/bot/collaborator/lock/steal', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    data: body,
    ...(options || {}),
  });
}

/**
 * 沙箱只读规则：查询目录树
 * GET /api/service-bot/read-only/tree?bot_id=&path=&recursive=
 * 返回当前目录下的节点 + 默认规则 + 自定义规则
 */
export async function getReadOnlyTree(
  params: GetReadOnlyTreeParams,
  options?: { [key: string]: any },
): Promise<GetReadOnlyTreeResponse> {
  return request<GetReadOnlyTreeResponse>('/api/service-bot/read-only/tree', {
    method: 'GET',
    params,
    ...(options || {}),
  });
}

/**
 * 保存自定义只读规则
 * PUT /api/bots/{bot_id}（按文档：通过 updateBot 写入 ext.read_only_rules）
 *
 * 请求体形如 { ext: { read_only_rules: [...] } }；后端按 PermissionLevel.OWNER 鉴权
 * （只有 bot owner 能改，admin 协作者也不可）。协作场景下需要前端注入 owner_id，
 * 让后端按 bot 归属鉴权。
 * 文档：内部接口文档
 */
export async function saveReadOnlyRules(
  params: { bot_id: string; owner_id?: string; rules: ReadOnlyRule[] },
  options?: { [key: string]: any },
): Promise<ApiResponse<any>> {
  const { bot_id, owner_id, rules } = params;
  return request<ApiResponse<any>>(`/api/bots/${bot_id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    ...(owner_id ? { params: { owner_id } } : {}),
    data: { ext: { read_only_rules: rules } },
    ...(options || {}),
  });
}

/**
 * 获取发布单的引擎配置
 * GET /api/service-bot/publish/{publish_id}/engine-config
 */
export async function getPublishEngineConfig(
  params: { publish_id: number },
  options?: { [key: string]: any },
): Promise<{ success: boolean; data: Record<string, any> }> {
  const { publish_id } = params;
  return request<{ success: boolean; data: Record<string, any> }>(
    `/api/service-bot/publish/${publish_id}/engine-config`,
    {
      method: 'GET',
      ...(options || {}),
    },
  );
}

// ======================== 发布回滚相关类型 ========================

/**
 * 检查是否可以回滚响应 data
 * GET /api/service-bot/publish/{publish_id}/can-rollback
 */
export interface CanRollbackData {
  can_rollback: boolean;
  reason?: string | null;
  target_publish_id?: number;
  target_version?: number;
  target_status?: string;
  rollback_restored_from?: number;
  next_publish_id?: number;
  next_version?: number;
  next_status?: string;
}

export type CheckCanRollbackResponse = ApiResponse<CanRollbackData>;

/**
 * 执行回滚响应 data
 * POST /api/service-bot/publish/{publish_id}/rollback
 */
export interface RollbackResultData {
  rolled_back_publish_id: number;
  rolled_back_status: string;
  target_publish_id: number;
  target_version: number;
  target_status: string;
  deploy_status?: string;
  deploy_message?: string;
}

export type RollbackPublishResponse = ApiResponse<RollbackResultData>;

// ======================== 发布回滚 API 方法 ========================

/**
 * 检查是否可以回滚
 * GET /api/service-bot/publish/{publish_id}/can-rollback
 * 返回可回滚性 + 目标版本信息 + 不可回滚原因
 */
export async function checkCanRollback(
  params: { publish_id: number },
  options?: { [key: string]: any },
): Promise<CheckCanRollbackResponse> {
  const { publish_id } = params;
  return request<CheckCanRollbackResponse>(
    `/api/service-bot/publish/${publish_id}/can-rollback`,
    {
      method: 'GET',
      ...(options || {}),
    },
  );
}

/**
 * 执行发布回滚
 * POST /api/service-bot/publish/{publish_id}/rollback
 * 将当前 SUCCESS 版本回滚到上一个版本（当前版本变为 DRAFT，上一版本恢复为 SUCCESS）
 */
export async function rollbackPublish(
  params: { publish_id: number },
  options?: { [key: string]: any },
): Promise<RollbackPublishResponse> {
  const { publish_id } = params;
  return request<RollbackPublishResponse>(
    `/api/service-bot/publish/${publish_id}/rollback`,
    {
      method: 'POST',
      ...(options || {}),
    },
  );
}

// 控制器对象（兼容 BotController 的使用方式）
export const ServiceBotController = {
  processPublish,
  upgradePublish,
  getPublishDetail,
  syncPublishStatus,
  offlinePublish,
  retryPublish,
  retryPublishSimple,
  restartPublish,
  getRestartPublishStatus,
  upgradeBotType,
  deleteServiceBot,
  addCollaborator,
  listCollaborators,
  updateCollaborator,
  removeCollaborator,
  checkPermission,
  acquireLock,
  releaseLock,
  getLockInfo,
  getReadOnlyTree,
  saveReadOnlyRules,
  getPublishEngineConfig,
  checkCanRollback,
  rollbackPublish,
};
