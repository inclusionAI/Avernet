import { BotContainerInfo } from '@/components/BotWorkshop/BotCard/BotContainerInfo';
import BotDescriptionCell from '@/components/BotWorkshop/BotCard/BotDescriptionCell';
import BotInfoCell from '@/components/BotWorkshop/BotCard/BotInfoCell';
import { BotManagementMenu } from '@/components/BotWorkshop/BotCard/BotManagementMenu';
import BotPrimaryActionsCell from '@/components/BotWorkshop/BotCard/BotPrimaryActionsCell';
import BotStatusCell from '@/components/BotWorkshop/BotCard/BotStatusCell';
import type { BotTableColumnOptions } from '@/components/BotWorkshop/BotCard/botTableTypes';
import BotTagsCell from '@/components/BotWorkshop/BotCard/BotTagsCell';
import BotVersionCell from '@/components/BotWorkshop/BotCard/BotVersionCell';
import { type DataTableColumn } from '@/components/ui/DataTable';
import { botTableEngineVersionKey } from '@/hooks/useBotTableEngineVersions';
import { getBotEntryAvailability, type BotDomain } from '@/services/botWorkshop';
import { useMemo } from 'react';

export function useBotTableColumns(options: BotTableColumnOptions): DataTableColumn<BotDomain>[] {
  const {
    showOwner,
    showDescriptionColumn,
    expandedProgressIds,
    toggleProgress,
    openMenuKey,
    setOpenMenuKey,
    engineVersionStates,
    engineVersionUpdateAvailability,
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
    onOpenEngineInfo,
    onUpdateEngineVersion,
  } = options;

  return useMemo(
    () => [
      {
        id: 'info',
        header: 'Bot信息',
        width: showDescriptionColumn ? 'w-[clamp(192px,16vw,240px)]' : 'w-[clamp(224px,19vw,320px)]',
        cell: (bot) => (
          <BotInfoCell
            bot={bot}
            compactDescription={!showDescriptionColumn}
            onClaimLock={onClaimLock}
            onReleaseLock={onReleaseLock}
          />
        ),
      },
      ...(showDescriptionColumn
        ? [
            {
              id: 'description',
              header: '描述',
              width: 'w-[clamp(160px,14vw,240px)]',
              cell: (bot: BotDomain) => <BotDescriptionCell bot={bot} />,
            },
          ]
        : []),
      {
        id: 'status',
        header: '状态',
        width: 'w-[clamp(96px,8vw,112px)]',
        cell: (bot) => (
          <BotStatusCell
            bot={bot}
            progressExpanded={expandedProgressIds.has(bot.id)}
            onToggleProgress={() => toggleProgress(bot.id)}
          />
        ),
      },
      {
        id: 'version',
        header: '版本',
        width: 'w-[clamp(60px,5vw,72px)]',
        cell: (bot) => <BotVersionCell bot={bot} />,
      },
      {
        id: 'tags',
        header: 'bot 标签',
        width: 'w-[clamp(120px,10vw,160px)]',
        cell: (bot) => {
          const key = botTableEngineVersionKey(bot);
          return (
            <BotTagsCell bot={bot} engineVersionState={engineVersionStates[key]} onOpenEngineInfo={onOpenEngineInfo} />
          );
        },
      },
      {
        id: 'containers',
        header: '容器信息',
        width: 'w-[clamp(108px,9vw,128px)]',
        cell: (bot) =>
          onOpenContainers ? (
            <BotContainerInfo
              bot={bot}
              userId={containerUserId}
              canView={canViewContainers?.(bot) ?? true}
              onOpen={onOpenContainers}
            />
          ) : (
            <span>—</span>
          ),
      },
      ...(showOwner
        ? [
            {
              id: 'owner',
              header: 'Owner',
              width: 'w-[clamp(96px,8vw,120px)]',
              cell: (bot: BotDomain) => (
                <span className="truncate text-xs text-foreground">{bot.ownerName || bot.ownerId || '—'}</span>
              ),
            },
          ]
        : []),
      {
        id: 'primary-actions',
        header: '主要操作',
        width: 'w-[224px]',
        align: 'end',
        className: 'sticky right-12 z-10 bg-card group-hover:bg-muted/40',
        cell: (bot) => {
          const entry = getBotEntryAvailability(bot);
          const collaborationAllowsConversation = canOpenConversation?.(bot) ?? true;
          return (
            <BotPrimaryActionsCell
              bot={bot}
              onView={onView}
              onEdit={onEdit}
              onConversation={onConversation}
              conversationAllowed={collaborationAllowsConversation && entry.enabled}
              conversationDisabledReason={
                entry.enabled
                  ? collaborationAllowsConversation
                    ? undefined
                    : '申请协作权限后才可对话'
                  : entry.disabledReason
              }
              onOpenLogs={onOpenLogs}
              onOpenTaskInstances={onOpenTaskInstances}
              canOpenTaskInstances={canOpenTaskInstances}
              onHealthCheck={onHealthCheck}
              inventoryActions={getInventoryActions?.(bot)}
              healthCheckAvailability={getHealthCheckAvailability?.(bot)}
              logAction={getLogAction?.(bot)}
            />
          );
        },
      },
      {
        id: 'more-actions',
        header: <span className="sr-only">更多操作</span>,
        width: 'w-[48px]',
        align: 'end',
        className: 'sticky right-0 z-10 bg-card group-hover:bg-muted/40',
        cell: (bot) => {
          if (!onAction) return null;
          const menuKey = botTableEngineVersionKey(bot);
          const generalConfigAvailability = getGeneralConfigAvailability?.(bot);
          return (
            <div className="flex justify-end" onClick={(event) => event.stopPropagation()}>
              <BotManagementMenu
                bot={bot}
                open={openMenuKey === menuKey}
                onOpenChange={(open) =>
                  setOpenMenuKey((current) => (open ? menuKey : current === menuKey ? undefined : current))
                }
                collaborationMode={getCollaborationMode?.(bot)}
                lockedByOther={bot.lock?.status === 'other'}
                onAction={onAction}
                onManagePublication={onManagePublication}
                onChangeSpace={onChangeSpace}
                onAuthorize={onAuthorize}
                canGeneralConfig={generalConfigAvailability?.enabled}
                generalConfigDisabledReason={generalConfigAvailability?.disabledReason}
                onGeneralConfig={onGeneralConfig}
                engineVersionUpdateAvailability={engineVersionUpdateAvailability[menuKey]}
                onOpenEngineInfo={onOpenEngineInfo}
                onUpdateEngineVersion={onUpdateEngineVersion}
              />
            </div>
          );
        },
      },
    ],
    [
      showOwner,
      showDescriptionColumn,
      onView,
      onEdit,
      onConversation,
      canOpenConversation,
      onOpenLogs,
      onOpenTaskInstances,
      canOpenTaskInstances,
      onOpenContainers,
      canViewContainers,
      containerUserId,
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
      openMenuKey,
      expandedProgressIds,
      toggleProgress,
      getGeneralConfigAvailability,
      onGeneralConfig,
      onOpenEngineInfo,
      onUpdateEngineVersion,
      engineVersionStates,
      engineVersionUpdateAvailability,
    ],
  );
}
