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
        width: 'w-[clamp(192px,16vw,240px)]',
        cell: (bot) => (
          <BotInfoCell
            bot={bot}
            onClaimLock={onClaimLock}
            onReleaseLock={onReleaseLock}
            engineVersionState={engineVersionStates[botTableEngineVersionKey(bot)]}
            onOpenEngineUpdate={onUpdateEngineVersion}
          />
        ),
      },
      {
        id: 'description',
        header: '描述',
        width: 'w-[clamp(160px,14vw,260px)]',
        cell: (bot: BotDomain) => <BotDescriptionCell bot={bot} />,
      },
      {
        id: 'status',
        header: '状态',
        width: 'w-[clamp(96px,8vw,112px)]',
        cell: (bot) => (
          <BotStatusCell
            bot={bot}
            progressExpanded={expandedProgressIds.has(bot.id)}
            onToggleProgress={() => toggleProgress(bot.id)}
            onManagePublication={onAction ? onManagePublication : undefined}
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
        header: 'Bot 标签',
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
        id: 'actions',
        header: '操作',
        width: 'w-[284px]',
        align: 'end',
        className: 'sticky right-0 z-10 bg-card group-hover:bg-muted/40',
        headerClassName: 'sticky right-0 z-10 bg-muted/50',
        cell: (bot) => {
          const entry = getBotEntryAvailability(bot);
          const collaborationAllowsConversation = canOpenConversation?.(bot) ?? true;
          const primaryCell = (
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
                    : bot.disabledActions?.chat || '申请协作权限后才可对话'
                  : entry.disabledReason
              }
              onOpenLogs={onOpenLogs}
              inventoryActions={getInventoryActions?.(bot)}
              logAction={getLogAction?.(bot)}
            />
          );
          // 三个点与操作链接同处一个 gap-0.5 flex 行，间距由同一 gap 保证
          // 与链接之间完全一致（合并原「更多操作」独立列造成的 td padding 空隙）。
          if (!onAction) return primaryCell;
          const menuKey = botTableEngineVersionKey(bot);
          const generalConfigAvailability = getGeneralConfigAvailability?.(bot);
          return (
            <div className="flex items-center justify-end gap-0.5" onClick={(event) => event.stopPropagation()}>
              {primaryCell}
              <BotManagementMenu
                bot={bot}
                open={openMenuKey === menuKey}
                onOpenChange={(open) =>
                  setOpenMenuKey((current) => (open ? menuKey : current === menuKey ? undefined : current))
                }
                collaborationMode={getCollaborationMode?.(bot)}
                lockedByOther={bot.lock?.status === 'other'}
                onAction={onAction}
                onHealthCheck={onHealthCheck}
                healthCheckAvailability={getHealthCheckAvailability?.(bot)}
                onOpenTaskInstances={onOpenTaskInstances}
                canOpenTaskInstances={canOpenTaskInstances?.(bot)}
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
