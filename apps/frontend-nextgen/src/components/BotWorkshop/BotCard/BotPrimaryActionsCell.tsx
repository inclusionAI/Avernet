import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotActionAvailability, BotDomain } from '@/services/botWorkshop';
import { isServiceVerification } from '@/services/botWorkshop/botPolicy';
import { ArrowUpRight, ClipboardList, FileText, HeartPulse, MessageSquare, Pencil } from 'lucide-react';
import React from 'react';

export interface BotPrimaryActionsCellProps {
  bot: BotDomain;
  onConversation?: (bot: BotDomain) => void;
  conversationAllowed?: boolean;
  conversationDisabledReason?: string;
  onView: (bot: BotDomain) => void;
  onEdit?: (bot: BotDomain) => void;
  onOpenLogs?: (bot: BotDomain) => void;
  onOpenTaskInstances?: (bot: BotDomain) => void;
  canOpenTaskInstances?: (bot: BotDomain) => boolean;
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
  conversationAllowed = true,
  conversationDisabledReason,
  onView,
  onEdit,
  onOpenLogs,
  onOpenTaskInstances,
  canOpenTaskInstances,
  onHealthCheck,
  inventoryActions = {},
  healthCheckAvailability,
  logAction,
}) => {
  if (bot.lifecycle === 'deploying') return null;
  const isAgentCodingBot = bot.runtime.isAgentCodingBot;
  const editLabel = isServiceVerification(bot) ? '预发验证' : '编辑';
  const lockedByOther = bot.lock?.status === 'other';
  // Coding Bot 在 Bot 管理页固定展示「去使用」和「任务实例」，
  // 不额外暴露查看/编辑/日志/健康检查等普通库存操作。
  const showInventoryActions = !isAgentCodingBot;

  // Agent Coding Bot 的「去使用」固定展示。显式协作者/Owner 进入 Coding Chat;
  // 普通空间成员仅有 view/chat 时保持可点击，但降级为基础信息抽屉。
  const codingBotBasicInfoFallback = isAgentCodingBot && !conversationAllowed && !conversationDisabledReason;
  const chatDisabled = codingBotBasicInfoFallback
    ? false
    : !conversationAllowed || (isAgentCodingBot ? false : !inventoryActions.chat?.enabled);
  const chatDisabledReason = codingBotBasicInfoFallback
    ? undefined
    : !conversationAllowed
    ? conversationDisabledReason || '仅 Bot Owner 可进入对话协作'
    : isAgentCodingBot
    ? undefined
    : inventoryActions.chat?.disabledReason;

  const chatButton = (
    <Button
      variant="ghost"
      size="icon"
      aria-label={isAgentCodingBot ? `去使用 ${bot.name}` : `与 ${bot.name} 对话`}
      disabled={chatDisabled}
      onClick={() => {
        if (codingBotBasicInfoFallback) {
          onView(bot);
          return;
        }
        onConversation?.(bot);
      }}
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
      aria-label={`${editLabel} ${bot.name}`}
      disabled={!inventoryActions.edit?.enabled}
      onClick={() => onEdit?.(bot)}
    >
      {isServiceVerification(bot) ? <MessageSquare className="size-3.5" /> : <Pencil className="size-3.5" />}
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

  const taskInstancesButton = (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`查看 ${bot.name} 任务实例`}
      onClick={() => onOpenTaskInstances?.(bot)}
    >
      <ClipboardList className="size-3.5" />
    </Button>
  );

  const showChat = isAgentCodingBot
    ? Boolean(onConversation)
    : Boolean(inventoryActions.chat?.visible && onConversation);
  // Coding Bot 固定展示任务实例入口；普通 Bot 的编辑、日志、任务实例
  // 与健康检查仍按后端 actions / disabled_actions 与本地能力策略渲染。
  const showView = showInventoryActions && Boolean(inventoryActions.view?.visible);
  const showEdit = showInventoryActions && Boolean(inventoryActions.edit?.visible);
  const showLog = showInventoryActions && Boolean(logAction?.visible && onOpenLogs);
  // 后端 routines 端点以 MEMBER 权限校验；普通 Bot 仅 Owner / 显式协作者展示。
  // Coding Bot 按产品口径固定展示，最终读取与操作仍由服务端权限兜底。
  const showTaskInstances = isAgentCodingBot
    ? Boolean(onOpenTaskInstances)
    : showInventoryActions && Boolean(onOpenTaskInstances && canOpenTaskInstances?.(bot));
  const showHealth = showInventoryActions && Boolean(healthCheckAvailability?.visible && onHealthCheck);

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
          label={editLabel}
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
      {showTaskInstances ? <IconAction label="任务实例">{taskInstancesButton}</IconAction> : null}
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
