/**
 * 请求错误处理工具
 * 负责生成友好的错误提示信息和显示错误对话框
 */

/**
 * 从后端响应中提取错误信息，兼容多种不规范的响应结构：
 * - { success: false, message: "..." }
 * - { success: false, error: "..." }
 * - { success: false, msg: "..." }
 * - { code: 500, error: "..." }（部分设备代理接口）
 */
export function extractErrorMessage(error: any, fallback = '操作失败'): string {
  const data = error?.data ?? error?.response?.data ?? error;
  return (
    data?.message ||
    data?.error ||
    data?.msg ||
    data?.detail ||
    error?.message ||
    data?.errorMsg ||
    data?.error_msg ||
    fallback
  );
}

/**
 * 从错误对象中提取用户友好的错误信息
 * 比 extractErrorMessage 增强：HTTP 状态码 → 中文文案、网络错误识别
 * 适合直接展示给用户的场景（如 Bootstrap 初始化、轮询）
 */
export function extractFriendlyErrorMessage(
  error: unknown,
  fallback: string,
): string {
  if (!(error instanceof Error)) return fallback;

  const err = error as any;
  const status: number | undefined = err?.response?.status;

  // 1. 优先取后端返回的业务错误信息
  const data = err?.data ?? err?.response?.data;
  const backendMsg: string =
    data?.message || data?.error || data?.msg || data?.detail || '';
  if (backendMsg) return backendMsg;

  // 2. HTTP 状态码 → 友好文案
  if (status) {
    if (status === 401) return '登录已过期，请刷新页面后重试';
    if (status === 403) return '账号暂无访问权限，请联系管理员';
    if (status === 404) return '服务暂时不可用，请稍后重试';
    if (status === 429) return '请求过于频繁，请稍后重试';
    if (status >= 500) return '服务器暂时异常，请稍后重试';
    return `请求失败，请稍后重试（${status}）`;
  }

  // 3. 网络层错误
  if (err?.code === 'ECONNABORTED' || err?.message?.includes('timeout')) {
    return '请求超时，请检查网络后重试';
  }
  if (err?.message?.includes('Network Error')) {
    return '网络连接异常，请检查网络后重试';
  }

  // 4. 兜底：error.message（纯 Error 对象，如 throw new Error(response.message)）
  if (err?.message) return err.message;

  return fallback;
}

/**
 * 从完整 URL 中提取可读的 API 路径，去除代理前缀和 host
 * - /proxypass/127.0.0.1:20001/api/sessions → /api/sessions
 * - http://127.0.0.1:20001/api/engine/status → /api/engine/status
 * - /api/bots/xxx → /api/bots/xxx（不变）
 */
export function formatApiPath(url: string): string {
  if (!url) return '未知接口';
  // 去除 http(s)://host:port 前缀
  let path = url.replace(/^https?:\/\/[^/]+/, '');
  // 去除 /proxypass/{host:port} 前缀
  path = path.replace(/^\/proxypass\/[^/]+/, '');
  return path || url;
}
import { toast } from 'sonner';
import { showErrorPrompt } from '../pages/Bootstrap/ErrorPrompt/errorPromptState';
import { getServers } from './env';
import { isLocalDev } from './platform';

/**
 * 将请求路径解析为实际的后端服务器完整 URL
 * - 绝对 URL（http(s)://...）→ 直接返回
 * - /proxypass/... → SESSION 服务器
 * - /asfagent/... → ASFAGENT 服务器
 * - /api/... 等 → MANAGEMENT 服务器
 */
function resolveFullUrl(url: string): string {
  if (/^https?:\/\//.test(url)) return url;

  const servers = getServers();

  if (url.startsWith('/proxypass')) {
    return `${servers.SESSION}${url}`;
  }
  if (url.startsWith('/asfagent')) {
    return `${servers.ASFAGENT}${url}`;
  }
  return `${servers.MANAGEMENT}${url}`;
}

/**
 * 根据接口路径和错误信息生成友好的错误提示
 */
function generateErrorMessage(
  url: string,
  error: any,
  status?: number,
): string {
  const baseMessage = status ? `请求失败 [${status}]` : '网络异常';

  // 非本地开发环境，返回简化错误信息
  if (!isLocalDev()) {
    return baseMessage;
  }

  // 本地开发环境，提供详细的错误提示和解决方案
  const suggestions: string[] = [];

  // 显示实际请求的完整地址（含 host）
  suggestions.push(`请求地址: ${resolveFullUrl(url)}`);

  // 根据接口路径提供针对性建议
  if (url.includes('/api/v1/devices') || url.includes('/api/v1/brain')) {
    suggestions.push('请确认本地管理后端服务已启动');
  } else if (url.includes('/proxypass')) {
    suggestions.push('请确认 Bot 代理服务正常');
  } else if (url.includes('/access')) {
    suggestions.push('本地环境可能未实现访问控制');
  } else if (url.includes('/asfagent')) {
    suggestions.push('请确认 ASF Agent 服务可访问');
  } else if (url.startsWith('/api')) {
    suggestions.push('请确认本地管理后端服务已启动');
  }

  // 添加错误详情
  const errorDetail = error?.message || error?.code || '';
  if (errorDetail) {
    suggestions.push(`错误详情: ${errorDetail}`);
  }

  // 构建完整错误消息
  return [baseMessage, ...suggestions].join('\n');
}

/**
 * 显示请求错误提示
 * 本地开发环境：使用对话框强提醒
 * 其他环境：使用 toast 简单提示
 */
export function showRequestError(
  url: string,
  error: any,
  status?: number,
  backendMessage?: string,
) {
  let errorMessage = generateErrorMessage(url, error, status);

  // 后端错误信息在所有环境都展示，确保用户能看到完整提示
  if (backendMessage && status) {
    const prefix = `请求失败 [${status}]`;
    if (isLocalDev()) {
      errorMessage = errorMessage.replace(
        prefix,
        `${prefix}\n后端返回: ${backendMessage}`,
      );
    } else {
      errorMessage = `${formatApiPath(url)}: ${backendMessage}`;
    }
  } else if (!isLocalDev() && status) {
    errorMessage = `${formatApiPath(url)}: 请求失败 [${status}]`;
  }

  // 本地开发环境：使用 ErrorPrompt 强提醒
  if (isLocalDev() && errorMessage.includes('\n')) {
    const lines = errorMessage.split('\n');
    const title = lines[0];
    const description = lines.slice(1).join('\n');

    showErrorPrompt({
      title,
      message: description,
      dismissible: true,
    });
  } else {
    // 非本地开发环境：使用 toast 简单提示
    toast.error(errorMessage, {
      duration: 5000,
    });
  }
}
