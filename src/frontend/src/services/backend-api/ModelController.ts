/* eslint-disable */
// 该文件由 OneAPI 自动生成，请勿手动修改！
import { request } from '@umijs/max';

/** 模型定价信息 */
export interface ModelPricing {
  input_price?: number;
  output_price?: number;
  cache_read_price?: number;
  cache_write_price?: number;
}

/** 模型能力信息 */
export interface ModelCapabilities {
  context_window?: number;
  max_output_tokens?: number;
  vision?: boolean;
  function_calling?: boolean;
  reasoning?: boolean;
  streaming?: boolean;
  json_mode?: boolean;
}

/** 模型信息 */
export interface Model {
  /** 模型ID，统一格式如 "openai/gpt-5.3" */
  id?: string;
  /** Provider ID */
  provider_id?: string;
  /** Provider名称 */
  provider?: string;
  /** 模型名称 */
  name?: string;
  /** 显示名称 */
  display_name?: string;
  /** 模型描述 */
  description?: string;
  /** 企业版是否启用 */
  enterprise_enabled?: boolean;
  /** 是否为企业版默认模型 */
  enterprise_default?: boolean;
  /** 模型能力 */
  capabilities?: ModelCapabilities;
  /** 模型定价 */
  pricing?: ModelPricing;
}

/** 模型列表响应 */
export interface ModelsListResponse {
  /** 模型列表 */
  models?: Model[];
  /** 总数 */
  total?: number;
}

/** 基础API响应 */
export interface ApiResponse {
  /** 是否成功 */
  success: boolean;
  /** 响应数据 */
  data?: any;
  /** 消息 */
  message?: string;
}

/** 获取可用模型列表 GET /api/models */
export async function listModels(options?: { [key: string]: any }) {
  return request<API.Result_ModelsListResponse_>('/api/models', {
    method: 'GET',
    ...(options || {}),
  });
}

/** 获取单个模型详情 GET /api/models/${param0} */
export async function getModel(
  params: {
    // path
    /** 模型 ID，统一格式如 "openai/gpt-5.3" */
    modelId: string;
  },
  options?: { [key: string]: any },
) {
  const { modelId: param0 } = params;
  return request<API.Result>(`/api/models/${param0}`, {
    method: 'GET',
    ...(options || {}),
  });
}
