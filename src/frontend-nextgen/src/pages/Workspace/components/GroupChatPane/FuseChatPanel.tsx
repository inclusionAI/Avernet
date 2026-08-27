import { Button, Empty, Input } from '@/components/ui';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { GroupView } from '@/domain/collaboration';
import { useFuse, type FusionBotInfo } from '@/pages/Workspace/hooks/useFuse';
import { cn } from '@/utils/cn';
import type { TextBlock } from '@tc-chat/core';
import { Bubble } from '@tc-chat/ui/es/Bubble';
import { Check, Send } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { AvatarTile } from '../AvatarTile';

interface FuseChatPanelProps {
  group: GroupView | null;
  sessionId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function FuseChatPanel({ group, sessionId, open, onOpenChange }: FuseChatPanelProps) {
  const { messages, isFusing, submitQuestion, fusionBots, isLoadingFusionBots } = useFuse(
    open ? group : null,
    open ? sessionId : null,
  );
  const [input, setInput] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages]);

  const availableBots = useMemo(() => fusionBots.filter((b) => b.fusionEnable), [fusionBots]);

  const toggleBot = (id: string) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const handleSubmit = () => {
    if (!input.trim() || isFusing) return;
    void submitQuestion(input, [...selected]);
    setInput('');
  };

  return (
    <Modal open={open} onOpenChange={onOpenChange}>
      <ModalContent size="md" showClose={false} className="p-0">
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3">
          <ModalHeader>
            <ModalTitle>融合模式</ModalTitle>
            <p className="text-xs text-[var(--color-muted)]">选择协作群内公开画像的 Bot，融合回答你的问题</p>
          </ModalHeader>
        </div>

        {/* 融合 Bot 选择 */}
        <div className="border-b border-[var(--color-border)] px-4 py-3">
          {isLoadingFusionBots ? (
            <p className="text-xs text-[var(--color-muted)]">加载中…</p>
          ) : availableBots.length === 0 ? (
            <p className="text-xs text-[var(--color-muted)]">协作群内无 Bot 公开画像，融合模式暂不可用</p>
          ) : (
            <div className="flex flex-wrap gap-3">
              {availableBots.map((bot: FusionBotInfo) => {
                const isSelected = selected.has(bot.botUuid);
                return (
                  <Button
                    key={bot.botUuid}
                    variant="ghost"
                    size="sm"
                    onClick={() => toggleBot(bot.botUuid)}
                    className="h-auto min-w-16 flex-col items-center gap-1 rounded-lg px-1 py-1"
                  >
                    <div className="relative">
                      <AvatarTile src={bot.avatar} label={bot.name} className="h-10 w-10 rounded-xl" />
                      <div
                        className={cn(
                          'absolute -right-0.5 -top-0.5 flex h-4 w-4 items-center justify-center rounded-full transition-colors',
                          isSelected ? 'bg-[var(--color-primary)]' : 'bg-[var(--color-border)]',
                        )}
                      >
                        {isSelected && <Check className="h-2.5 w-2.5 text-white" />}
                      </div>
                    </div>
                    <span
                      className={cn(
                        'max-w-[60px] truncate text-[10px]',
                        isSelected ? 'font-medium text-[var(--color-primary)]' : 'text-[var(--color-muted)]',
                      )}
                    >
                      {bot.name}
                    </span>
                  </Button>
                );
              })}
            </div>
          )}
        </div>

        {/* 消息列表 */}
        <div ref={scrollRef} className="app-scrollbar max-h-[400px] space-y-3 overflow-y-auto px-4 py-3">
          {messages.length === 0 ? (
            <Empty compact title="暂无消息" description="输入问题后开始融合问答" />
          ) : (
            messages.map((m) => (
              <Bubble
                key={m.id}
                sender={{ role: m.role, name: m.role === 'user' ? '我' : '融合回答' }}
                blocks={[{ type: 'text', content: m.content } as TextBlock]}
                isStreaming={m.isLoading}
              />
            ))
          )}
        </div>

        {/* 输入区 */}
        <div className="flex gap-2 border-t border-[var(--color-border)] p-3">
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                handleSubmit();
              }
            }}
            placeholder="输入问题…"
            disabled={isFusing}
          />
          <Button variant="primary" size="sm" disabled={!input.trim() || isFusing} onClick={handleSubmit}>
            <Send className="h-4 w-4" />
          </Button>
        </div>
      </ModalContent>
    </Modal>
  );
}
