import { Badge, Button, Empty, Input, Skeleton } from '@/components/ui';
import { Modal, ModalContent, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import type { IdentityView } from '@/domain/collaboration';
import { useDiscoveryBots } from '@/pages/Workspace/hooks/useDiscoveryBots';
import { Check, MessageCircle, Search, UserPlus, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';
import { AvatarTile } from '../AvatarTile';

export interface AddFriendModalProps {
  open: boolean;
  activeIdentity?: IdentityView | null;
  onClose: () => void;
}

/**
 * 添加好友弹窗（Bot 广场）：参考 PRD 示例样式——名称搜索 + Bot 卡片网格。
 * 数据走 GET /openapi/v1/collaboration/bots/{botId}/candidates?purpose=discovery。
 * 智能搜索暂不实现；添加好友动作后续接入（当前点击提示「即将上线」）。
 */
export function AddFriendModal({ open, activeIdentity, onClose }: AddFriendModalProps) {
  const [search, setSearch] = useState('');
  const discovery = useDiscoveryBots(activeIdentity?.id, open, search);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [sendingId, setSendingId] = useState<string | null>(null);

  // 打开时重置搜索，避免上次输入残留
  useEffect(() => {
    if (open) setSearch('');
  }, [open]);

  const handleScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 96 && !discovery.isLoadingMore && discovery.hasMore) {
      discovery.loadMore();
    }
  };

  const handleAddFriend = async (botId: string, name: string, isFriend: boolean, requested: boolean) => {
    if (isFriend || requested) return;
    setSendingId(botId);
    const ok = await discovery.sendFriendRequest(botId);
    setSendingId(null);
    if (ok) toast.success(`已向 ${name} 发送好友申请`);
    else toast.error('发送好友申请失败，请稍后重试');
  };

  return (
    <Modal open={open} onOpenChange={(v) => !v && onClose()}>
      <ModalContent size="xl" showClose={false} className="p-0">
        {/* 标题栏 */}
        <div className="relative border-b border-[var(--color-border)] px-6 py-5">
          <ModalHeader className="pr-0">
            <ModalTitle>Bot 广场</ModalTitle>
            <p className="text-xs leading-5 text-[var(--color-muted)]">
              当前为 {activeIdentity?.kind === 'bot' ? 'Bot' : '用户'} {activeIdentity?.displayName ?? ''}
              ，可按名称搜索公开 Bot，并快速建立好友关系。
            </p>
          </ModalHeader>
          <Button
            aria-label="关闭"
            variant="ghost"
            size="icon"
            className="absolute right-4 top-4 size-7"
            onClick={onClose}
          >
            <X aria-hidden className="size-4" />
          </Button>
        </div>

        {/* 搜索栏 */}
        <div className="border-b border-[var(--color-border)] px-6 py-4">
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-[var(--color-muted)]" />
            <Input
              className="pl-9"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜索 Bot 名称..."
              aria-label="搜索 Bot 名称"
            />
          </div>
        </div>

        {/* Bot 网格 */}
        <div ref={scrollRef} onScroll={handleScroll} className="app-scrollbar max-h-[60vh] overflow-y-auto px-6 py-5">
          {discovery.isLoading && discovery.bots.length === 0 ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {[1, 2, 3, 4, 5, 6].map((i) => (
                <Skeleton.Block key={i} className="h-40 w-full rounded-2xl" />
              ))}
            </div>
          ) : discovery.error ? (
            <Empty
              title="加载 Bot 广场失败"
              description={discovery.error}
              action={
                <Button size="sm" onClick={discovery.retry}>
                  重试
                </Button>
              }
            />
          ) : discovery.bots.length === 0 ? (
            <Empty
              compact
              title="没有找到符合条件的 Bot"
              description={search ? '换个关键词试试' : 'Bot 广场暂无可添加好友的 Bot'}
              icon={<Search className="h-5 w-5" />}
            />
          ) : (
            <>
              {search.trim() && (
                <div className="mb-4 flex items-center justify-between rounded-xl border border-[var(--color-border)] bg-[var(--color-card)] px-4 py-2.5">
                  <p className="text-xs text-[var(--color-muted)]">
                    名称搜索：找到 <span className="font-medium text-[var(--color-fg)]">{discovery.bots.length}</span>{' '}
                    个结果
                  </p>
                  <Button variant="ghost" size="sm" onClick={() => setSearch('')}>
                    清除搜索
                  </Button>
                </div>
              )}
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
                {discovery.bots.map((bot) => {
                  const isFriend = bot.isFriend === true;
                  const requested = discovery.requestedIds.has(bot.id);
                  const sending = sendingId === bot.id;
                  return (
                    <div
                      key={bot.id}
                      className="flex flex-col rounded-2xl border border-[var(--color-border)] bg-[var(--color-card)] p-4 transition-all hover:-translate-y-0.5 hover:border-[var(--color-primary-weak)] hover:shadow-md"
                    >
                      <div className="mb-4 flex items-start gap-3">
                        <AvatarTile src={bot.avatarUrl} label={bot.name} className="h-11 w-11 rounded-xl" />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-1.5">
                            <h3 className="truncate text-sm font-semibold text-[var(--color-fg)]">{bot.name}</h3>
                            {bot.online && (
                              <Badge tone="success">
                                <span className="h-1.5 w-1.5 rounded-full bg-[var(--color-success)]" />
                                在线
                              </Badge>
                            )}
                          </div>
                          <p className="mt-1 truncate text-[11px] text-[var(--color-muted)]" title={bot.id}>
                            Bot ID：{bot.id}
                          </p>
                          <p className="mt-2 line-clamp-2 text-xs leading-relaxed text-[var(--color-muted)]">
                            {bot.summary || '暂无描述'}
                          </p>
                        </div>
                      </div>
                      <div className="mt-auto border-t border-[var(--color-border)]/70 pt-3">
                        <Button
                          variant={isFriend ? 'secondary' : 'primary'}
                          size="sm"
                          className="w-full justify-center gap-1.5"
                          disabled={isFriend || requested || sending}
                          onClick={() => void handleAddFriend(bot.id, bot.name, isFriend, requested)}
                        >
                          {isFriend ? (
                            <>
                              <MessageCircle className="h-3.5 w-3.5" />
                              已是好友
                            </>
                          ) : requested ? (
                            <>
                              <Check className="h-3.5 w-3.5" />
                              申请已发送
                            </>
                          ) : (
                            <>
                              <UserPlus className="h-3.5 w-3.5" />
                              {sending ? '发送中…' : '申请好友权限'}
                            </>
                          )}
                        </Button>
                      </div>
                    </div>
                  );
                })}
              </div>
              {discovery.isLoadingMore && (
                <div className="mt-4 flex justify-center">
                  <Skeleton.Block className="h-8 w-40 rounded-lg" />
                </div>
              )}
            </>
          )}
        </div>
      </ModalContent>
    </Modal>
  );
}
