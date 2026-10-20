import { DataTable } from '@/components/ui/DataTable';
import { useBotTableColumns } from '@/hooks/useBotTableColumns';
import { useBotTableEngineVersions } from '@/hooks/useBotTableEngineVersions';
import { getBotEntryAvailability, type BotDomain } from '@/services/botWorkshop';
import React from 'react';
import { DesktopOfflineNotice, DesktopStartProgress } from './DesktopStartProgress';
import type { BotTableProps } from './botTableTypes';

export type { BotInventoryActions, BotTableProps } from './botTableTypes';

const BotTable: React.FC<BotTableProps> = ({
  bots,
  ariaLabel = 'Bot 列表',
  onRowClick,
  onView,
  onEdit,
  onConversation,
  canOpenConversation,
  canEnterDetail,
  onOpenLogs,
  onOpenTaskInstances,
  canOpenTaskInstances,
  onOpenContainers,
  canViewContainers,
  containerUserId,
  onEngineVersionUpdated,
  onHealthCheck,
  onChangeSpace,
  onAuthorize,
  onManagePublication,
  onAction,
  onClaimLock,
  onReleaseLock,
  getInventoryActions,
  getHealthCheckAvailability,
  getLogAction,
  getCollaborationMode,
  getGeneralConfigAvailability,
  onGeneralConfig,
}) => {
  const showOwner = bots.some((bot) => bot.spaceKind === 'team');
  const showDescriptionColumn = bots.some((bot) => (bot.description?.trim().length ?? 0) > 28);
  const [expandedProgressIds, setExpandedProgressIds] = React.useState<Set<string>>(() => new Set());
  const [openMenuKey, setOpenMenuKey] = React.useState<string>();
  const toggleProgress = React.useCallback((botId: string) => {
    setExpandedProgressIds((current) => {
      const next = new Set(current);
      if (next.has(botId)) next.delete(botId);
      else next.add(botId);
      return next;
    });
  }, []);
  const engineVersions = useBotTableEngineVersions({
    bots,
    userId: containerUserId,
    onUpdated: onEngineVersionUpdated,
  });
  const columns = useBotTableColumns({
    showOwner,
    showDescriptionColumn,
    expandedProgressIds,
    toggleProgress,
    openMenuKey,
    setOpenMenuKey,
    engineVersionStates: engineVersions.states,
    engineVersionUpdateAvailability: engineVersions.updateAvailability,
    containerUserId,
    onView,
    onEdit,
    onConversation,
    canOpenConversation,
    onOpenLogs,
    onOpenTaskInstances,
    canOpenTaskInstances,
    onOpenContainers,
    canViewContainers,
    onHealthCheck,
    onChangeSpace,
    onAuthorize,
    onManagePublication,
    onAction,
    onClaimLock,
    onReleaseLock,
    getInventoryActions,
    getHealthCheckAvailability,
    getLogAction,
    getCollaborationMode,
    getGeneralConfigAvailability,
    onGeneralConfig,
    onOpenEngineInfo: engineVersions.openInfo,
    onUpdateEngineVersion: engineVersions.openUpdate,
  });
  const handleRowClick = React.useCallback(
    (bot: BotDomain) => {
      if (!getBotEntryAvailability(bot).enabled) return;
      if (onRowClick) {
        onRowClick(bot);
        return;
      }
      if (canEnterDetail && !canEnterDetail(bot)) {
        onView(bot);
        return;
      }
      if (bot.runtime.isAgentCodingBot && onConversation && (canOpenConversation?.(bot) ?? true)) {
        onConversation(bot);
        return;
      }
      onView(bot);
    },
    [onRowClick, onView, onConversation, canOpenConversation, canEnterDetail],
  );

  return (
    <>
      <DataTable
        columns={columns}
        rows={bots}
        getRowKey={(bot) => bot.cardId ?? bot.entityKey}
        onRowClick={handleRowClick}
        isRowClickable={(bot) => getBotEntryAvailability(bot).enabled}
        renderExpandedRow={(bot) =>
          bot.deployment === 'local' &&
          ['deploying', 'failed', 'offline'].includes(bot.lifecycle) &&
          expandedProgressIds.has(bot.id) ? (
            bot.lifecycle === 'offline' ? (
              <DesktopOfflineNotice botId={bot.id} />
            ) : (
              <DesktopStartProgress botId={bot.id} />
            )
          ) : null
        }
        ariaLabel={ariaLabel}
        minWidth={
          showOwner
            ? showDescriptionColumn
              ? 'min-w-[1260px]'
              : 'min-w-[1080px]'
            : showDescriptionColumn
            ? 'min-w-[1180px]'
            : 'min-w-[1000px]'
        }
      />
      {engineVersions.dialogs}
    </>
  );
};

export default BotTable;
