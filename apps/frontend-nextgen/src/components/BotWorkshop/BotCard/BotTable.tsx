import { DataTable, type DataTableColumn } from '@/components/ui/DataTable';
import { getBotEntryAvailability, type BotActionAvailability, type BotDomain } from '@/services/botWorkshop';
import React from 'react';
import BotDescriptionCell from './BotDescriptionCell';
import BotInfoCell from './BotInfoCell';
import { BotManagementMenu } from './BotManagementMenu';
import BotPrimaryActionsCell from './BotPrimaryActionsCell';
import BotStatusCell from './BotStatusCell';
import BotTagsCell from './BotTagsCell';
import BotVersionCell from './BotVersionCell';
import type { BotCardManagementAction } from './config';
import { DesktopStartProgress } from './DesktopStartProgress';

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
  canOpenConversation?: (bot: BotDomain) => boolean;
  /** 无详情权限时，整行及查看按钮仅打开基础信息，不进入编辑页。 */
  canEnterDetail?: (bot: BotDomain) => boolean;
  onOpenLogs?: (bot: BotDomain) => void;
  onHealthCheck?: (bot: BotDomain) => void;
  onChangeSpace?: (bot: BotDomain) => void;
  onAuthorize?: (bot: BotDomain) => void;
  onManagePublication?: (bot: BotDomain) => void;
  onAction?: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  onClaimLock?: (bot: BotDomain) => Promise<void>;
  onReleaseLock?: (bot: BotDomain) => Promise<void>;

  /** 可用性逐 bot 不同,一律以 accessor 传入,表格内按行求值。 */
  getInventoryActions?: (bot: BotDomain) => BotInventoryActions | undefined;
  getHealthCheckAvailability?: (bot: BotDomain) => BotActionAvailability | undefined;
  getLogAction?: (bot: BotDomain) => BotActionAvailability | undefined;
  getCollaborationMode?: (bot: BotDomain) => 'authorize' | 'request' | undefined;
  /** 通用配置菜单项门禁（collab-permission-entry-migration）；与 onGeneralConfig 成对传入。 */
  getGeneralConfigAvailability?: (bot: BotDomain) => { enabled: boolean; disabledReason?: string } | undefined;
  onGeneralConfig?: (bot: BotDomain) => void;
}

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
  const [expandedProgressIds, setExpandedProgressIds] = React.useState<Set<string>>(() => new Set());
  const toggleProgress = React.useCallback((botId: string) => {
    setExpandedProgressIds((current) => {
      const next = new Set(current);
      if (next.has(botId)) next.delete(botId);
      else next.add(botId);
      return next;
    });
  }, []);
  const columns: DataTableColumn<BotDomain>[] = React.useMemo(
    () => [
      {
        id: 'info',
        header: '机器人信息',
        width: 'w-[280px]',
        cell: (bot) => <BotInfoCell bot={bot} onClaimLock={onClaimLock} onReleaseLock={onReleaseLock} />,
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
        width: 'w-[144px]',
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
        width: 'w-[80px]',
        cell: (bot) => <BotVersionCell bot={bot} />,
      },
      {
        id: 'tags',
        header: 'bot 标签',
        width: 'w-[220px]',
        cell: (bot) => <BotTagsCell bot={bot} />,
      },
      ...(showOwner
        ? [
            {
              id: 'owner',
              header: 'Owner',
              width: 'w-[140px]',
              cell: (bot: BotDomain) => (
                <span className="truncate text-xs text-foreground">{bot.ownerName || bot.ownerId || '—'}</span>
              ),
            },
          ]
        : []),
      {
        id: 'primary-actions',
        header: '主要操作',
        width: 'w-[360px]',
        align: 'end',
        cell: (bot) => {
          const entry = getBotEntryAvailability(bot);
          return (
            <BotPrimaryActionsCell
              bot={bot}
              onView={onView}
              onEdit={onEdit}
              onConversation={onConversation}
              conversationAllowed={(canOpenConversation?.(bot) ?? true) && entry.enabled}
              conversationDisabledReason={entry.enabled ? undefined : entry.disabledReason}
              onOpenLogs={onOpenLogs}
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
        width: 'w-[72px]',
        align: 'end',
        cell: (bot) => {
          if (!onAction) return null;
          const isAgentCodingBot = bot.runtime.isAgentCodingBot;
          const generalConfigAvailability = getGeneralConfigAvailability?.(bot);
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
                canGeneralConfig={generalConfigAvailability?.enabled}
                generalConfigDisabledReason={generalConfigAvailability?.disabledReason}
                onGeneralConfig={onGeneralConfig}
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
      expandedProgressIds,
      toggleProgress,
      getGeneralConfigAvailability,
      onGeneralConfig,
    ],
  );

  const handleRowClick = React.useCallback(
    (bot: BotDomain) => {
      if (onRowClick) {
        onRowClick(bot);
        return;
      }
      // Coding Bot：点击整行与「去使用」一致，直接进入对话（跳转 coding-chat）；
      // 其它引擎仍进详情页。对话不可用时回退详情，避免整行点不开。
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
    <DataTable
      columns={columns}
      rows={bots}
      getRowKey={(bot) => bot.cardId ?? bot.entityKey}
      onRowClick={handleRowClick}
      isRowClickable={(bot) => (canEnterDetail && !canEnterDetail(bot)) || getBotEntryAvailability(bot).enabled}
      renderExpandedRow={(bot) =>
        bot.deployment === 'local' &&
        ['deploying', 'failed'].includes(bot.lifecycle) &&
        expandedProgressIds.has(bot.id) ? (
          <DesktopStartProgress botId={bot.id} />
        ) : null
      }
      ariaLabel={ariaLabel}
    />
  );
};

export default BotTable;
