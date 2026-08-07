/**
 * BcsfuseController - Bot 智能融合 API 控制器
 *
 * 封装 BCS Fuse 服务相关的 HTTP API 调用
 * 所有接口统一使用 /bcnfuse 前缀（代理到 bcsfuse 服务）
 */

import { request } from '@umijs/max';

// === 请求/响应类型 ===

/** 融合问答请求参数 */
export interface FuseRequestParams {
  /** 会话 ID：用于前端本地消息隔离；后端兼容接收但按 group_id 聚合上下文 */
  session_id: string;
  /** 用户输入的问题 */
  question: string;
  /** 发起 bot 的 id */
  driver_bot_id: string;
  /** 参与融合的 bot ID 列表 */
  participants: string[];
  /** 融合模式，固定为 bot_profile_fuse */
  fusion_mode: 'bot_profile_fuse';
  /** 选项 */
  options: {
    /** 超时时间（毫秒） */
    timeout_ms: number;
    /** 是否强制刷新缓存 */
    refresh?: boolean;
  };
}

/** 融合问答推荐结果 */
export interface FuseRecommendation {
  /** 融合后的回答摘要（Markdown 格式） */
  summary: string;
  /** 决策（yes/no） */
  decision?: string;
}

/** 融合问答耗时统计 */
export interface FuseTiming {
  /** Profile 融合耗时（秒） */
  profile_fusion?: number;
  /** 会话上下文处理耗时（秒） */
  group_conversation?: number;
  /** LLM 生成耗时（秒） */
  llm_generation?: number;
  /** 总耗时统计 */
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
}

/** 融合问答响应 */
export interface FuseResponse {
  /** 群组 ID */
  group_id?: string;
  /** 会话 ID */
  session_id?: string;
  /** 融合 ID */
  fusion_id?: string;
  /** 原始问题 */
  question?: string;
  /** 发起 bot ID */
  driver_bot_id?: string;
  /** 推荐结果 */
  recommendation: FuseRecommendation;
  /** 是否部分成功 */
  partial_success?: boolean;
  /** 警告信息 */
  warnings?: string[];
  /** 错误信息 */
  errors?: string[];
  /** 耗时统计 */
  timing?: FuseTiming;
  /** 融合模式 */
  fusion_mode?: string;
  /** 扩展结果（融合 profile 等） */
  extend_result?: {
    fused_profile?: Record<string, any>;
    group_conversation?: Record<string, any>;
    timing?: FuseTiming;
  };
  /** 业务错误标记 */
  success?: boolean;
  error?: string;
}

// === API 接口 ===

/**
 * 调用 Bot 智能融合接口
 * POST /bcnfuse/api/v1/groups/{group_id}/fuse
 */
export async function postFuse(
  params: { group_id: string } & FuseRequestParams,
  options?: { [key: string]: any },
) {
  // 后端已兼容 session_id，前端内外保持同一请求体，避免同步时产生差异。
  const { group_id, ...body } = params;
  return request<FuseResponse>(`/bcnfuse/api/v1/groups/${group_id}/fuse`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    data: body,
    skipErrorHandler: true,
    ...(options || {}),
  });
}

// === Worker Config 接口 ===

/** 获取 Worker 配置响应 */
export interface GetWorkerConfigResponse {
  success: boolean;
  worker_id: string;
  fusion_enable: boolean;
  /** 乐观锁版本号 */
  version: number;
}

/**
 * 获取 Worker 配置
 * GET /bcnfuse/v1/workers/{worker_id}/config
 */
export async function getWorkerConfig(
  params: { worker_id: string },
  options?: { [key: string]: any },
) {
  const { worker_id } = params;
  return request<GetWorkerConfigResponse>(
    `/bcnfuse/v1/workers/${worker_id}/config`,
    {
      method: 'GET',
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}

/** 更新 Worker 配置响应 */
export interface UpdateWorkerConfigResponse {
  success: boolean;
  worker_id: string;
  fusion_enable: boolean;
  version: number;
}

/**
 * 修改 Worker 配置
 * PUT /bcnfuse/v1/workers/{worker_id}/config
 */
export async function updateWorkerConfig(
  params: {
    worker_id: string;
    fusion_enable: boolean;
    version?: number;
    updated_by?: string;
  },
  options?: { [key: string]: any },
) {
  const { worker_id, ...body } = params;
  return request<UpdateWorkerConfigResponse>(
    `/bcnfuse/v1/workers/${worker_id}/config`,
    {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      data: body,
      skipErrorHandler: true,
      ...(options || {}),
    },
  );
}
