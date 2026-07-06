/* eslint-disable */
// Bot CDN（CDN 页面）管理 API 服务
import { request } from '@umijs/max';

const BASE = '/api';

/** Bot CDN记录 */
export interface BotRenderScreen {
  /** CDN ID */
  id: number;
  /** Bot ID */
  bot_id: string;
  /** CDN 名称（库名，如 'asfui'） */
  name: string;
  /** CDN URL */
  cdn_url: string;
  /** 创建者ID */
  creator_id: string;
  /** 创建时间 */
  gmt_create: string;
  /** 修改时间 */
  gmt_modified: string;
}

/** 创建CDN请求参数 */
export interface CreateBotRenderScreenParams {
  /** Bot ID */
  bot_id: string;
  /** CDN 名称（库名，如 'asfui'） */
  name: string;
  /** CDN URL */
  cdn_url: string;
}

/** 创建CDN响应 */
export interface CreateBotRenderScreenResponse {
  success: boolean;
  data?: {
    /** CDN ID */
    id: number;
  };
  message?: string;
}

/** 更新CDN请求参数 */
export interface UpdateBotRenderScreenParams {
  /** Bot ID */
  bot_id: string;
  /** CDN 名称（库名，如 'asfui'） */
  name: string;
  /** CDN URL */
  cdn_url: string;
}

/** 更新CDN响应 */
export interface UpdateBotRenderScreenResponse {
  success: boolean;
  data?: {
    /** CDN ID */
    id: number;
  };
  message?: string;
}

/** CDN列表响应 */
export interface BotRenderScreenListResponse {
  success: boolean;
  data?: BotRenderScreen[];
  message?: string;
}

/** 删除CDN响应 */
export interface BotRenderScreenDeleteResponse {
  success: boolean;
  message?: string;
}

/** 查询CDN列表参数 */
export interface ListBotRenderScreensParams {
  /** Bot ID */
  bot_id: string;
}

/**
 * 查询 Bot CDN列表
 * @param params 查询参数
 * @param options 可选配置
 */
export async function listBotRenderScreens(
  params: ListBotRenderScreensParams,
  options?: { skipErrorHandler?: boolean },
): Promise<BotRenderScreen[]> {
  const res = await request<BotRenderScreenListResponse>(
    `${BASE}/bot-render-screens`,
    {
      method: 'GET',
      params,
      skipErrorHandler: options?.skipErrorHandler,
    },
  );
  if (!res.success) throw new Error(res.message || '获取CDN列表失败');
  return res.data || [];
}

/**
 * 创建 Bot CDN
 * @param params 创建参数
 * @param options 可选配置
 * @returns 创建成功返回CDN ID
 */
export async function createBotRenderScreen(
  params: CreateBotRenderScreenParams,
  options?: { skipErrorHandler?: boolean },
): Promise<number> {
  const res = await request<CreateBotRenderScreenResponse>(
    `${BASE}/bot-render-screens`,
    {
      method: 'POST',
      data: params,
      skipErrorHandler: options?.skipErrorHandler,
    },
  );
  if (!res.success) throw new Error(res.message || '创建CDN失败');
  if (!res.data) throw new Error('创建CDN返回数据为空');
  return res.data.id;
}

/**
 * 更新 Bot CDN
 * @param id CDN ID
 * @param params 更新参数
 * @param options 可选配置
 * @returns 更新成功返回CDN ID
 */
export async function updateBotRenderScreen(
  id: number,
  params: UpdateBotRenderScreenParams,
  options?: { skipErrorHandler?: boolean },
): Promise<number> {
  const res = await request<UpdateBotRenderScreenResponse>(
    `${BASE}/bot-render-screens/${id}`,
    {
      method: 'PUT',
      data: params,
      skipErrorHandler: options?.skipErrorHandler,
    },
  );
  if (!res.success) throw new Error(res.message || '更新CDN失败');
  if (!res.data) throw new Error('更新CDN返回数据为空');
  return res.data.id;
}

/**
 * 删除 Bot CDN
 * @param id CDN ID
 * @param options 可选配置
 */
export async function deleteBotRenderScreen(
  id: number,
  options?: { skipErrorHandler?: boolean },
): Promise<void> {
  const res = await request<BotRenderScreenDeleteResponse>(
    `${BASE}/bot-render-screens/${id}`,
    {
      method: 'DELETE',
      skipErrorHandler: options?.skipErrorHandler,
    },
  );
  if (!res.success) throw new Error(res.message || '删除CDN失败');
}
