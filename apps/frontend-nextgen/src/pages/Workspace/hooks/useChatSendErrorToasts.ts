/**
 * useChatSendErrorToasts —— 把 BCS 对 chat.send 的服务端拒绝转成用户可见 toast。
 *
 * SDK 对请求失败静默处理：服务端以 ok:false 拒绝一条群消息（"需先获取发言权"、
 * 身份缺失、无效操作……）时界面上不会有任何反馈，用户以为消息"没有回复"。
 * 本 Hook 订阅 Provider 的旁路错误通知，按已知原因映射成明确话术后提示；
 * 未知原因兜底展示服务端原文。
 */
import type { ConnectionStatusEvent } from '@tc-chat/adapters';
import { useEffect, useRef } from 'react';
import { toast } from 'sonner';

/** Hook 消费的 Provider 结构——GroupChatProvider 结构性满足，测试可注入 fake。 */
export interface ChatSendErrorProvider {
  subscribeToChatSendErrors(
    listener: (error: { code: string; message: string }) => void,
  ): () => void;
  /** 仅为对齐既有 Hook 订阅形态（Provider 生命周期由调用方持有）；当前无附加消费。 */
  subscribeToConnectionStatus?(listener: (event: ConnectionStatusEvent) => void): () => void;
}

/** 服务端 InvalidOperation 的包装前缀，提示前剥掉。 */
const INVALID_OPERATION_PREFIX = 'Invalid operation on request unknown:';
/** message -> 明确话术;未命中走服务端原文。 */
const REASON_TEXT: Array<[RegExp, string]> = [
  [/speaking lease required/, '需先获取发言权才能在 Caller 群发言'],
  [/speaking lease rejected/, '发言权已失效或被抢占，请重新获取后发送'],
  [/caller identity missing/, '登录身份缺失，请刷新页面重新连接后再发送'],
  [/caller target requires human speech/, '该会话仅支持真人 Caller 发言'],
  [/participant absent/i, '当前身份未加入会话，请先加入后再发言'],
];

function reasonOf(error: { code: string; message: string }): string {
  const raw = error.message.trim().replace(INVALID_OPERATION_PREFIX, '').trim();
  for (const [pattern, text] of REASON_TEXT) {
    if (pattern.test(raw)) return text;
  }
  return raw || `消息发送失败（${error.code}）`;
}

/**
 * 群聊发送错误可见化。Provider 变化时即时隔离旧订阅；迟到错误不产生新 toast。
 */
export function useChatSendErrorToasts(provider: ChatSendErrorProvider | null): void {
  const providerRef = useRef<ChatSendErrorProvider | null>(provider);
  providerRef.current = provider;

  useEffect(() => {
    if (!provider) return;
    return provider.subscribeToChatSendErrors((error) => {
      if (providerRef.current !== provider) return;
      toast.error(reasonOf(error));
    });
  }, [provider]);
}