/**
 * Hooks 错误处理工具
 * 提供统一的错误信息提取和提示功能，供业务 Hooks 使用
 *
 * 使用场景：
 * - 业务 Hooks 中处理 API 错误
 * - 将简单的错误提示优化为包含后端详细错误信息的格式
 */

import { toast } from 'sonner';
import { isLocalDev } from './platform';
import {
  extractErrorMessage,
  extractFriendlyErrorMessage,
} from './requestErrorHandler';

// 懒加载 showErrorPrompt 避免循环依赖
let showErrorPrompt: any = null;

/**
 * 获取强提示函数（懒加载）
 */
async function getErrorPrompt() {
  if (!showErrorPrompt && isLocalDev()) {
    const module = await import(
      '../pages/Bootstrap/ErrorPrompt/errorPromptState'
    );
    showErrorPrompt = module.showErrorPrompt;
  }
  return showErrorPrompt;
}

export interface HandleHookErrorOptions {
  /** 业务模块名称，用于标识错误来源 */
  module: string;
  /** 操作名称，如"删除"、"更新"、"创建" */
  action: string;
  /** 是否显示 toast，默认 true */
  showToast?: boolean;
  /** 是否在本地开发环境使用强提醒，默认 false */
  usePrompt?: boolean;
  /** 兜底错误文案，默认使用 {action}失败 */
  fallback?: string;
}

/**
 * 处理 API 响应中的业务错误（success: false）
 *
 * @param response API 响应对象
 * @param options 配置选项
 * @returns 格式化后的错误信息，如果没有错误返回 null
 *
 * @example
 * const errorMsg = handleApiError(response, { module: 'SkillMarket', action: '删除' });
 * if (errorMsg) return false; // 有错误，提前返回
 */
export function handleApiError(
  response: any,
  options: HandleHookErrorOptions,
): string | null {
  if (response?.success !== false) return null;

  const fallback = options.fallback || `${options.action}失败`;
  const backendMsg = extractErrorMessage(response, '');

  // 优化格式：操作失败: 后端详细错误信息
  const errorMsg = backendMsg ? `${fallback}: ${backendMsg}` : fallback;

  if (options.showToast !== false) {
    toast.error(errorMsg);
  }

  return errorMsg;
}

/**
 * 处理网络/HTTP 错误（catch 块中）
 *
 * @param error 捕获的错误对象
 * @param options 配置选项
 * @returns 格式化后的错误信息
 *
 * @example
 * try {
 *   await apiCall();
 * } catch (error) {
 *   const errorMsg = handleNetworkError(error, { module: 'SkillMarket', action: '删除' });
 *   return false;
 * }
 */
export function handleNetworkError(
  error: any,
  options: HandleHookErrorOptions,
): string {
  const fallback = options.fallback || `${options.action}失败`;

  // 提取后端返回的详细错误信息
  const backendMsg = extractErrorMessage(
    error?.data ?? error?.response?.data ?? error,
    '',
  );

  // 优先使用后端详细错误，其次使用友好错误信息
  const friendlyMsg = extractFriendlyErrorMessage(error, '');
  const errorDetail = backendMsg || friendlyMsg;

  // 优化格式：操作失败: 后端详细错误信息
  const errorMsg = errorDetail ? `${fallback}: ${errorDetail}` : fallback;

  if (options.showToast !== false) {
    // 本地开发环境且开启强提醒时使用 ErrorPrompt
    if (options.usePrompt && isLocalDev()) {
      getErrorPrompt().then((prompt: any) => {
        if (prompt) {
          prompt({
            title: `[${options.module}] ${options.action}失败`,
            message: errorDetail || error?.message || '网络请求异常',
            dismissible: true,
          });
        } else {
          toast.error(errorMsg);
        }
      });
    } else {
      toast.error(errorMsg);
    }
  }

  return errorMsg;
}

/**
 * 处理完整的 API 调用错误（包括业务错误和 HTTP 错误）
 * 组合 handleApiError 和 handleNetworkError
 *
 * @param responseOrError API 响应对象或捕获的错误
 * @param options 配置选项
 * @returns 包含是否成功、错误信息的对象
 *
 * @example
 * try {
 *   const response = await apiCall();
 *   const result = handleApiResponse(response, { module: 'SkillMarket', action: '删除' });
 *   return result.success;
 * } catch (error) {
 *   const result = handleApiResponse(error, { module: 'SkillMarket', action: '删除' });
 *   return result.success;
 * }
 */
export function handleApiResponse(
  responseOrError: any,
  options: HandleHookErrorOptions,
): { success: boolean; errorMsg: string | null } {
  // 判断是响应对象还是错误对象
  const isResponse = responseOrError?.success !== undefined;

  if (isResponse) {
    // 业务错误
    const errorMsg = handleApiError(responseOrError, options);
    return { success: !errorMsg, errorMsg };
  } else {
    // 网络/HTTP 错误
    const errorMsg = handleNetworkError(responseOrError, options);
    return { success: false, errorMsg };
  }
}

/**
 * 生成成功提示
 *
 * @param options 配置选项
 * @param customMsg 自定义成功消息，可选
 */
export function showSuccessToast(
  options: HandleHookErrorOptions,
  customMsg?: string,
): void {
  const msg = customMsg || `${options.action}成功`;
  toast.success(msg);
}
