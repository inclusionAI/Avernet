import type { ConversationSessionAction } from '@/services/workspace/conversationSessionActionService';
import { useRef, useState } from 'react';

export interface ConversationSessionRowActions {
  run(action: ConversationSessionAction): Promise<boolean>;
  pending: boolean;
}

/** 弹窗只在请求成功后关闭；异步失败保留草稿和确认上下文。 */
export function useConversationSessionMenu(title: string, actions?: ConversationSessionRowActions) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [dialog, setDialog] = useState<ConversationSessionAction['type'] | null>(null);
  const [titleDraft, setTitleDraft] = useState(title);
  const [submitting, setSubmitting] = useState(false);
  const inFlight = useRef(false);
  const busy = Boolean(actions?.pending) || submitting;
  const open = (kind: ConversationSessionAction['type']) => {
    if (!actions || busy) return;
    setMenuOpen(false);
    setTitleDraft(title);
    setDialog(kind);
  };
  const close = () => {
    if (!busy) setDialog(null);
  };
  const confirm = async () => {
    if (!actions || !dialog || busy || inFlight.current) return;
    const nextTitle = titleDraft.trim();
    if (dialog === 'rename' && !nextTitle) return;
    if (dialog === 'rename' && nextTitle === title) {
      setDialog(null);
      return;
    }
    inFlight.current = true;
    setSubmitting(true);
    try {
      const ok = await actions.run(dialog === 'rename' ? { type: dialog, title: nextTitle } : { type: dialog });
      if (ok) setDialog(null);
    } finally {
      inFlight.current = false;
      setSubmitting(false);
    }
  };
  return { menuOpen, setMenuOpen, dialog, titleDraft, setTitleDraft, busy, open, close, confirm };
}
