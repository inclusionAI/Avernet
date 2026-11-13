import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotActionAvailability, BotDomain } from '@/services/botWorkshop';
import type { BotCollaborationMode } from '@/services/botWorkshop/botPolicy';
import { isServiceVerification } from '@/services/botWorkshop/botPolicy';
import React from 'react';
import { Points } from '@/tracking/points';
import { Tracert } from '@/utils/tracert';

export interface BotPrimaryActionsCellProps {
  bot: BotDomain;
  onConversation?: (bot: BotDomain) => void;
  onRequestAccess?: (bot: BotDomain) => void;
  collaborationMode?: BotCollaborationMode;
  conversationAllowed?: boolean;
  conversationDisabledReason?: string;
  onView: (bot: BotDomain) => void;
  onEdit?: (bot: BotDomain) => void;
  onOpenLogs?: (bot: BotDomain) => void;
  inventoryActions?: Partial<Record<'view' | 'chat' | 'edit', BotActionAvailability>>;
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

/**
 * 核心操作（对话/查看/编辑/日志）按 dmore 稿以蓝色文字链接呈现（13px）；
 * 任务实例/健康检查/管理类操作统一收口到行尾「三个点」气泡菜单
 * （见 BotManagementMenu，设计稿 artboard-087）。
 * linkClass 需覆盖 ghost 默认 hover 色，显式回落 primary。
 */
const linkClass = 'h-7 rounded-md px-1.5 text-[13px] font-normal text-primary hover:bg-primary/10 hover:text-primary';

const BotPrimaryActionsCell: React.FC<BotPrimaryActionsCellProps> = ({
  bot,
  onConversation,
  onRequestAccess,
  collaborationMode,
  conversationAllowed = true,
  conversationDisabledReason,
  onView,
  onEdit,
  onOpenLogs,
  inventoryActions = {},
  logAction,
}) => {
  if (bot.lifecycle === 'deploying') return null;
  const isAgentCodingBot = bot.runtime.isAgentCodingBot;
  const editLabel = isServiceVerification(bot) ? '预发验证' : '编辑';
  // Coding Bot 在 Bot 管理页展示「去使用」；任务实例随管理类操作
  // 收口在「三个点」气泡菜单，普通库存操作（查看/编辑/日志）不展示。
  const showInventoryActions = !isAgentCodingBot;

  // 团队空间里的普通空间成员不能使用 Coding Bot。此时不复用「去使用」
  // 降级查看，而是直接提供已有的操作权限申请入口。
  const shouldRequestAccess =
    isAgentCodingBot && bot.spaceKind === 'team' && collaborationMode === 'request' && Boolean(onRequestAccess);
  // 未命中团队空间申请态时保留原有降级策略，避免影响个人空间和普通 Bot。
  const codingBotBasicInfoFallback =
    isAgentCodingBot && !shouldRequestAccess && !conversationAllowed && !conversationDisabledReason;
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

  const primaryChatLabel = shouldRequestAccess ? '去申请' : isAgentCodingBot ? '去使用' : '对话';
  const chatButton = (
    <Button
      variant="ghost"
      className={linkClass}
      aria-label={isAgentCodingBot ? `${primaryChatLabel} ${bot.name}` : `与 ${bot.name} 对话`}
      disabled={shouldRequestAccess ? false : chatDisabled}
      onClick={() => {
        if (shouldRequestAccess) {
          onRequestAccess?.(bot);
          return;
        }
        if (codingBotBasicInfoFallback) {
          onView(bot);
          return;
        }
        onConversation?.(bot);
      }}
    >
      {primaryChatLabel}
    </Button>
  );

  const viewButton = (
    <Button
      variant="ghost"
      className={linkClass}
      aria-label={`查看 ${bot.name} 详情`}
      disabled={!inventoryActions.view?.enabled}
      onClick={() => onView(bot)}
    >
      查看
    </Button>
  );

  const editButton = (
    <Button
      variant="ghost"
      className={linkClass}
      aria-label={`${editLabel} ${bot.name}`}
      disabled={!inventoryActions.edit?.enabled}
      onClick={() => onEdit?.(bot)}
    >
      {editLabel}
    </Button>
  );

  const logButton = (
    <Button
      variant="ghost"
      className={linkClass}
      aria-label={`查看 ${bot.name} 日志`}
      disabled={!logAction?.enabled}
      onClick={() => {
        Tracert.click(Points.botWorkshop.viewLogs, { bot_id: bot.id });
        onOpenLogs?.(bot);
      }}
    >
      日志
    </Button>
  );

  const showChat = isAgentCodingBot
    ? Boolean(onConversation || onRequestAccess)
    : Boolean(inventoryActions.chat?.visible && onConversation);
  // 编辑与日志仍按后端 actions / disabled_actions 渲染；任务实例/健康检查
  // 已随管理类操作收口至「三个点」气泡菜单。
  const showView = showInventoryActions && Boolean(inventoryActions.view?.visible);
  const showEdit = showInventoryActions && Boolean(inventoryActions.edit?.visible);
  const showLog = showInventoryActions && Boolean(logAction?.visible && onOpenLogs);

  return (
    <div
      className="flex items-center justify-end gap-0.5"
      onClick={(event) => event.stopPropagation()}
      role="group"
      aria-label={`${bot.name} 操作`}
    >
      {showChat ? (
        <IconAction
          label={primaryChatLabel}
          disabled={shouldRequestAccess ? false : chatDisabled}
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
    </div>
  );
};

export default BotPrimaryActionsCell;
