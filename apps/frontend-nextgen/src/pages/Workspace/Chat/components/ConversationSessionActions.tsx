import { Button, ConfirmDialog, IconButton, Input } from '@/components/ui';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { cn } from '@/utils/cn';
import { Eraser, MoreHorizontal, Pencil, ScrollText, Share2, ShieldCheck, Trash2 } from 'lucide-react';
import { useConversationSessionMenu, type ConversationSessionRowActions } from '../hooks/useConversationSessionMenu';
import { ConversationSessionMeta } from './ConversationSessionMeta';

export function ConversationSessionActions({
  title,
  createdAt,
  actions,
  withMeta = true,
}: {
  title: string;
  createdAt: string;
  actions?: ConversationSessionRowActions;
  /** 平齐化会话行(dmore)不展示右侧时间列,只出 hover「…」菜单;缺省包含时间槽。 */
  withMeta?: boolean;
}) {
  const menu = useConversationSessionMenu(title, actions);
  const titleMaxLength = actions?.features?.titleMaxLength;
  const menuPopover = (
    <Popover open={menu.menuOpen} onOpenChange={menu.setMenuOpen}>
      {/* 选中仅绑定标题按钮；click 须冒泡，让其他会话菜单识别外部点击并关闭。 */}
      <PopoverTrigger asChild>
        <IconButton
          label={menu.busy ? '会话操作或列表加载中，请稍候' : '会话更多操作'}
          ariaLabel="会话更多操作"
          size="sm"
          icon={<MoreHorizontal className={withMeta ? 'h-4 w-4' : 'h-3.5 w-3.5'} />}
          disabled={menu.busy}
          className={cn(
            'shrink-0',
            !withMeta && 'h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground',
          )}
        />
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-44 p-1"
        onCloseAutoFocus={(event) => {
          if (menu.dialog) event.preventDefault();
        }}
      >
        {actions && (
          <>
            {actions.features?.dima && (
              <Button
                variant="ghost"
                className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
                onClick={() => {
                  menu.setMenuOpen(false);
                  void actions.run({ type: 'dima' });
                }}
              >
                <ShieldCheck className="h-3.5 w-3.5" />
                关联 DIMA
              </Button>
            )}
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
            {actions.features?.share && (
              <Button
                variant="ghost"
                className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
                onClick={() => {
                  menu.setMenuOpen(false);
                  void actions.run({ type: 'share' });
                }}
              >
                <Share2 className="h-3.5 w-3.5" />
                分享链接
              </Button>
            )}
            {actions.features?.logs && (
              <Button
                variant="ghost"
                className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
                onClick={() => {
                  menu.setMenuOpen(false);
                  void actions.run({ type: 'logs' });
                }}
              >
                <ScrollText className="h-3.5 w-3.5" />
                日志
              </Button>
            )}
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
  );
  return (
    <>
      {withMeta ? (
        <ConversationSessionMeta createdAt={createdAt} menuOpen={menu.menuOpen}>
          {menuPopover}
        </ConversationSessionMeta>
      ) : (
        // 平齐化行内形态:与星标同款 hover/focus 显现,菜单打开期间钉住可见(定位由行提供)。
        <span
          data-session-meta-actions
          className={cn(
            'opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:opacity-100',
            menu.menuOpen && 'opacity-100',
          )}
        >
          {menuPopover}
        </span>
      )}
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
              maxLength={titleMaxLength}
              placeholder={titleMaxLength ? `请输入会话标题（最多 ${titleMaxLength} 个字符）` : undefined}
            />
            {titleMaxLength ? (
              <p className="text-right text-xs font-normal text-content-hint">
                {menu.titleDraft.length}/{titleMaxLength}
              </p>
            ) : null}
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
