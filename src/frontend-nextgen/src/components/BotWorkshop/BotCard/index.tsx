import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card, CardContent } from '@/components/ui/Card';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotActionAvailability, BotDomain } from '@/services/botWorkshop';
import { ArrowUpRight, Boxes, Cloud, FileText, HeartPulse, Laptop, MessageSquare, Pencil, Users } from 'lucide-react';
import React from 'react';
import BotAvatar from '../BotAvatar';
import { BotEditLockAction } from './BotEditLockAction';
import { BotManagementMenu } from './BotManagementMenu';
import { lifecycleLabel, type BotCardManagementAction } from './config';
export interface BotCardProps {
  bot: BotDomain;
  onView: (id: string) => void;
  onConversation?: (bot: BotDomain) => void;
  onHealthCheck?: (bot: BotDomain) => void;
  healthCheckAvailability?: BotActionAvailability;
  logAction?: BotActionAvailability;
  onOpenLogs?: (bot: BotDomain) => void;
  onChangeSpace?: (bot: BotDomain) => void;
  onAuthorize?: (bot: BotDomain) => void;
  collaborationMode?: 'authorize' | 'request';
  onEdit?: (id: string) => void;
  onManagePublication?: (bot: BotDomain) => void;
  onAction?: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  onClaimLock?: (bot: BotDomain) => Promise<void>;
  inventoryActions?: Partial<Record<'view' | 'chat' | 'edit', BotActionAvailability>>;
}

function ActionButton({
  availability,
  children,
}: {
  availability: BotActionAvailability;
  label: string;
  children: React.ReactElement;
}) {
  if (availability.enabled || !availability.disabledReason) return children;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">{children}</span>
        </TooltipTrigger>
        <TooltipContent>{availability.disabledReason}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

const BotCard: React.FC<BotCardProps> = ({
  bot,
  onView,
  onConversation,
  onHealthCheck,
  healthCheckAvailability,
  logAction,
  onOpenLogs,
  onChangeSpace,
  onAuthorize,
  collaborationMode,
  onEdit,
  onManagePublication,
  onAction,
  onClaimLock,
  inventoryActions = {},
}) => {
  const isAgentCodingBot = bot.runtime.isAgentCodingBot;
  const showHealthCheck = !isAgentCodingBot && Boolean(healthCheckAvailability?.visible && onHealthCheck);
  const showEdit = !isAgentCodingBot && Boolean(onEdit);
  const lockedByOther = bot.lock?.status === 'other';
  const failureReason = bot.lifecycle === 'failed' ? bot.disabledActions.restart : undefined;

  return (
    <Card className="flex min-h-64 min-w-0 flex-col overflow-hidden transition-all duration-200 hover:border-brand/40 hover:shadow">
      <CardContent className="flex min-w-0 flex-1 flex-col p-5">
        <div className="flex items-start gap-3">
          <BotAvatar name={bot.name} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="m-0 truncate text-base font-semibold text-foreground">{bot.name}</h3>
              <BotEditLockAction bot={bot} onClaimLock={onClaimLock} />
              {failureReason ? (
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="inline-flex">
                        <Badge tone="error">{lifecycleLabel[bot.lifecycle]}</Badge>
                      </span>
                    </TooltipTrigger>
                    <TooltipContent>{failureReason}</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              ) : (
                <Badge
                  tone={
                    bot.lifecycle === 'running'
                      ? 'success'
                      : bot.lifecycle === 'failed'
                      ? 'error'
                      : bot.lifecycle === 'unknown'
                      ? 'warning'
                      : 'neutral'
                  }
                >
                  {lifecycleLabel[bot.lifecycle]}
                </Badge>
              )}
            </div>
            <p className="mt-1 line-clamp-2 min-h-8 text-xs leading-4 text-muted-foreground">
              {bot.description ?? '暂无描述'}
            </p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-1.5">
          <Badge tone="primary">
            {isAgentCodingBot
              ? bot.runtime.templateName ?? bot.runtime.templateType ?? 'AgentCoding'
              : bot.runtime.engine}
          </Badge>
          <Badge>
            {bot.deployment === 'local' ? (
              <Laptop aria-hidden className="mr-1 size-3" />
            ) : (
              <Cloud aria-hidden className="mr-1 size-3" />
            )}
            {bot.deployment === 'local' ? '本地' : '云端'}
          </Badge>
          <Badge>
            {bot.ownership === 'team' && <Users aria-hidden className="mr-1 size-3" />}
            {bot.ownership === 'team' ? '团队' : '个人'}
          </Badge>
          {bot.serviceMode === 'service' && <Badge tone="success">服务 Bot</Badge>}
          {bot.serviceMode === 'service' && bot.publicationVersion !== undefined ? (
            <Badge tone="primary">V{bot.publicationVersion}</Badge>
          ) : null}
        </div>
        <div className="mt-auto pt-4 text-xs text-muted-foreground">
          <div className="flex min-h-6 flex-wrap items-center gap-x-3 gap-y-1">
            {bot.healthScore !== undefined && (
              <span className="flex items-center gap-1">
                <HeartPulse aria-hidden className="size-3.5" />
                健康分 {bot.healthScore}
              </span>
            )}
            {bot.healthyInstances !== undefined && bot.totalInstances !== undefined && (
              <span className="flex items-center gap-1">
                <Boxes aria-hidden className="size-3.5" />
                实例 {bot.healthyInstances}/{bot.totalInstances}
              </span>
            )}
          </div>
          <div className="mt-3 flex min-w-0 items-start gap-2 border-t border-border pt-3">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
              {isAgentCodingBot ? (
                onConversation ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    leftIcon={<MessageSquare className="size-3.5" />}
                    onClick={() => onConversation(bot)}
                  >
                    去使用
                  </Button>
                ) : null
              ) : inventoryActions.chat?.visible && onConversation ? (
                <ActionButton availability={inventoryActions.chat} label="对话">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={!inventoryActions.chat.enabled}
                    leftIcon={<MessageSquare className="size-3.5" />}
                    onClick={() => onConversation(bot)}
                  >
                    对话
                  </Button>
                </ActionButton>
              ) : null}
              {showHealthCheck ? (
                healthCheckAvailability?.enabled && !lockedByOther ? (
                  <Button
                    variant="ghost"
                    size="sm"
                    leftIcon={<HeartPulse className="h-3.5 w-3.5" />}
                    onClick={() => onHealthCheck?.(bot)}
                  >
                    健康检查
                  </Button>
                ) : (
                  <TooltipProvider>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="inline-flex">
                          <Button variant="ghost" size="sm" leftIcon={<HeartPulse className="h-3.5 w-3.5" />} disabled>
                            健康检查
                          </Button>
                        </span>
                      </TooltipTrigger>
                      <TooltipContent>
                        {lockedByOther ? '该 Bot 正被他人编辑，请先抢锁' : healthCheckAvailability?.disabledReason}
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                )
              ) : null}
              {!isAgentCodingBot && inventoryActions.view?.visible ? (
                <ActionButton availability={inventoryActions.view} label="查看">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={!inventoryActions.view.enabled}
                    rightIcon={<ArrowUpRight className="h-3.5 w-3.5" />}
                    onClick={() => onView(bot.id)}
                  >
                    查看
                  </Button>
                </ActionButton>
              ) : null}
              {showEdit && inventoryActions.edit?.visible ? (
                <ActionButton availability={inventoryActions.edit} label="编辑">
                  <Button
                    variant="ghost"
                    size="sm"
                    leftIcon={<Pencil className="size-3.5" />}
                    disabled={!inventoryActions.edit.enabled}
                    onClick={() => onEdit?.(bot.id)}
                  >
                    编辑
                  </Button>
                </ActionButton>
              ) : null}
              {!isAgentCodingBot && logAction?.visible && onOpenLogs ? (
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <span className="inline-flex">
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={!logAction.enabled}
                          leftIcon={<FileText className="h-3.5 w-3.5" />}
                          onClick={() => onOpenLogs(bot)}
                        >
                          日志
                        </Button>
                      </span>
                    </TooltipTrigger>
                    {!logAction.enabled && logAction.disabledReason ? (
                      <TooltipContent>{logAction.disabledReason}</TooltipContent>
                    ) : null}
                  </Tooltip>
                </TooltipProvider>
              ) : null}
            </div>
            {onAction ? (
              <BotManagementMenu
                bot={bot}
                collaborationMode={isAgentCodingBot ? undefined : collaborationMode}
                lockedByOther={lockedByOther}
                onAction={onAction}
                onManagePublication={onManagePublication}
                onChangeSpace={onChangeSpace}
                onAuthorize={isAgentCodingBot ? undefined : onAuthorize}
              />
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export default BotCard;
