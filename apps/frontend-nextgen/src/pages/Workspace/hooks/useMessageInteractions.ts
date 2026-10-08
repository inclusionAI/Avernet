import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';

import { notifyError, notifySuccess } from '@/components/ui';
import type { ChatMessage } from '@tc-chat/core';

export interface MessageSelection {
  messageId: string;
  text: string;
  rect: DOMRect;
}

export interface MessageQuote {
  messageId: string;
  senderName: string;
  text: string;
}

interface UseMessageInteractionsOptions {
  sessionId: string | null | undefined;
  messages: ChatMessage[];
  isRequesting: boolean;
  onStop?: () => void;
}

export interface MessageInteractions {
  rootRef: RefObject<HTMLDivElement>;
  selection: MessageSelection | null;
  quote: MessageQuote | null;
  unreadCount: number;
  copyText: (text: string, subject?: string) => Promise<boolean>;
  quoteMessage: (messageId: string, senderName: string, text: string) => void;
  clearQuote: () => void;
  setSelection: (selection: MessageSelection | null) => void;
  setAtBottom: (atBottom: boolean) => void;
  markRead: () => void;
}

export function getMessageText(message: ChatMessage): string {
  const textBlocks = (message.blocks ?? [])
    .filter((block) => block.type === 'text')
    .map((block) => {
      const content = (block as { content?: unknown }).content;
      return typeof content === 'string' ? content : '';
    })
    .filter(Boolean);
  return textBlocks.join('\n\n') || message.content || '';
}

export function buildQuotePrompt(senderName: string, text: string): string {
  const quotedLines = text
    .trim()
    .split('\n')
    .map((line) => `> ${line}`)
    .join('\n');
  return `引用 ${senderName} 的消息：\n${quotedLines}`;
}

export function buildExplainPrompt(senderName: string, text: string): string {
  const quotedLines = text
    .trim()
    .split('\n')
    .map((line) => `> ${line}`)
    .join('\n');
  return `请解释以下内容（来自 ${senderName || '未命名成员'}）：\n${quotedLines}`;
}

function getMessageIds(messages: ChatMessage[]): string[] {
  return messages.map((message) => message.id);
}

export function getLatestUserMessageId(messages: ChatMessage[]): string | null {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'user') return messages[index].id;
  }
  return null;
}

function findScrollElement(root: HTMLDivElement): HTMLElement | null {
  return (
    Array.from(root.querySelectorAll<HTMLElement>('div')).find(
      (element) => element.style.overflow === 'auto' || element.style.overflowY === 'auto',
    ) ?? null
  );
}

function isAtBottom(element: HTMLElement, threshold = 96): boolean {
  return element.scrollHeight - element.scrollTop - element.clientHeight < threshold;
}

export function useMessageInteractions({ sessionId, messages, isRequesting, onStop }: UseMessageInteractionsOptions) {
  const rootRef = useRef<HTMLDivElement>(null);
  const scrollElementRef = useRef<HTMLElement | null>(null);
  const previousSessionIdRef = useRef(sessionId);
  const previousMessageIdsRef = useRef(getMessageIds(messages));
  const atBottomRef = useRef(true);
  const [selection, setSelection] = useState<MessageSelection | null>(null);
  const [quote, setQuote] = useState<MessageQuote | null>(null);
  const [unreadCount, setUnreadCount] = useState(0);

  const setAtBottom = useCallback((atBottom: boolean) => {
    atBottomRef.current = atBottom;
    if (atBottom) setUnreadCount(0);
  }, []);

  const markRead = useCallback(() => {
    setUnreadCount(0);
    atBottomRef.current = true;
    const element = scrollElementRef.current;
    if (element) {
      element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' });
    }
  }, []);

  const copyText = useCallback(async (text: string, subject = '消息'): Promise<boolean> => {
    const normalizedText = text.trim();
    if (!normalizedText) {
      notifyError(`暂无可复制的${subject}内容`);
      return false;
    }
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard-unavailable');
      await navigator.clipboard.writeText(normalizedText);
      notifySuccess(`${subject}已复制`);
      return true;
    } catch {
      notifyError(`复制${subject}失败，请检查剪贴板权限`);
      return false;
    }
  }, []);

  const quoteMessage = useCallback((messageId: string, senderName: string, text: string) => {
    const normalizedText = text.trim();
    if (!normalizedText) return;
    setQuote({ messageId, senderName: senderName || '未命名成员', text: normalizedText });
    setSelection(null);
  }, []);

  useEffect(() => {
    if (previousSessionIdRef.current === sessionId) return;
    previousSessionIdRef.current = sessionId;
    previousMessageIdsRef.current = getMessageIds(messages);
    atBottomRef.current = true;
    setSelection(null);
    setQuote(null);
    setUnreadCount(0);
  }, [messages, sessionId]);

  useEffect(() => {
    const previousIds = new Set(previousMessageIdsRef.current);
    const currentIds = getMessageIds(messages);
    if (previousSessionIdRef.current === sessionId) {
      const newMessageCount = messages.filter(
        (message) => !previousIds.has(message.id) && message.role !== 'system',
      ).length;
      if (newMessageCount > 0 && !atBottomRef.current) setUnreadCount((count) => count + newMessageCount);
    }
    previousMessageIdsRef.current = currentIds;
  }, [messages, sessionId]);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    let disposed = false;
    let frame = 0;
    const bind = () => {
      if (disposed) return;
      const element = findScrollElement(root);
      if (!element) {
        frame = requestAnimationFrame(bind);
        return;
      }
      scrollElementRef.current = element;
      const handleScroll = () => setAtBottom(isAtBottom(element));
      element.addEventListener('scroll', handleScroll, { passive: true });
      handleScroll();
      return () => element.removeEventListener('scroll', handleScroll);
    };
    const cleanup = bind();
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      cleanup?.();
      scrollElementRef.current = null;
    };
  }, [messages.length, sessionId, setAtBottom]);

  useEffect(() => {
    const handleSelectionChange = () => {
      const currentSelection = window.getSelection();
      const text = currentSelection?.toString().trim() ?? '';
      const anchor = currentSelection?.anchorNode?.parentElement?.closest('[data-message-id]');
      const focus = currentSelection?.focusNode?.parentElement?.closest('[data-message-id]');
      if (!text || !anchor || !focus || anchor !== focus || !rootRef.current?.contains(anchor)) {
        setSelection(null);
        return;
      }
      const range = currentSelection?.rangeCount ? currentSelection.getRangeAt(0) : null;
      if (!range) return;
      setSelection({
        messageId: anchor.getAttribute('data-message-id') ?? '',
        text,
        rect: range.getBoundingClientRect(),
      });
    };
    document.addEventListener('selectionchange', handleSelectionChange);
    return () => document.removeEventListener('selectionchange', handleSelectionChange);
  }, []);

  useEffect(() => {
    if (!isRequesting || !onStop) return;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      onStop();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isRequesting, onStop]);

  return {
    rootRef,
    selection,
    quote,
    unreadCount,
    copyText,
    quoteMessage,
    clearQuote: () => setQuote(null),
    setSelection,
    setAtBottom,
    markRead,
  } satisfies MessageInteractions;
}
