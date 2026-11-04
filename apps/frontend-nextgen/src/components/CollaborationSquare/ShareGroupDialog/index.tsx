import { GroupTypeBadge } from '@/components/CollaborationSquare/GroupCard';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Modal, ModalContent, ModalDescription, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import { notifyError, notifySuccess } from '@/components/ui/notify';
import type { PublicGroup } from '@/domain/collaborationSquare/types';
import { getCollaborationSquareShareUrl } from '@/utils/collaborationSquare';
import { Bot, Check, UserRound } from 'lucide-react';
import type { ReactElement } from 'react';
import { useEffect, useState } from 'react';

export interface ShareGroupDialogProps {
  open: boolean;
  group: PublicGroup | null;
  onClose: () => void;
}

/** 文本超宽时悬浮查看全文，与广场卡片同规格。 */
function ShareTextTooltip({ text, children }: { text: string; children: ReactElement }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent className="max-w-[min(24rem,calc(100vw-2rem))] whitespace-normal break-words">
        {text}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * 分享协作群弹窗（dmore artboard-006 Dialog 420×248）：标题/副说明 + 灰底群预览条（名称、
 * 群主与 Owner 灰字、右上类型胶囊）+ 「分享链接」只读输入框 + 76px 蓝底「复制链接」按钮；
 * 复制成功后按钮切换为「已复制」并保留既有 toast 提示。
 */
export function ShareGroupDialog({ open, group, onClose }: ShareGroupDialogProps) {
  const [copied, setCopied] = useState(false);
  const shareUrl = group ? getCollaborationSquareShareUrl(window.location.origin, 'group', group.id) : '';

  useEffect(() => {
    if (!open) setCopied(false);
  }, [open]);

  const copyLink = async () => {
    try {
      await navigator.clipboard.writeText(shareUrl);
      setCopied(true);
      notifySuccess('分享链接已复制');
    } catch {
      notifyError('复制失败，请检查浏览器剪贴板权限');
    }
  };

  const ownerBotLabel =
    group && group.driverBotUuid && group.ownerBotName !== group.driverBotUuid
      ? `${group.ownerBotName} (${group.driverBotUuid})`
      : group?.ownerBotName;

  return (
    <TooltipProvider delayDuration={200}>
      <Modal open={open} onOpenChange={(next) => !next && onClose()}>
        {/* 宽度 480px：用同组 max-w 覆盖基类 max-w-md（避免 sm: 变体与无前缀类级联歧义）；
            内容定高很短，内联 overflow:hidden 杜绝左右/上下滚动条（类级联同款不可靠，见 ghost 底色经验）。 */}
        <ModalContent
          className="gap-0 max-w-[480px] rounded-xl"
          style={{ overflow: 'hidden' }}
          closeLabel="关闭分享协作群弹窗"
          // 弹窗打开时 Radix 会把初始焦点赋给 Content 内 DOM 序第一个可聚焦元素——本弹窗里
          // 那是预览条中带 tooltip 的群名称段（p[tabIndex=0]，关闭按钮渲染在 children 之后），
          // 焦点会令该 tooltip 无延迟 instant-open，视觉上"未 hover 凭空展开"，故阻断初始抢焦。
          onOpenAutoFocus={(event) => event.preventDefault()}
          // 关闭弹窗默认把焦点还给触发钮（卡上的分享 IconButton），归还的 focus 同样会
          // 触发其 tooltip 的 focus 展开逻辑——鼠标未 hover 也会凭空弹出 tooltip，故阻断归还。
          onCloseAutoFocus={(event) => event.preventDefault()}
        >
          <ModalHeader>
            <ModalTitle>分享协作群</ModalTitle>
            <ModalDescription className="text-[13px] leading-5">
              复制链接，邀请其他用户查看这个公开协作群。
            </ModalDescription>
          </ModalHeader>
          {group && (
            /* 群预览条（dmore 370×60 灰底圆角条；加宽后随内容列 434px 弹性）：左列名称 + 12px 群主/Owner 灰字，右列类型胶囊。 */
            <div className="mt-4 flex h-[60px] min-w-0 items-center gap-3 rounded-lg bg-muted px-3">
              <div className="min-w-0 flex-1">
                <ShareTextTooltip text={group.name}>
                  <p tabIndex={0} className="m-0 truncate text-sm font-semibold leading-[18px] text-foreground">
                    {group.name}
                  </p>
                </ShareTextTooltip>
                <div className="mt-1 flex min-w-0 items-center gap-3 text-xs text-muted-foreground">
                  <span className="flex min-w-0 max-w-[45%] items-center gap-1">
                    <UserRound aria-hidden className="h-3 w-3 shrink-0" />
                    <span className="sr-only">Owner 用户：</span>
                    <span className="truncate">{group.ownerUserName || '未公开'}</span>
                  </span>
                  <ShareTextTooltip text={ownerBotLabel ?? ''}>
                    <span tabIndex={0} className="flex min-w-0 flex-1 items-center gap-1">
                      <Bot aria-hidden className="h-3 w-3 shrink-0" />
                      <span className="sr-only">群主 Bot：</span>
                      <span className="truncate">{ownerBotLabel}</span>
                    </span>
                  </ShareTextTooltip>
                </div>
              </div>
              <span className="shrink-0">
                <GroupTypeBadge typeLabel={group.typeLabel} />
              </span>
            </div>
          )}
          <p className="mt-4 text-xs text-muted-foreground">分享链接</p>
          <div className="mt-2 flex min-w-0 items-center gap-2">
            <Input
              readOnly
              value={shareUrl}
              aria-label="分享链接"
              onFocus={(event) => event.currentTarget.select()}
              className="h-9 min-w-0 flex-1 border-input bg-muted font-normal text-muted-foreground"
            />
            {/* 复制按钮随内容自适应加宽（px-3 收窄内边距 + 单行不换行）：基类 px-4 会把
                「复制链接」挤成两行，固定 76px 也装不下「已复制✓」，改为 whitespace-nowrap 自宽。 */}
            <Button
              onClick={() => void copyLink()}
              aria-label="复制链接"
              className="h-9 shrink-0 whitespace-nowrap rounded-lg px-3 text-[13px] font-medium"
              leftIcon={
                copied ? <Check aria-hidden className="h-3.5 w-3.5" /> : undefined
              }
            >
              {copied ? '已复制' : '复制链接'}
            </Button>
          </div>
        </ModalContent>
      </Modal>
    </TooltipProvider>
  );
}
