import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotActionAvailability, BotDomain } from '@/services/botWorkshop';
import { ArrowUpRight, FileText, HeartPulse, MessageSquare, Pencil } from 'lucide-react';
import React from 'react';

export interface BotPrimaryActionsCellProps {
  bot: BotDomain;
  onConversation?: (bot: BotDomain) => void;
  onView: (bot: BotDomain) => void;
  onEdit?: (bot: BotDomain) => void;
  onOpenLogs?: (bot: BotDomain) => void;
  onHealthCheck?: (bot: BotDomain) => void;
  inventoryActions?: Partial<Record<'view' | 'chat' | 'edit', BotActionAvailability>>;
  healthCheckAvailability?: BotActionAvailability;
  logAction?: BotActionAvailability;
}

interface IconActionProps {
  label: string;
  disabledReason?: string;
  disabled?: boolean;
  children: React.ReactElement;
}

/**
 * 图标按钮统一带 Tooltip:可用时显示动作名,禁用且有原因时改显示禁用原因。
 * 注意:不要用 React.cloneElement(children, { onClick }) 覆盖子按钮的回调,
 * 否则 onClick 为 undefined 时会清掉按钮原有处理。
 */
function IconAction({ label, disabledReason, disabled, children }: IconActionProps) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">{children}</span>
        </TooltipTrigger>
        <TooltipContent>{disabled && disabledReason ? disabledReason : label}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

const BotPrimaryActionsCell: React.FC<BotPrimaryActionsCellProps> = ({
  bot,
  onConversation,
  onView,
  onEdit,
  onOpenLogs,
  onHealthCheck,
  inventoryActions = {},
  healthCheckAvailability,
  logAction,
}) => {
  const isAgentCodingBot = bot.runtime.isAgentCodingBot;
  const lockedByOther = bot.lock?.status === 'other';

  // Agent Coding Bot 的「去使用」固定可用,不受 chat action 可用性约束(与卡片实现一致)。
  const chatDisabled = isAgentCodingBot ? false : !inventoryActions.chat?.enabled;
  const chatDisabledReason = isAgentCodingBot ? undefined : inventoryActions.chat?.disabledReason;

  const chatButton = (
    <Button
      variant="ghost"
      size="icon"
      aria-label={isAgentCodingBot ? `去使用 ${bot.name}` : `与 ${bot.name} 对话`}
      disabled={chatDisabled}
      onClick={() => onConversation?.(bot)}
    >
      <MessageSquare className="size-3.5" />
    </Button>
  );

  const viewButton = (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`查看 ${bot.name} 详情`}
      disabled={!inventoryActions.view?.enabled}
      onClick={() => onView(bot)}
    >
      <ArrowUpRight className="size-3.5" />
    </Button>
  );

  const editButton = (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`编辑 ${bot.name}`}
      disabled={!inventoryActions.edit?.enabled}
      onClick={() => onEdit?.(bot)}
    >
      <Pencil className="size-3.5" />
    </Button>
  );

  const logButton = (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`查看 ${bot.name} 日志`}
      disabled={!logAction?.enabled}
      onClick={() => onOpenLogs?.(bot)}
    >
      <FileText className="size-3.5" />
    </Button>
  );

  const healthButton = (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`${bot.name} 健康检查`}
      disabled={!healthCheckAvailability?.enabled || lockedByOther}
      onClick={() => onHealthCheck?.(bot)}
    >
      <HeartPulse className="size-3.5" />
    </Button>
  );

  const showChat = isAgentCodingBot
    ? Boolean(onConversation)
    : Boolean(inventoryActions.chat?.visible && onConversation);
  const showView = !isAgentCodingBot && Boolean(inventoryActions.view?.visible);
  const showEdit = !isAgentCodingBot && Boolean(inventoryActions.edit?.visible);
  const showLog = !isAgentCodingBot && Boolean(logAction?.visible && onOpenLogs);
  const showHealth = !isAgentCodingBot && Boolean(healthCheckAvailability?.visible && onHealthCheck);

  return (
    <div
      className="flex items-center justify-end gap-1"
      onClick={(event) => event.stopPropagation()}
      role="group"
      aria-label={`${bot.name} 主要操作`}
    >
      {showChat ? (
        <IconAction
          label={isAgentCodingBot ? '去使用' : '对话'}
          disabled={chatDisabled}
          disabledReason={chatDisabledReason}
        >
          {chatButton}
        </IconAction>
      ) : null}
      {showView ? (
        <IconAction
          label="查看"
          disabled={!inventoryActions.view?.enabled}
          disabledReason={inventoryActions.view?.disabledReason}
        >
          {viewButton}
        </IconAction>
      ) : null}
      {showEdit ? (
        <IconAction
          label="编辑"
          disabled={!inventoryActions.edit?.enabled}
          disabledReason={inventoryActions.edit?.disabledReason}
        >
          {editButton}
        </IconAction>
      ) : null}
      {showLog ? (
        <IconAction label="日志" disabled={!logAction?.enabled} disabledReason={logAction?.disabledReason}>
          {logButton}
        </IconAction>
      ) : null}
      {showHealth ? (
        <IconAction
          label="健康检查"
          disabled={!healthCheckAvailability?.enabled || lockedByOther}
          disabledReason={lockedByOther ? '该 Bot 正被他人编辑，请先抢锁' : healthCheckAvailability?.disabledReason}
        >
          {healthButton}
        </IconAction>
      ) : null}
    </div>
  );
};

export default BotPrimaryActionsCell;
