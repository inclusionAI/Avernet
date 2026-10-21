// useSessionSwitchReset —— 会话切换重置(2026-10-21,自 ConversationPage 拆出):
// /file 等面板唤起后切会话,输入框清空、未发送文件 chip 全部失效;
// subConfig 子面板(如 /file 文件清单)的显隐是 SDK 内部状态,clear 清空输入不足以关闭——
// 经原生输入元素派发 Escape(SDK 退出方式:子级退回根级、根级关闭)。第二次 Escape 须延至
// 下一宏任务派发:同步连发会读到旧导航深度,两次都执行"退回上一级"而漏掉根级关闭。
// 首次挂载不触发(仅会话间切换);清理器清掉未派发的挂起 Escape(快速连切由新会话的派发接管)。
import type { SenderRef } from '@tc-chat/ui/es/Sender';
import { useEffect, useRef, type RefObject } from 'react';

export function useSessionSwitchReset(
  selectedSessionId: string | null,
  senderRef: RefObject<SenderRef | null>,
): void {
  const prevSessionSwitchRef = useRef<string | null>(null);
  const sessionSwitchEscapeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    const prev = prevSessionSwitchRef.current;
    const current = selectedSessionId;
    prevSessionSwitchRef.current = current;
    if (prev === null || prev === current) return;
    const sender = senderRef.current;
    sender?.clear();
    sender?.invalidateFileChips();
    const native = sender?.nativeElement;
    if (native) {
      native.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      sessionSwitchEscapeTimerRef.current = setTimeout(() => {
        native.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      }, 0);
    }
  }, [selectedSessionId, senderRef]);
  useEffect(
    () => () => {
      if (sessionSwitchEscapeTimerRef.current) {
        clearTimeout(sessionSwitchEscapeTimerRef.current);
        sessionSwitchEscapeTimerRef.current = null;
      }
    },
    [],
  );
}