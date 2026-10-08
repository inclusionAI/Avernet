import { Button, IconButton, Input } from '@/components/ui';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/AlertDialog';
import { Modal, ModalContent, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { IdentityView, SessionView } from '@/domain/collaboration';
import type { DomainResult } from '@/services/workspace/identityService';
import { cn } from '@/utils/cn';
import { MoreHorizontal, Pencil, Settings2, Share2, Star, Trash2 } from 'lucide-react';
import React, { useState } from 'react';
import { toast } from 'sonner';
import { ShareDialog } from '../ManagePanel/ShareDialog';
import { formatSessionTime, formatSessionTimeTooltip, SessionCard } from '../SessionCard';

interface SessionItemProps {
  session: SessionView;
  selected: boolean;
  favorite: boolean;
  /** 当前身份（会话操作权限判断：创建者或 driver/manager 可编辑/删除）。 */
  activeIdentity?: IdentityView | null;
  onSelectSession: (sessionId: string) => void;
  onToggleFavorite: (sessionId: string) => void;
  onManageSession?: (sessionId: string) => void;
  onRenameSession?: (sessionId: string, title: string) => Promise<boolean>;
  onDeleteSession?: (sessionId: string) => Promise<boolean>;
  onShareSession?: (sessionId: string) => Promise<DomainResult<{ invitationUrl: string }>>;
}

/** 菜单项通用样式（对齐 Bot 单聊菜单与群菜单）。 */
const MENU_ITEM_CLASS = 'h-auto w-full justify-start gap-2 px-2 py-2 text-xs';
/** 无权限菜单项：视觉置灰 + aria-disabled（保留可点击以给出提示，而非静默消失）。 */
const MENU_ITEM_DISABLED_CLASS = 'cursor-not-allowed opacity-50';

/**
 * 协作群会话条目：标题 + 副行 + 日期，收藏星标常显直达；
 * 会话操作菜单（验收微调）：管理会话 → 编辑标题 → 分享会话 → 删除会话，
 * 能力对齐会话管理面板与 Bot 单聊列表（编辑/删除）。编辑/删除需会话创建者或
 * driver/manager 权限（与面板口径一致），无权限置灰并提示；分享与管理对所有成员开放。
 */
export const SessionItem = React.memo(function SessionItem({
  session,
  selected,
  favorite,
  activeIdentity,
  onSelectSession,
  onToggleFavorite,
  onManageSession,
  onRenameSession,
  onDeleteSession,
  onShareSession,
}: SessionItemProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [titleDraft, setTitleDraft] = useState(session.title);
  const [renaming, setRenaming] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [shareUrl, setShareUrl] = useState<string | null>(null);
  const [sharing, setSharing] = useState(false);

  const sessionTime = session.lastMessageAt ?? session.createdAt;
  const createdTime = formatSessionTime(sessionTime);

  // 会话操作权限（对齐 SessionManagePanel）：会话创建者或 participants 中 driver/manager。
  const isCreator = !!session.createdBy && !!activeIdentity && session.createdBy === activeIdentity.id;
  const canManageSession =
    isCreator ||
    session.participants.some((p) => p.actorId === activeIdentity?.id && (p.role === 'driver' || p.role === 'manager'));

  const guard = (action: () => void) => () => {
    if (!canManageSession) {
      toast.error('仅会话创建者或群主/主节点可执行此操作');
      return;
    }
    action();
  };

  const startRename = () => {
    setMenuOpen(false);
    setTitleDraft(session.title);
    setRenameOpen(true);
  };
  const confirmRename = async () => {
    if (!onRenameSession) return;
    setRenaming(true);
    const ok = await onRenameSession(session.sessionId, titleDraft.trim());
    setRenaming(false);
    if (ok) setRenameOpen(false);
  };
  const startDelete = () => {
    setMenuOpen(false);
    setDeleteOpen(true);
  };
  const confirmDelete = async () => {
    if (!onDeleteSession) return;
    setDeleting(true);
    const ok = await onDeleteSession(session.sessionId);
    setDeleting(false);
    if (ok) setDeleteOpen(false);
  };
  const startShare = async () => {
    setMenuOpen(false);
    setShareOpen(true);
    setShareUrl(null);
    setSharing(true);
    const res = onShareSession ? await onShareSession(session.sessionId) : null;
    setSharing(false);
    if (res?.ok) setShareUrl(res.data.invitationUrl);
  };

  return (
    <>
      <SessionCard
        title={session.title}
        subtitle=""
        compact
        dateText={createdTime}
        dateTooltip={formatSessionTimeTooltip(sessionTime)}
        selected={selected}
        indicator="message"
        onSelect={() => onSelectSession(session.sessionId)}
        persistentAction={
          // v1.4：收藏星标——已收藏常显（黄色实心）；未收藏默认隐藏，
          // 悬停/键盘聚焦/会话选中时显现，触屏常显；点击直达切换不触发行选中。
          <IconButton
            label={favorite ? '取消收藏' : '收藏会话'}
            size="sm"
            icon={<Star className={cn('h-4 w-4', favorite ? 'fill-warning text-warning' : 'text-muted-foreground')} />}
            className={cn(
              !favorite &&
                'opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100',
              !favorite && selected && 'opacity-100',
            )}
            onClick={(e) => {
              e.stopPropagation();
              onToggleFavorite(session.sessionId);
            }}
          />
        }
        trailing={
          <div className="flex items-center gap-0.5" onClick={(e) => e.stopPropagation()}>
            <Popover open={menuOpen} onOpenChange={setMenuOpen}>
              <PopoverTrigger asChild>
                <IconButton
                  label="会话更多操作"
                  size="sm"
                  icon={<MoreHorizontal className="h-4 w-4" />}
                  onClick={(e) => e.stopPropagation()}
                />
              </PopoverTrigger>
              <PopoverContent align="end" className="w-44 p-1">
                {onManageSession && (
                  <Button
                    variant="ghost"
                    className={MENU_ITEM_CLASS}
                    onClick={() => {
                      setMenuOpen(false);
                      onManageSession(session.sessionId);
                    }}
                  >
                    <Settings2 className="h-3.5 w-3.5" aria-hidden="true" />
                    管理会话
                  </Button>
                )}
                {onRenameSession && (
                  <Button
                    variant="ghost"
                    aria-disabled={canManageSession ? undefined : true}
                    className={cn(MENU_ITEM_CLASS, !canManageSession && MENU_ITEM_DISABLED_CLASS)}
                    onClick={guard(startRename)}
                  >
                    <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                    编辑标题
                  </Button>
                )}
                {onShareSession && (
                  <Button variant="ghost" className={MENU_ITEM_CLASS} onClick={() => void startShare()}>
                    <Share2 className="h-3.5 w-3.5" aria-hidden="true" />
                    分享会话
                  </Button>
                )}
                {onDeleteSession && (
                  <Button
                    variant="ghost"
                    aria-disabled={canManageSession ? undefined : true}
                    className={cn(MENU_ITEM_CLASS, 'text-destructive', !canManageSession && MENU_ITEM_DISABLED_CLASS)}
                    onClick={guard(startDelete)}
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    删除会话
                  </Button>
                )}
              </PopoverContent>
            </Popover>
          </div>
        }
      />

      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除会话</AlertDialogTitle>
            <AlertDialogDescription>
              删除后该会话及其消息将无法恢复，确定删除“{session.title}”吗？
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => void confirmDelete()} disabled={deleting}>
              {deleting ? '删除中…' : '确认删除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <ShareDialog
        open={shareOpen}
        title="会话"
        inviting={sharing}
        invitationUrl={shareUrl}
        onClose={() => setShareOpen(false)}
      />

      <Modal open={renameOpen} onOpenChange={setRenameOpen}>
        <ModalContent size="sm" showClose>
          <ModalHeader>
            <ModalTitle>编辑会话标题</ModalTitle>
          </ModalHeader>
          <Input value={titleDraft} onChange={(e) => setTitleDraft(e.target.value)} aria-label="会话标题" />
          <ModalFooter>
            <Button variant="ghost" size="sm" onClick={() => setRenameOpen(false)} disabled={renaming}>
              取消
            </Button>
            <Button
              variant="default"
              size="sm"
              onClick={() => void confirmRename()}
              disabled={renaming || !titleDraft.trim()}
            >
              保存
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </>
  );
});
