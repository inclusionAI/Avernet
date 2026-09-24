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
import type { BotChatSessionView } from '@/services/workspace/botSessionService';
import { cn } from '@/utils/cn';
import { Eraser, MoreHorizontal, Pencil, Star, Trash2 } from 'lucide-react';
import React, { useState } from 'react';
import { formatSessionTime, formatSessionTimeTooltip, SessionCard } from '../SessionCard';

interface Props {
  session: BotChatSessionView;
  selected: boolean;
  onSelect: (sessionId: string) => void;
  onRename: (sessionId: string, title: string) => Promise<boolean>;
  onClearContext: (sessionId: string) => Promise<boolean>;
  onDelete: (sessionId: string) => Promise<boolean>;
  favorite?: boolean;
  showFavorite?: boolean;
  onToggleFavorite: (sessionId: string) => Promise<boolean>;
}

export const BotSessionItem = React.memo(function BotSessionItem({
  session,
  selected,
  onSelect,
  onRename,
  onClearContext,
  onDelete,
  favorite,
  showFavorite = true,
  onToggleFavorite,
}: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [titleDraft, setTitleDraft] = useState(session.title);
  const [renaming, setRenaming] = useState(false);
  const [clearOpen, setClearOpen] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const startRename = () => {
    setMenuOpen(false);
    setTitleDraft(session.title);
    setRenameOpen(true);
  };
  const confirmRename = async () => {
    const trimmed = titleDraft.trim();
    if (!trimmed || trimmed === session.title) {
      setRenameOpen(false);
      return;
    }
    setRenaming(true);
    const ok = await onRename(session.sessionId, trimmed);
    setRenaming(false);
    if (ok) setRenameOpen(false);
  };
  const startClear = () => {
    setMenuOpen(false);
    setClearOpen(true);
  };
  const confirmClear = async () => {
    setClearing(true);
    const ok = await onClearContext(session.sessionId);
    setClearing(false);
    if (ok) setClearOpen(false);
  };
  const startDelete = () => {
    setMenuOpen(false);
    setDeleteOpen(true);
  };
  const confirmDelete = async () => {
    setDeleting(true);
    const ok = await onDelete(session.sessionId);
    setDeleting(false);
    if (ok) setDeleteOpen(false);
  };

  return (
    <>
      <SessionCard
        title={session.title}
        /* v1.4：条数副行弱化（移至单聊顶栏 summary），单聊/群聊会话行统一紧凑单行。 */
        subtitle=""
        compact
        dateText={formatSessionTime(session.gmtModified || session.gmtCreate)}
        dateTooltip={formatSessionTimeTooltip(session.gmtModified || session.gmtCreate)}
        selected={selected}
        indicator="message"
        onSelect={() => onSelect(session.sessionId)}
        persistentAction={
          showFavorite ? (
            // v1.4：收藏星标——已收藏常显（黄色实心）；未收藏默认隐藏，
            // 悬停/键盘聚焦/会话选中时显现，触屏常显；点击直达切换不触发行选中。
            <IconButton
              label={favorite ? '取消收藏' : '收藏会话'}
              size="sm"
              icon={
                <Star className={cn('h-4 w-4', favorite ? 'fill-warning text-warning' : 'text-muted-foreground')} />
              }
              className={cn(
                !favorite &&
                  'opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 [@media(hover:none)]:opacity-100',
                !favorite && selected && 'opacity-100',
              )}
              onClick={(e) => {
                e.stopPropagation();
                void onToggleFavorite(session.sessionId);
              }}
            />
          ) : undefined
        }
        trailing={
          <div className="flex items-center gap-0.5" onClick={(e) => e.stopPropagation()}>
            <Popover open={menuOpen} onOpenChange={setMenuOpen}>
              <PopoverTrigger asChild>
                <IconButton label="会话更多操作" size="sm" icon={<MoreHorizontal className="h-4 w-4" />} />
              </PopoverTrigger>
              <PopoverContent align="end" className="w-44 p-1">
                <Button
                  variant="ghost"
                  className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
                  onClick={startRename}
                >
                  <Pencil className="h-3.5 w-3.5" />
                  编辑标题
                </Button>
                <Button
                  variant="ghost"
                  className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs"
                  onClick={startClear}
                >
                  <Eraser className="h-3.5 w-3.5" />
                  清除上下文
                </Button>
                <Button
                  variant="ghost"
                  className="h-auto w-full justify-start gap-2 px-2 py-2 text-xs text-destructive"
                  onClick={startDelete}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  删除会话
                </Button>
              </PopoverContent>
            </Popover>
          </div>
        }
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

      <AlertDialog open={clearOpen} onOpenChange={setClearOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>清除上下文</AlertDialogTitle>
            <AlertDialogDescription>将删除该会话的全部历史消息，清除后无法恢复。确定继续吗？</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={clearing}>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => void confirmClear()} disabled={clearing}>
              {clearing ? '清除中…' : '确认清除'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

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
    </>
  );
});
