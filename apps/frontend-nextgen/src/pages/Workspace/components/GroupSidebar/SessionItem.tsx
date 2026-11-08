import { Button, IconButton } from '@/components/ui';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { IdentityView, SessionView } from '@/domain/collaboration';
import type { DomainResult } from '@/services/workspace/identityService';
import { formatRelativeTime } from '@/utils/format';
import { cn } from '@/utils/cn';
import { MoreHorizontal, Pencil, Settings2, Share2, Star, Trash2 } from 'lucide-react';
import React, { useRef, useState } from 'react';
import { toast } from 'sonner';
import { SharePopover } from '../ManagePanel/SharePopover';
import { SessionItemDeleteDialog, SessionItemRenameDialog } from './SessionItemDialogs';

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
  onShareSession?: (sessionId: string, expiresInSeconds?: number) => Promise<DomainResult<{ invitationUrl: string }>>;
}

/** 菜单项通用样式（对齐 Bot 单聊菜单与群菜单）。 */
const MENU_ITEM_CLASS = 'h-auto w-full justify-start gap-2 px-2 py-2 text-xs';
/** 无权限菜单项：视觉置灰 + aria-disabled（保留可点击以给出提示，而非静默消失）。 */
const MENU_ITEM_DISABLED_CLASS = 'cursor-not-allowed opacity-50';

/**
 * v2 会话二级行（dmore artboard-010「群目录二级_*」32px）：复用对话 ConversationSessionRow
 * 的行几何（背景胶囊/热区分级/星标 28..56px / dots 64..92px 槽位/长标题让位），去除日期展示。
 * 会话操作菜单（管理会话 → 编辑标题 → 分享会话 → 删除会话）能力与权限口径对齐重构前：
 * 编辑/删除需会话创建者或 driver/manager 权限，无权限置灰并提示；分享与管理对所有成员开放。
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
  // 相对时间（lastMessageAt 即接口 updated_at，2026-10-22 精修对齐对话行 gmtModified 口径）：
  // 群列表默认按 lastActivity 排序，时间轻提示呼应排序语义；0/缺失不渲染。
  const timeText = session.lastMessageAt ? formatRelativeTime(new Date(session.lastMessageAt).toISOString()) : '';
  const [renameOpen, setRenameOpen] = useState(false);
  const [titleDraft, setTitleDraft] = useState(session.title);
  const [renaming, setRenaming] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const shareAnchorRef = useRef<HTMLSpanElement | null>(null);

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
  // 打开即止：生成流程收敛在 SharePopover 气泡（有效期 → 生成链接）；气泡锚定在 dots 触发钮下。
  const startShare = () => {
    setMenuOpen(false);
    setShareOpen(true);
  };

  return (
    <>
      <div className="group/row relative flex min-h-8 items-center">
        {/* 行背景胶囊：hover 与选中同几何（对齐对话行）。 */}
        {/* 行背景胶囊：hover 与选中同几何（对齐对话行）。选中升级品牌蓝 bg-primary/10
            （2026-10-22 精修，与对话侧同轮裁决）：原 bg-selected 灰底与 hover muted/60 几乎
            不可辨，且割裂 shell 主导航已定稿的品牌蓝激活语言；几何槽位契约原样保留。 */}
        <span
          aria-hidden="true"
          className={cn(
            'absolute inset-y-0 left-2 right-2 rounded-md',
            selected
              ? 'bg-primary/10'
              : 'bg-muted/60 opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:opacity-100',
          )}
        />
        <Button
          variant="ghost"
          aria-current={selected ? 'page' : undefined}
          onClick={() => onSelectSession(session.sessionId)}
          className={cn(
            'relative flex h-8 min-w-0 flex-1 items-center justify-start gap-2 rounded-md pl-2 pr-2 text-left hover:bg-transparent',
            // 标题左缘对齐一级名称：16px 外距 + 36px 头像槽 + 8px 间隔 = 52px margin + 8px padding。
            favorite ? 'ml-[52px] mr-2' : 'ml-[52px] mr-4',
            // 长标题让位（去日期简化为两槽位仍照对话规格）：
            // 静默 pr-2（右界 24）；星标常驻（收藏/选中）右界 60；hover/聚焦/触屏/菜单开 右界 96。
            favorite && 'pr-[52px]',
            !favorite && selected && 'pr-[44px]',
            'group-hover/row:pr-[80px] group-focus-within/row:pr-[80px] [@media(hover:none)]:pr-[80px] group-has-[[data-state=open]]/row:pr-[80px]',
            favorite &&
              'group-hover/row:pr-[88px] group-focus-within/row:pr-[88px] [@media(hover:none)]:pr-[88px] group-has-[[data-state=open]]/row:pr-[88px]',
          )}
        >
          <span
            className={cn(
              'min-w-0 flex-1 truncate text-[13px] font-normal leading-[18px] text-content-strong',
              // 选中行标题点亮品牌蓝（twMerge 后发覆盖）：与蓝胶囊共同构成选中锚点，不加粗。
              selected && 'text-primary',
            )}
          >
            {session.title}
          </span>
          {/* 相对时间轻提示：静默态可见，hover/聚焦/触屏/「…」菜单开启期退场把右缘让给
              操作图标（与星标显隐同一组触发条件，方向相反）；aria-hidden 保持行可访问名
              为纯标题。群会话行无只读形态，让位档恒挂。 */}
          {timeText && (
            <span
              aria-hidden="true"
              className="ml-1 shrink-0 text-[11px] font-normal leading-none text-content-hint transition-opacity group-hover/row:opacity-0 group-focus-within/row:opacity-0 [@media(hover:none)]:opacity-0 group-has-[[data-state=open]]/row:opacity-0"
            >
              {timeText}
            </span>
          )}
        </Button>
        {/* dots 菜单槽（对话行 64..92px）。 */}
        <div className="absolute right-[64px] top-1/2 z-10 -translate-y-1/2" onClick={(e) => e.stopPropagation()}>
          <Popover open={menuOpen} onOpenChange={setMenuOpen}>
            <PopoverTrigger asChild>
              {/* span 承载分享气泡锚点 ref（IconButton 本体让给菜单 trigger 的 ref 合并）。 */}
              <span
                ref={shareAnchorRef}
                className={cn(
                  'flex shrink-0 opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:opacity-100',
                  menuOpen && 'opacity-100',
                )}
              >
                <IconButton
                  label="会话更多操作"
                  size="sm"
                  icon={<MoreHorizontal className="h-3.5 w-3.5" />}
                  className="h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
                />
              </span>
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
        {/* 收藏星标槽（对话行 28..56px）：未收藏默认隐藏，hover/聚焦/选中/触屏显现。 */}
        <span
          className={cn(
            'absolute right-7 top-1/2 -translate-y-1/2',
            !favorite && !selected && 'opacity-0 transition-opacity',
            !favorite &&
              !selected &&
              'group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:opacity-100',
          )}
        >
          <IconButton
            label={favorite ? '取消收藏' : '收藏会话'}
            size="sm"
            icon={<Star className={cn('h-3.5 w-3.5', favorite ? 'fill-star text-star' : 'text-muted-foreground')} />}
            className="h-6 w-6 rounded-md text-muted-foreground hover:bg-muted hover:text-foreground"
            onClick={(e) => {
              e.stopPropagation();
              onToggleFavorite(session.sessionId);
            }}
          />
        </span>
      </div>

      <SessionItemDeleteDialog
        open={deleteOpen}
        onOpenChange={setDeleteOpen}
        title={session.title}
        deleting={deleting}
        onConfirm={() => void confirmDelete()}
      />

      <SharePopover
        open={shareOpen}
        anchorRef={shareAnchorRef}
        title="会话"
        onCreate={(expiresInSeconds) =>
          onShareSession
            ? onShareSession(session.sessionId, expiresInSeconds)
            : Promise.resolve({ ok: false as const, error: { code: 'NO_SHARE', friendlyMessage: '暂不支持分享该会话', canRetry: false } })
        }
        onClose={() => setShareOpen(false)}
      />

      <SessionItemRenameDialog
        open={renameOpen}
        onOpenChange={setRenameOpen}
        draft={titleDraft}
        onDraftChange={setTitleDraft}
        saving={renaming}
        onConfirm={() => void confirmRename()}
      />
    </>
  );
});
