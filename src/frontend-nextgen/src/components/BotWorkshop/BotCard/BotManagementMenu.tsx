import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { restartPublishStageLabel, restartPublishStageOf } from '@/domain/botWorkshop';
import type { BotDomain } from '@/services/botWorkshop';
import { ArrowUpRight, CircleHelp, MapPin, MoreHorizontal, Users } from 'lucide-react';
import { useState } from 'react';
import { actionIcon, actionLabel, type BotCardManagementAction } from './config';

interface BotManagementMenuProps {
  bot: BotDomain;
  collaborationMode?: 'authorize' | 'request';
  lockedByOther: boolean;
  onAction: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  onManagePublication?: (bot: BotDomain) => void;
  onChangeSpace?: (bot: BotDomain) => void;
  onAuthorize?: (bot: BotDomain) => void;
}

function ActionHelp({ children, description }: { children: string; description: string }) {
  return (
    <span className="flex min-w-0 flex-1 items-center justify-between gap-2">
      {children}
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <span tabIndex={0} aria-label={`${children}说明`} className="inline-flex shrink-0 text-muted-foreground">
              <CircleHelp className="size-3.5" />
            </span>
          </TooltipTrigger>
          <TooltipContent className="max-w-72">{description}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </span>
  );
}

export function BotManagementMenu(props: BotManagementMenuProps) {
  const { bot, collaborationMode, lockedByOther, onAction, onManagePublication, onChangeSpace, onAuthorize } = props;
  const [open, setOpen] = useState(false);
  const [confirmAction, setConfirmAction] = useState<BotCardManagementAction>();
  const [confirming, setConfirming] = useState(false);
  const isAgentCodingBot = bot.runtime.isAgentCodingBot;
  const isServiceBot = bot.serviceMode === 'service';
  // 重启词表（Avernet PR #1911）：动作名即路由键。服务卡由后端词表决定三个
  // 重启动词各自落在哪张卡（draft→restart，predeploy/online→restart_publish）；词表与
  // disabled_actions 均未声明时不渲染（对齐 bot-workshop-page.md §10.3 卡片操作口径）。
  const restartDeclared = bot.actions.includes('restart') || Boolean(bot.disabledActions.restart);
  const restartPublishDeclared =
    bot.actions.includes('restart_publish') || Boolean(bot.disabledActions.restart_publish);
  const restartPublishDisabledReason = bot.disabledActions.restart_publish;
  const restartDisabledReason = bot.disabledActions.restart;
  const restartPublishStage = confirmAction === 'restart_publish' ? restartPublishStageOf(bot.lifecycle) : undefined;
  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`管理 ${bot.name}`}
            leftIcon={<MoreHorizontal className="size-4" />}
          />
        </PopoverTrigger>
        <PopoverContent align="end" className="w-52 space-y-1 p-2">
          {bot.serviceMode === 'service' && onManagePublication ? (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              leftIcon={<ArrowUpRight className="size-4" />}
              disabled={lockedByOther}
              onClick={() => {
                setOpen(false);
                onManagePublication(bot);
              }}
            >
              发布与阶段推进
            </Button>
          ) : null}
          {bot.serviceMode === 'non-service' && bot.deployment === 'cloud' && bot.canUpgradeToService ? (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              leftIcon={actionIcon.upgrade}
              disabled={lockedByOther}
              onClick={() => {
                setOpen(false);
                setConfirmAction('upgrade');
              }}
            >
              {actionLabel.upgrade}
            </Button>
          ) : null}
          {!isServiceBot || restartDeclared ? (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              leftIcon={actionIcon.restart}
              disabled={lockedByOther || !bot.actions.includes('restart')}
              onClick={() => {
                setOpen(false);
                setConfirmAction('restart');
              }}
            >
              <ActionHelp
                description={
                  restartDisabledReason && !bot.actions.includes('restart')
                    ? restartDisabledReason
                    : '指重新启动当前 Bot 实例，重新加载当前会话状态、配置或运行流程。'
                }
              >
                {actionLabel.restart}
              </ActionHelp>
            </Button>
          ) : null}
          {restartPublishDeclared ? (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              leftIcon={actionIcon.restart_publish}
              disabled={lockedByOther || !bot.actions.includes('restart_publish')}
              onClick={() => {
                setOpen(false);
                setConfirmAction('restart_publish');
              }}
            >
              <ActionHelp
                description={
                  restartPublishDisabledReason && !bot.actions.includes('restart_publish')
                    ? restartPublishDisabledReason
                    : '指重新启动该服务已发布环境（预发/线上）的运行时，不影响草稿机器。'
                }
              >
                {actionLabel.restart_publish}
              </ActionHelp>
            </Button>
          ) : null}
          {!isAgentCodingBot && !isServiceBot ? (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              leftIcon={actionIcon.engine_restart}
              disabled={lockedByOther || !bot.actions.includes('engine_restart')}
              onClick={() => {
                setOpen(false);
                setConfirmAction('engine_restart');
              }}
            >
              <ActionHelp description="指重新启动 Bot 所依赖的底层运行引擎（如 OpenClaw、ClaudeCode 等）。">
                {actionLabel.engine_restart}
              </ActionHelp>
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={<MapPin className="size-4" />}
            disabled={lockedByOther}
            onClick={() => {
              setOpen(false);
              onChangeSpace?.(bot);
            }}
          >
            变更归属空间
          </Button>
          {!isAgentCodingBot && collaborationMode && onAuthorize ? (
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              leftIcon={<Users className="size-4" />}
              onClick={() => {
                setOpen(false);
                onAuthorize(bot);
              }}
            >
              {collaborationMode === 'authorize' ? '授权' : '申请操作权限'}
            </Button>
          ) : null}
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start text-destructive"
            leftIcon={actionIcon.delete}
            disabled={lockedByOther || !bot.actions.includes('delete')}
            onClick={() => {
              setOpen(false);
              setConfirmAction('delete');
            }}
          >
            {actionLabel.delete}
          </Button>
        </PopoverContent>
      </Popover>
      <ConfirmDialog
        open={Boolean(confirmAction)}
        loading={confirming}
        title={confirmAction === 'delete' ? '确认删除 Bot' : confirmAction ? actionLabel[confirmAction] : '确认'}
        description={
          confirmAction === 'delete'
            ? `删除「${bot.name}」后无法恢复。`
            : confirmAction === 'upgrade'
            ? '开启后不可逆，确认将此 Bot 转换为服务 Bot？'
            : confirmAction === 'engine_restart'
            ? '仅重启引擎进程，不重建容器。'
            : confirmAction === 'restart_publish'
            ? restartPublishStage
              ? `将重启该服务在${restartPublishStageLabel[restartPublishStage]}环境发布的运行时，进行中的服务会话可能短暂中断；草稿机器与草稿数据不受影响。`
              : '当前发布状态不支持重启发布，请刷新 Bot 列表后重试。'
            : '将重新拉起整个 Bot 容器，现有会话可能中断。'
        }
        confirmText={confirmAction === 'delete' ? '删除' : '确认'}
        confirmVariant={confirmAction === 'delete' ? 'destructive' : 'primary'}
        onCancel={() => setConfirmAction(undefined)}
        onConfirm={async () => {
          if (!confirmAction) return;
          setConfirming(true);
          try {
            await onAction(confirmAction, bot);
            setConfirmAction(undefined);
          } catch {
            // 失败提示已由 Hook toast 统一负责；保留弹窗供用户取消或重试，同时避免 rethrow 变成未处理拒绝。
          } finally {
            setConfirming(false);
          }
        }}
      />
    </>
  );
}
