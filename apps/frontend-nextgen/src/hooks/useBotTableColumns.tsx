import { BotContainerInfo } from '@/components/BotWorkshop/BotCard/BotContainerInfo';
import { BotCopyActionsCell } from '@/components/BotWorkshop/BotCard/BotCopyActionsCell';
import BotDescriptionCell from '@/components/BotWorkshop/BotCard/BotDescriptionCell';
import BotInfoCell from '@/components/BotWorkshop/BotCard/BotInfoCell';
import { BotManagementMenu } from '@/components/BotWorkshop/BotCard/BotManagementMenu';
import BotPrimaryActionsCell from '@/components/BotWorkshop/BotCard/BotPrimaryActionsCell';
import BotStatusCell from '@/components/BotWorkshop/BotCard/BotStatusCell';
import type { BotTableColumnOptions } from '@/components/BotWorkshop/BotCard/botTableTypes';
import BotTagsCell from '@/components/BotWorkshop/BotCard/BotTagsCell';
import BotVersionCell from '@/components/BotWorkshop/BotCard/BotVersionCell';
import { type DataTableColumn } from '@/components/ui/DataTable';
import { BOT_COPY_FEATURE_ENABLED } from '@/hooks/useBotWorkshopCopyHost';
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
    onOpenContainers, canViewContainers,
    onHealthCheck,
    onChangeSpace,
    onAuthorize,
    onPublicationChanged, onPublicationGateBlocked, onOpenEvaluation, onOpenEvalHistory,
    onAction, onClaimLock, onReleaseLock,
    getInventoryActions,
    getHealthCheckAvailability,
    getLogAction,
    getCollaborationMode,
    getGeneralConfigAvailability,
    onGeneralConfig,
    getCopyAvailability,
    onCopy,
    onRetryCopy,
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
            onPublicationChanged={onAction ? onPublicationChanged : undefined}
            onPublicationGateBlocked={onPublicationGateBlocked}
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
      {
        // 标签单行横铺：下限覆盖引擎+部署+服务化 3 chip（~200px），上限兜住 4 chip 极值
        id: 'tags',
        header: 'Bot 标签',
        width: 'w-[clamp(200px,16vw,264px)]',
        cell: (bot) => {
          const key = botTableEngineVersionKey(bot);
          return (
            <BotTagsCell bot={bot} engineVersionState={engineVersionStates[key]} onOpenEngineInfo={onOpenEngineInfo} />
          );
        },
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
        // 240px：最宽按钮行（对话/查看/编辑/日志 + 管理菜单）实测内容约 224px，留 16px 余量。
        width: 'w-[240px]',
        align: 'end',
        sticky: 'right',
        cell: (bot) => {
          const copyState = BOT_COPY_FEATURE_ENABLED ? bot.copyState : undefined; // 非空 → 收敛为重试(FAILED)+删除,见 BotCopyActionsCell
          if (copyState)
            return <BotCopyActionsCell bot={bot} copyState={copyState} onRetryCopy={onRetryCopy} onAction={onAction} />;
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
          // 三个点与操作链接同处一个 gap-0.5 flex 行，间距由同一 gap 保证
          // 与链接之间完全一致（合并原「更多操作」独立列造成的 td padding 空隙）。
          if (!onAction) return primaryCell;
          const menuKey = botTableEngineVersionKey(bot);
          const generalConfigAvailability = getGeneralConfigAvailability?.(bot);
          const copyAvailability = getCopyAvailability?.(bot);
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
                onOpenEvaluation={onOpenEvaluation}
                onOpenEvalHistory={onOpenEvalHistory}
                copyAvailability={copyAvailability}
                onCopy={onCopy}
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
      onPublicationChanged,
      onPublicationGateBlocked,
      onOpenEvaluation,
      onOpenEvalHistory,
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
      getCopyAvailability,
      onCopy,
      onRetryCopy,
      onOpenEngineInfo,
      onUpdateEngineVersion,
      engineVersionStates,
      engineVersionUpdateAvailability,
    ],
  );
}
