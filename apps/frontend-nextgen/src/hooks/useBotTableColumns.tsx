import { BotContainerInfo } from '@/components/BotWorkshop/BotCard/BotContainerInfo';
import BotDescriptionCell from '@/components/BotWorkshop/BotCard/BotDescriptionCell';
import BotInfoCell from '@/components/BotWorkshop/BotCard/BotInfoCell';
import BotStatusCell from '@/components/BotWorkshop/BotCard/BotStatusCell';
import BotTagsCell from '@/components/BotWorkshop/BotCard/BotTagsCell';
import BotVersionCell from '@/components/BotWorkshop/BotCard/BotVersionCell';
import { BotTableActionsCell } from '@/components/BotWorkshop/BotCard/BotTableActionsCell';
import type { BotTableColumnOptions } from '@/components/BotWorkshop/BotCard/botTableTypes';
import { type DataTableColumn } from '@/components/ui/DataTable';
import { botTableEngineVersionKey } from '@/hooks/useBotTableEngineVersions';
import type { BotDomain } from '@/services/botWorkshop';
import { useMemo } from 'react';

export function useBotTableColumns(options: BotTableColumnOptions): DataTableColumn<BotDomain>[] {
  const {
    showOwner,
    expandedProgressIds,
    toggleProgress,
    engineVersionStates,
    containerUserId,
    canViewContainers,
    onOpenContainers,
    onClaimLock,
    onReleaseLock,
    onManagePublication,
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
            onManagePublication={onManagePublication}
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
        id: 'tags',
        header: 'Bot 标签',
        width: 'w-[clamp(200px,16vw,264px)]',
        cell: (bot) => (
          <BotTagsCell
            bot={bot}
            engineVersionState={engineVersionStates[botTableEngineVersionKey(bot)]}
            onOpenEngineInfo={onOpenEngineInfo}
          />
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
        width: 'w-[240px]',
        align: 'end',
        sticky: 'right',
        cell: (bot) => <BotTableActionsCell bot={bot} options={options} />,
      },
    ],
    [options],
  );
}
