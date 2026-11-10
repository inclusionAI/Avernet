import BotCopyRetryAction from '@/components/BotWorkshop/BotCard/BotCopyRetryAction';
import { BotManagementMenu } from '@/components/BotWorkshop/BotCard/BotManagementMenu';
import BotPrimaryActionsCell from '@/components/BotWorkshop/BotCard/BotPrimaryActionsCell';
import type { BotTableColumnOptions } from '@/components/BotWorkshop/BotCard/botTableTypes';
import { BOT_COPY_FEATURE_ENABLED } from '@/hooks/useBotWorkshopCopyHost';
import { botTableEngineVersionKey } from '@/hooks/useBotTableEngineVersions';
import { getBotEntryAvailability, type BotDomain } from '@/services/botWorkshop';

interface BotTableActionsCellProps {
  bot: BotDomain;
  options: BotTableColumnOptions;
}

export function BotTableActionsCell({ bot, options }: BotTableActionsCellProps) {
  const {
    canOpenConversation,
    canOpenTaskInstances,
    engineVersionUpdateAvailability,
    getCollaborationMode,
    getCopyAvailability,
    getGeneralConfigAvailability,
    getHealthCheckAvailability,
    getInventoryActions,
    getLogAction,
    onAction,
    onAuthorize,
    onChangeSpace,
    onConversation,
    onCopy,
    onEdit,
    onGeneralConfig,
    onHealthCheck,
    onOpenLogs,
    onOpenEvalHistory,
    onOpenEvaluation,
    onOpenEngineInfo,
    onOpenTaskInstances,
    onView,
    openMenuKey,
    onRetryCopy,
    onUpdateEngineVersion,
    setOpenMenuKey,
  } = options;

  const menuKey = botTableEngineVersionKey(bot);
  const copyState = BOT_COPY_FEATURE_ENABLED ? bot.copyState : undefined;
  if (copyState?.status === 'FAILED' && onRetryCopy) return <BotCopyRetryAction onRetry={() => onRetryCopy(bot)} />;

  const entry = getBotEntryAvailability(bot);
  const collaborationAllowsConversation = canOpenConversation?.(bot) ?? true;
  const primaryCell = (
    <BotPrimaryActionsCell
      bot={bot}
      onView={onView}
      onEdit={onEdit}
      onConversation={onConversation}
      onRequestAccess={onAuthorize}
      collaborationMode={getCollaborationMode?.(bot)}
      conversationAllowed={collaborationAllowsConversation && entry.enabled}
      conversationDisabledReason={
        entry.enabled
          ? collaborationAllowsConversation
            ? undefined
            : bot.disabledActions?.chat || '申请协作权限后才可对话'
          : entry.disabledReason
      }
      onOpenLogs={onOpenLogs}
      inventoryActions={getInventoryActions?.(bot)}
      logAction={getLogAction?.(bot)}
    />
  );
  if (!onAction) return primaryCell;

  const generalConfigAvailability = getGeneralConfigAvailability?.(bot);
  const copyAvailability = getCopyAvailability?.(bot);
  return (
    <div className="flex items-center justify-end gap-0.5" onClick={(event) => event.stopPropagation()}>
      {primaryCell}
      <BotManagementMenu
        bot={bot}
        open={openMenuKey === menuKey}
        onOpenChange={(open) => setOpenMenuKey((current) => (open ? menuKey : current === menuKey ? undefined : current))}
        collaborationMode={getCollaborationMode?.(bot)}
        lockedByOther={bot.lock?.status === 'other'}
        onAction={onAction}
        onOpenEvaluation={onOpenEvaluation}
        onOpenEvalHistory={onOpenEvalHistory}
        onHealthCheck={onHealthCheck}
        healthCheckAvailability={getHealthCheckAvailability?.(bot)}
        onOpenTaskInstances={onOpenTaskInstances}
        canOpenTaskInstances={canOpenTaskInstances?.(bot)}
        onChangeSpace={onChangeSpace}
        onAuthorize={onAuthorize}
        canGeneralConfig={generalConfigAvailability?.enabled}
        generalConfigDisabledReason={generalConfigAvailability?.disabledReason}
        onGeneralConfig={onGeneralConfig}
        copyAvailability={copyAvailability}
        onCopy={onCopy}
        engineVersionUpdateAvailability={engineVersionUpdateAvailability[menuKey]}
        onOpenEngineInfo={onOpenEngineInfo}
        onUpdateEngineVersion={onUpdateEngineVersion}
      />
    </div>
  );
}
