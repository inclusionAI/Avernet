import { Button } from '@/components/ui/Button';
import type { BotDomain } from '@/domain/botWorkshop';
import { ArrowUpRight } from 'lucide-react';
import {
  canRestartFailedServiceBot,
  failedServiceDeleteReason,
  publicationManagementAvailability,
} from './botManagementPolicy';
import { actionIcon, actionLabel, type BotCardManagementAction } from './config';
import { ActionHelp, MenuSection } from './menuPrimitives';

interface BotManagementRuntimeItemsProps {
  bot: BotDomain;
  lockedByOther: boolean;
  onClose: () => void;
  onConfirm: (action: BotCardManagementAction) => void;
  onManagePublication?: (bot: BotDomain) => void;
}

export function BotManagementRuntimeItems({
  bot,
  lockedByOther,
  onClose,
  onConfirm,
  onManagePublication,
}: BotManagementRuntimeItemsProps) {
  const isAgentCodingBot = bot.runtime.isAgentCodingBot;
  const isServiceBot = bot.serviceMode === 'service';
  const restartUnsupported = bot.runtime.engine === 'teclaw';
  const restartDeclared =
    bot.actions.includes('restart') || Boolean(bot.disabledActions.restart) || canRestartFailedServiceBot(bot);
  const restartPublishDeclared =
    bot.actions.includes('restart_publish') || Boolean(bot.disabledActions.restart_publish);
  const restartPublishDisabledReason = bot.disabledActions.restart_publish;
  const restartDisabledReason = bot.disabledActions.restart;
  const engineRestartDeclared = bot.actions.includes('engine_restart') || Boolean(bot.disabledActions.engine_restart);
  const showEngineRestart = !isAgentCodingBot && (restartUnsupported || !isServiceBot || engineRestartDeclared);
  const engineRestartEnabled = bot.actions.includes('engine_restart');
  const engineRestartDisabledReason = bot.disabledActions.engine_restart;
  const publication = publicationManagementAvailability(bot);
  const restartEnabled = bot.actions.includes('restart') || canRestartFailedServiceBot(bot);
  const deleteReason = failedServiceDeleteReason(bot);

  return (
    <>
      <MenuSection divider>
        {bot.serviceMode === 'service' && publication.visible && onManagePublication ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={<ArrowUpRight className="size-4" />}
            disabled={lockedByOther || !publication.enabled}
            onClick={() => {
              onClose();
              onManagePublication(bot);
            }}
          >
            {publication.enabled ? (
              '发布与阶段推进'
            ) : (
              <ActionHelp description={publication.disabledReason!}>发布与阶段推进</ActionHelp>
            )}
          </Button>
        ) : null}
        {restartUnsupported || !isServiceBot || restartDeclared ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={actionIcon.restart}
            disabled={restartUnsupported || lockedByOther || !restartEnabled}
            onClick={() => {
              onClose();
              onConfirm('restart');
            }}
          >
            <ActionHelp
              description={
                restartUnsupported
                  ? 'TeClaw 暂不支持重启容器'
                  : restartDisabledReason && !bot.actions.includes('restart')
                  ? restartDisabledReason
                  : 'Bot 卡住或没有响应时，可以尝试重启。不改变 Bot 已保存的配置和发布版本'
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
            disabled={restartUnsupported || lockedByOther || !bot.actions.includes('restart_publish')}
            onClick={() => {
              onClose();
              onConfirm('restart_publish');
            }}
          >
            <ActionHelp
              description={
                restartUnsupported
                  ? 'TeClaw 暂不支持重启发布'
                  : restartPublishDisabledReason && !bot.actions.includes('restart_publish')
                  ? restartPublishDisabledReason
                  : '指重新启动该服务已发布环境（预发/线上）的运行时，不影响草稿机器。'
              }
            >
              {actionLabel.restart_publish}
            </ActionHelp>
          </Button>
        ) : null}
        {showEngineRestart ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full justify-start"
            leftIcon={actionIcon.engine_restart}
            disabled={restartUnsupported || lockedByOther || !engineRestartEnabled}
            onClick={() => {
              onClose();
              onConfirm('engine_restart');
            }}
          >
            <ActionHelp
              description={
                restartUnsupported
                  ? 'TeClaw 暂不支持重启引擎'
                  : engineRestartDisabledReason && !engineRestartEnabled
                  ? engineRestartDisabledReason
                  : '将 Bot 使用的引擎更新到最新版本，并重新启动。不改变 Bot 已保存的配置和发布版本'
              }
            >
              {actionLabel.engine_restart}
            </ActionHelp>
          </Button>
        ) : null}
      </MenuSection>
      <MenuSection divider>
        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-start text-destructive"
          leftIcon={actionIcon.delete}
          disabled={lockedByOther || !bot.actions.includes('delete')}
          onClick={() => {
            onClose();
            onConfirm('delete');
          }}
        >
          {deleteReason ? <ActionHelp description={deleteReason}>{actionLabel.delete}</ActionHelp> : actionLabel.delete}
        </Button>
      </MenuSection>
    </>
  );
}
