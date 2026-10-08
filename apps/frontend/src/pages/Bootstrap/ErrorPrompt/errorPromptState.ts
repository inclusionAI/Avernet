/**
 * ErrorPrompt 状态管理
 * 用于在任何地方显示错误提示对话框
 */

export interface ErrorPromptStep {
  /** 步骤说明文本 */
  text: string;
  /** 可选的可复制内容（如 chrome:// 链接） */
  copyable?: string;
}

export interface ErrorPromptState {
  visible: boolean;
  title: string;
  message: string;
  /** 可选的步骤列表（优先于 message 展示） */
  steps?: ErrorPromptStep[];
  /** 步骤前的引导描述 */
  description?: string;
  onRetry?: () => void;
  dismissible?: boolean; // 是否可关闭
}

let state: ErrorPromptState = {
  visible: false,
  title: '系统错误',
  message: '',
  dismissible: false,
};

const listeners = new Set<() => void>();

export function getErrorPromptState(): ErrorPromptState {
  return state;
}

export function showErrorPrompt(options: {
  title?: string;
  message?: string;
  steps?: ErrorPromptStep[];
  description?: string;
  onRetry?: () => void;
  dismissible?: boolean;
}) {
  state = {
    visible: true,
    title: options.title || '系统错误',
    message: options.message || '',
    steps: options.steps,
    description: options.description,
    onRetry: options.onRetry,
    dismissible: options.dismissible ?? false,
  };
  listeners.forEach((l) => l());
}

export function hideErrorPrompt() {
  state = { ...state, visible: false };
  listeners.forEach((l) => l());
}

export function subscribeErrorPrompt(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
