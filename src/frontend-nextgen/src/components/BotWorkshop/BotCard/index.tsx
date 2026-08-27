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
  collaborationMode = 'request',
  onEdit,
  onManagePublication,
  onAction,
  onClaimLock,
}) => {
  const showHealthCheck = Boolean(healthCheckAvailability?.visible && onHealthCheck);
  const showEdit = Boolean(onEdit);
  const lockedByOther = bot.lock?.status === 'other';

  return (
    <Card className="flex min-h-64 min-w-0 flex-col overflow-hidden transition-shadow hover:shadow-sm">
      <CardContent className="flex min-w-0 flex-1 flex-col p-4">
        <div className="flex items-start gap-3">
          <BotAvatar name={bot.name} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h3 className="m-0 truncate text-base font-semibold text-[var(--color-fg)]">{bot.name}</h3>
              <BotEditLockAction bot={bot} onClaimLock={onClaimLock} />
              <Badge
                tone={bot.lifecycle === 'running' ? 'success' : bot.lifecycle === 'unknown' ? 'warning' : 'neutral'}
              >
                {lifecycleLabel[bot.lifecycle]}
              </Badge>
            </div>
            <p className="mt-1 line-clamp-2 min-h-8 text-xs leading-4 text-[var(--color-muted)]">
              {bot.description ?? '暂无描述'}
            </p>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap gap-1.5">
          <Badge tone="primary">{bot.runtime.engine}</Badge>
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
        </div>
        <div className="mt-auto pt-4 text-xs text-[var(--color-muted)]">
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
              {onConversation ? (
                <Button
                  variant="ghost"
                  size="sm"
                  leftIcon={<MessageSquare className="size-3.5" />}
                  onClick={() => onConversation(bot)}
                >
                  对话
                </Button>
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
              <Button
                variant="ghost"
                size="sm"
                rightIcon={<ArrowUpRight className="h-3.5 w-3.5" />}
                onClick={() => onView(bot.id)}
              >
                查看
              </Button>
              {showEdit ? (
                <Button
                  variant="ghost"
                  size="sm"
                  leftIcon={<Pencil className="size-3.5" />}
                  disabled={lockedByOther}
                  onClick={() => onEdit?.(bot.id)}
                >
                  编辑
                </Button>
              ) : null}
              {logAction?.visible && onOpenLogs ? (
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
                collaborationMode={collaborationMode}
                lockedByOther={lockedByOther}
                onAction={onAction}
                onManagePublication={onManagePublication}
                onChangeSpace={onChangeSpace}
                onAuthorize={onAuthorize}
              />
            ) : null}
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export default BotCard;
