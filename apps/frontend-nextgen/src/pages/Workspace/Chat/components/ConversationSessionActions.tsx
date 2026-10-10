import { Button, ConfirmDialog, IconButton, Input } from '@/components/ui';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';
import { Eraser, MoreHorizontal, Pencil, Star, Trash2 } from 'lucide-react';
import { useConversationSessionMenu, type ConversationSessionRowActions } from '../hooks/useConversationSessionMenu';
import { ConversationSessionMeta } from './ConversationSessionMeta';

export function ConversationSessionActions({
  title,
  createdAt,
  favorite,
  actions,
}: {
  title: string;
  createdAt: string;
  favorite?: { value?: boolean; pending: boolean; toggle(): void };
  actions?: ConversationSessionRowActions;
}) {
  const menu = useConversationSessionMenu(title, actions);
  return (
    <>
      <ConversationSessionMeta createdAt={createdAt} menuOpen={menu.menuOpen}>
        <Popover open={menu.menuOpen} onOpenChange={menu.setMenuOpen}>
          {/* 选中仅绑定标题按钮；click 须冒泡，让其他会话菜单识别外部点击并关闭。 */}
          <PopoverTrigger asChild>
            <IconButton
              label={menu.busy ? '会话操作或列表加载中，请稍候' : '会话更多操作'}
              ariaLabel="会话更多操作"
              size="sm"
              icon={<MoreHorizontal className="h-4 w-4" />}
              disabled={menu.busy || favorite?.pending}
              className="shrink-0"
            />
          </PopoverTrigger>
          <PopoverContent
            align="end"
            className="w-44 p-1"
            onCloseAutoFocus={(event) => {
              if (menu.dialog) event.preventDefault();
            }}
          >
            {favorite && (
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="block">
                      <Button
                        variant="ghost"
                        className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
                        disabled={menu.busy || favorite.pending || favorite.value === undefined}
                        aria-busy={favorite.pending}
                        onClick={() => {
                          menu.setMenuOpen(false);
                          favorite.toggle();
                        }}
                      >
                        <Star className={cn('h-3.5 w-3.5', favorite.value && 'fill-warning text-warning')} />
                        {favorite.value ? '取消收藏' : '收藏会话'}
                      </Button>
                    </span>
                  </TooltipTrigger>
                  {favorite.value === undefined && (
                    <TooltipContent>收藏状态暂不可用，请重新加载会话列表</TooltipContent>
                  )}
                </Tooltip>
              </TooltipProvider>
            )}
            {actions && (
              <>
                <Button
                  variant="ghost"
                  className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
                  onClick={() => menu.open('rename')}
                >
                  <Pencil className="h-3.5 w-3.5" />
                  编辑标题
                </Button>
                <Button
                  variant="ghost"
                  className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
                  onClick={() => menu.open('clear')}
                >
                  <Eraser className="h-3.5 w-3.5" />
                  清除上下文
                </Button>
                <Button
                  variant="ghost"
                  className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs text-destructive"
                  onClick={() => menu.open('delete')}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  删除会话
                </Button>
              </>
            )}
          </PopoverContent>
        </Popover>
      </ConversationSessionMeta>
      <Modal
        open={menu.dialog === 'rename'}
        onOpenChange={(open) => {
          if (!open) menu.close();
        }}
      >
        <ModalContent size="sm" showClose={!menu.busy}>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              void menu.confirm();
            }}
          >
            <ModalHeader>
              <ModalTitle>编辑会话标题</ModalTitle>
              <ModalDescription>修改当前会话的显示名称。</ModalDescription>
            </ModalHeader>
            <Input
              aria-label="会话标题"
              value={menu.titleDraft}
              onChange={(event) => menu.setTitleDraft(event.target.value)}
              disabled={menu.busy}
            />
            <ModalFooter>
              <Button variant="ghost" size="sm" onClick={menu.close} disabled={menu.busy}>
                取消
              </Button>
              <Button variant="default" size="sm" type="submit" disabled={menu.busy || !menu.titleDraft.trim()}>
                {menu.busy ? '保存中…' : '保存'}
              </Button>
            </ModalFooter>
          </form>
        </ModalContent>
      </Modal>
      <ConfirmDialog
        open={menu.dialog === 'clear'}
        title="清除上下文"
        description="将删除该会话的全部历史消息，清除后无法恢复。确定继续吗？"
        confirmText="确认清除"
        confirmVariant="destructive"
        loading={menu.busy}
        onCancel={menu.close}
        onConfirm={menu.confirm}
      />
      <ConfirmDialog
        open={menu.dialog === 'delete'}
        title="删除会话"
        description={`删除后该会话及其消息将无法恢复，确定删除“${title}”吗？`}
        confirmText="确认删除"
        confirmVariant="destructive"
        loading={menu.busy}
        onCancel={menu.close}
        onConfirm={menu.confirm}
      />
    </>
  );
}
