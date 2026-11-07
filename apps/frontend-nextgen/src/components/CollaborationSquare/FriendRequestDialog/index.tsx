import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalTitle } from '@/components/ui/Modal';
import { Skeleton } from '@/components/ui/Skeleton';
import type { PublicBot, PublicBotProfile } from '@/domain/collaborationSquare/types';
import { cn } from '@/utils/cn';
import { Bot, Hash, UserRound, Wrench } from 'lucide-react';

interface FriendRequestDialogProps {
  /** 目标公开 Bot（Catalog 摘要：名称/Owner/UUID/描述立即可用）。 */
  bot: PublicBot;
  /** 详情画像（引擎 + 已公开能力来自 profile 接口）；未就绪时为 null。 */
  profile: PublicBotProfile | null;
  /** 详情拉取中；为 true 时不展示发送按钮，避免版式在加载后跳变。 */
  loading: boolean;
  onClose: () => void;
  /** 点击「申请好友权限」发送好友申请。 */
  onSendRequest: () => void;
}

/** 引擎胶囊（75×22、10px 圆角、15% 灰底，dmore「OpenClaw」标签）。 */
function EngineChip({ engine }: { engine: string }) {
  return (
    <span className="inline-flex h-[22px] shrink-0 items-center rounded-[10px] bg-foreground/15 px-2 text-xs font-medium text-foreground">
      {engine}
    </span>
  );
}

/**
 * 申请好友权限弹窗（dmore artboard-003 无能力 / artboard-007 有能力）：展示目标 Bot
 * 公开画像与「申请好友权限」入口。是否有已公开能力决定两种版式：
 * - 无已公开能力（artboard-003）：580px 宽，按钮水平居中沉底。
 * - 有已公开能力（artboard-007）：682px 宽，按钮挂头部右端，能力列表区内滚动。
 */
export function FriendRequestDialog({ bot, profile, loading, onClose, onSendRequest }: FriendRequestDialogProps) {
  const capabilities = profile?.capabilities ?? [];
  const hasCapabilities = capabilities.length > 0;
  const engine = profile?.engine?.trim() ?? '';
  const sendButton = (
    <Button className="h-8 w-[110px] px-0 text-[13px]" onClick={onSendRequest}>
      申请好友权限
    </Button>
  );
  return (
    <Modal open onOpenChange={(open) => !open && onClose()}>
      <ModalContent
        className={cn(
          'flex flex-col gap-0 overflow-hidden rounded-xl border-0 p-0',
          // artboard-003＝580px、artboard-007＝682px，随已公开能力切换。
          hasCapabilities ? 'max-w-[682px]' : 'max-w-[580px]',
        )}
      >
        <ModalTitle className="sr-only">{`申请好友权限：${bot.name}`}</ModalTitle>
        <ModalDescription className="sr-only">
          查看该 Bot 的公开画像与已公开能力，确认后向 Bot Owner 用户发送好友申请。
        </ModalDescription>
        {/* 头部（dmore Frame 8/7）：72px 圆角头像 + 名称/引擎胶囊 + Owner/UUID 元信息行。 */}
        <div className="flex items-center gap-4 px-6 pt-12">
          <div className="flex h-[72px] w-[72px] shrink-0 items-center justify-center rounded-[14px] bg-primary/10 text-primary">
            <Bot aria-hidden className="h-10 w-10" />
          </div>
          <div className="flex min-w-0 flex-1 flex-col justify-center gap-1">
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate text-lg font-semibold text-foreground">{bot.name}</span>
              {engine && <EngineChip engine={engine} />}
            </div>
            <div className="flex min-w-0 items-center gap-3 text-xs text-muted-foreground">
              <span className="flex min-w-0 items-center gap-1">
                <UserRound aria-hidden className="h-3 w-3 shrink-0" />
                <span className="truncate">{bot.ownerName}</span>
              </span>
              <span aria-hidden className="h-4 w-px shrink-0 bg-foreground/15" />
              <span className="flex min-w-0 items-center gap-1">
                <Hash aria-hidden className="h-3 w-3 shrink-0" />
                <span className="truncate">{bot.id}</span>
              </span>
            </div>
          </div>
          {/* 有已公开能力时按钮挂头部右端（artboard-007），按钮与头像行垂直居中。 */}
          {hasCapabilities && <div className="ml-auto shrink-0">{sendButton}</div>}
        </div>
        {/* 公开描述（13px/#525252 观感走 foreground/75），Catalog 摘要即可先行展示。 */}
        <p className="m-0 mt-4 px-6 text-[13px] leading-[18px] text-foreground/75">
          {bot.description.trim() || '暂无描述'}
        </p>
        {loading && <Skeleton.Line className="mx-6 mt-3 w-2/5" />}
        {/* 无已公开能力：按钮水平居中沉底（artboard-003）。加载中不展示，避免版式跳变。 */}
        {!hasCapabilities && !loading && <div className="mt-6 flex justify-center pb-8">{sendButton}</div>}
        {/* 有已公开能力：分割线 + 「已公开能力」清单（artboard-007）。 */}
        {hasCapabilities && (
          <>
            <span aria-hidden className="mx-6 mt-5 h-px bg-foreground/10" />
            <div className="px-6 pb-6 pt-4">
              <div className="flex items-center justify-between">
                <h3 className="m-0 text-[13px] font-medium text-foreground">已公开能力</h3>
                <span className="text-[11px] text-muted-foreground">{capabilities.length} 个MCP</span>
              </div>
              <ul className="app-scrollbar m-0 mt-3 max-h-[420px] list-none space-y-2 overflow-y-auto p-0">
                {capabilities.map((capability) => (
                  // 行（62px 高、#F8F8F8 软底、10px 圆角）：28px 白底图标位 + 能力名 + Owner 模式胶囊。
                  <li
                    key={capability.id}
                    className="flex h-[62px] items-center gap-3 rounded-[10px] bg-muted px-3"
                  >
                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-[7px] bg-card">
                      <Wrench aria-hidden className="h-4 w-4 text-foreground" />
                    </span>
                    <span className="truncate text-[13px] font-medium text-foreground">{capability.name}</span>
                    <Badge tone="success" className="shrink-0 px-1.5 text-[10px] font-normal">
                      Owner模式
                    </Badge>
                  </li>
                ))}
              </ul>
            </div>
          </>
        )}
      </ModalContent>
    </Modal>
  );
}
