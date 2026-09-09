import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import type { BotActionAvailability, BotDomain } from '@/services/botWorkshop';
import React from 'react';
import BotDescriptionCell from './BotDescriptionCell';
import BotInfoCell from './BotInfoCell';
import { BotManagementMenu } from './BotManagementMenu';
import BotPrimaryActionsCell from './BotPrimaryActionsCell';
import BotStatusCell from './BotStatusCell';
import BotTagsCell from './BotTagsCell';
import BotVersionCell from './BotVersionCell';
import type { BotCardManagementAction } from './config';

export type BotInventoryActions = Partial<Record<'view' | 'chat' | 'edit', BotActionAvailability>>;

export interface BotTableProps {
  bots: BotDomain[];
  /** 表格可访问名称 */
  ariaLabel?: string;
  /** 行点击回调;缺省走 onView */
  onRowClick?: (bot: BotDomain) => void;

  onView: (bot: BotDomain) => void;
  onEdit?: (bot: BotDomain) => void;
  onConversation?: (bot: BotDomain) => void;
  onOpenLogs?: (bot: BotDomain) => void;
  onHealthCheck?: (bot: BotDomain) => void;
  onChangeSpace?: (bot: BotDomain) => void;
  onAuthorize?: (bot: BotDomain) => void;
  onManagePublication?: (bot: BotDomain) => void;
  onAction?: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  onClaimLock?: (bot: BotDomain) => Promise<void>;

  /** 可用性逐 bot 不同,一律以 accessor 传入,表格内按行求值。 */
  getInventoryActions?: (bot: BotDomain) => BotInventoryActions | undefined;
  getHealthCheckAvailability?: (bot: BotDomain) => BotActionAvailability | undefined;
  getLogAction?: (bot: BotDomain) => BotActionAvailability | undefined;
  getCollaborationMode?: (bot: BotDomain) => 'authorize' | 'request' | undefined;
}

const BotTable: React.FC<BotTableProps> = ({
  bots,
  ariaLabel = 'Bot 列表',
  onRowClick,
  onView,
  onEdit,
  onConversation,
  onOpenLogs,
  onHealthCheck,
  onChangeSpace,
  onAuthorize,
  onManagePublication,
  onAction,
  onClaimLock,
  getInventoryActions,
  getHealthCheckAvailability,
  getLogAction,
  getCollaborationMode,
}) => {
  const columns: DataTableColumn<BotDomain>[] = React.useMemo(
    () => [
      {
        id: 'info',
        header: '机器人信息',
        width: 'w-[280px]',
        cell: (bot) => <BotInfoCell bot={bot} onClaimLock={onClaimLock} />,
      },
      {
        // 不写死宽度:table-fixed 下由该列吸收剩余空间
        id: 'description',
        header: '描述',
        cell: (bot) => <BotDescriptionCell bot={bot} />,
      },
      {
        id: 'status',
        header: '状态',
        width: 'w-[96px]',
        cell: (bot) => <BotStatusCell bot={bot} />,
      },
      {
        id: 'version',
        header: '版本',
        width: 'w-[80px]',
        cell: (bot) => <BotVersionCell bot={bot} />,
      },
      {
        id: 'tags',
        header: 'bot 标签',
        width: 'w-[220px]',
        cell: (bot) => <BotTagsCell bot={bot} />,
      },
      {
        id: 'primary-actions',
        header: '主要操作',
        width: 'w-[360px]',
        align: 'end',
        cell: (bot) => (
          <BotPrimaryActionsCell
            bot={bot}
            onView={onView}
            onEdit={onEdit}
            onConversation={onConversation}
            onOpenLogs={onOpenLogs}
            onHealthCheck={onHealthCheck}
            inventoryActions={getInventoryActions?.(bot)}
            healthCheckAvailability={getHealthCheckAvailability?.(bot)}
            logAction={getLogAction?.(bot)}
          />
        ),
      },
      {
        id: 'more-actions',
        header: <span className="sr-only">更多操作</span>,
        width: 'w-[72px]',
        align: 'end',
        cell: (bot) => {
          if (!onAction) return null;
          const isAgentCodingBot = bot.runtime.isAgentCodingBot;
          return (
            <div className="flex justify-end" onClick={(event) => event.stopPropagation()}>
              <BotManagementMenu
                bot={bot}
                collaborationMode={isAgentCodingBot ? undefined : getCollaborationMode?.(bot)}
                lockedByOther={bot.lock?.status === 'other'}
                onAction={onAction}
                onManagePublication={onManagePublication}
                onChangeSpace={onChangeSpace}
                onAuthorize={isAgentCodingBot ? undefined : onAuthorize}
              />
            </div>
          );
        },
      },
    ],
    [
      onView,
      onEdit,
      onConversation,
      onOpenLogs,
      onHealthCheck,
      onChangeSpace,
      onAuthorize,
      onManagePublication,
      onAction,
      onClaimLock,
      getInventoryActions,
      getHealthCheckAvailability,
      getLogAction,
      getCollaborationMode,
    ],
  );

  const handleRowClick = React.useCallback(
    (bot: BotDomain) => {
      if (onRowClick) {
        onRowClick(bot);
        return;
      }
      onView(bot);
    },
    [onRowClick, onView],
  );

  return (
    <DataTable
      columns={columns}
      rows={bots}
      getRowKey={(bot) => bot.cardId ?? bot.entityKey}
      onRowClick={handleRowClick}
      ariaLabel={ariaLabel}
    />
  );
};

export default BotTable;
