/**
 * VSCode 扩展通信工具
 * 负责与 VSCode 父窗口进行 postMessage 通信
 */

import { isVSCode, postMessageToVSCode as _postMessage } from './platform';

/**
 * VSCode 消息类型定义
 */
export interface VSCodeMessage {
  source: 'web';
  type: string;
  [key: string]: any;
}

/**
 * 认证 Token 消息
 */
export interface AuthTokenMessage extends VSCodeMessage {
  type: 'auth-token';
  token: string;
}

/**
 * 向 VSCode 扩展宿主发送消息
 */
function postMessageToVSCode(message: VSCodeMessage): void {
  if (!isVSCode()) {
    console.debug('[VSCode] 非VSCode环境,跳过消息发送:', message.type);
    return;
  }

  try {
    _postMessage(message);
    console.log('[VSCode] 消息已发送:', message.type, message);
  } catch (error) {
    console.error('[VSCode] 发送消息失败:', error);
  }
}

/**
 * 发送认证 Token 到 VSCode
 * @param token 用户认证 token
 */
export function sendAuthToken(token: string): void {
  if (!token) {
    console.warn('[VSCode] Token 为空,跳过发送');
    return;
  }

  const message: AuthTokenMessage = {
    source: 'web',
    type: 'auth-token',
    token,
  };

  postMessageToVSCode(message);
}

/**
 * 发送自定义事件到 VSCode
 * @param type 事件类型
 * @param payload 事件数据
 */
export function sendEvent(type: string, payload?: Record<string, any>): void {
  const message: VSCodeMessage = {
    source: 'web',
    type,
    ...payload,
  };

  postMessageToVSCode(message);
}

/**
 * 发送用户信息到 VSCode
 * @param userInfo 用户信息对象
 */
export function sendUserInfo(userInfo: {
  userId?: string;
  nickname?: string;
  [key: string]: any;
}): void {
  sendEvent('user-info', userInfo);
}

// ======================== 接收消息 ========================

/**
 * VSCode 发送给 Web 的消息类型
 */
export interface VSCodeIncomingMessage {
  type: 'file-context' | 'selection-context' | 'file-saved' | string;
  [key: string]: any;
}

/**
 * 消息处理器类型
 */
export type MessageHandler = (message: VSCodeIncomingMessage) => void;

// 消息处理器映射表
const messageHandlers = new Map<string, Set<MessageHandler>>();

/**
 * 注册消息处理器
 * @param type 消息类型，支持通配符 '*' 表示处理所有消息
 * @param handler 处理函数
 * @returns 取消注册的函数
 */
export function onMessage(type: string, handler: MessageHandler): () => void {
  if (!messageHandlers.has(type)) {
    messageHandlers.set(type, new Set());
  }
  messageHandlers.get(type)!.add(handler);

  // 返回取消注册的函数
  return () => {
    const handlers = messageHandlers.get(type);
    if (handlers) {
      handlers.delete(handler);
      if (handlers.size === 0) {
        messageHandlers.delete(type);
      }
    }
  };
}

/**
 * 处理接收到的消息
 */
function handleIncomingMessage(event: MessageEvent): void {
  const message = event.data as VSCodeIncomingMessage;

  if (!message || !message.type) {
    return;
  }

  console.log('[VSCode] 收到消息:', message);

  // 触发特定类型的处理器
  const typeHandlers = messageHandlers.get(message.type);
  if (typeHandlers) {
    typeHandlers.forEach((handler) => {
      try {
        handler(message);
      } catch (error) {
        console.error(`[VSCode] 处理消息失败 (${message.type}):`, error);
      }
    });
  }

  // 触发通配符处理器
  const wildcardHandlers = messageHandlers.get('*');
  if (wildcardHandlers) {
    wildcardHandlers.forEach((handler) => {
      try {
        handler(message);
      } catch (error) {
        console.error('[VSCode] 通配符处理器失败:', error);
      }
    });
  }
}

let isListenerInitialized = false;

/**
 * 初始化消息监听器
 * 只会初始化一次，重复调用会被忽略
 */
export function initMessageListener(): void {
  if (isListenerInitialized) {
    console.debug('[VSCode] 消息监听器已初始化，跳过');
    return;
  }

  if (!isVSCode()) {
    console.debug('[VSCode] 非VSCode环境，不初始化消息监听器');
    return;
  }

  window.addEventListener('message', handleIncomingMessage);
  isListenerInitialized = true;
  console.log('[VSCode] 消息监听器已初始化');
}

/**
 * 移除消息监听器（用于清理）
 */
export function removeMessageListener(): void {
  if (!isListenerInitialized) {
    return;
  }

  window.removeEventListener('message', handleIncomingMessage);
  messageHandlers.clear();
  isListenerInitialized = false;
  console.log('[VSCode] 消息监听器已移除');
}
