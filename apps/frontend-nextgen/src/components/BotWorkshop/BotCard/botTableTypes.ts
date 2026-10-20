import type { EngineVersionUpdateAvailability } from '@/domain/botEngineVersion';
import type { BotActionAvailability, BotDomain } from '@/domain/botWorkshop';
import type { BotEngineVersionState } from '@/hooks/useBotEngineVersions';
import type { Dispatch, SetStateAction } from 'react';
import type { BotCardManagementAction } from './config';

export type BotInventoryActions = Partial<Record<'view' | 'chat' | 'edit', BotActionAvailability>>;

export interface BotTableProps {
  bots: BotDomain[];
  ariaLabel?: string;
  onRowClick?: (bot: BotDomain) => void;
  onView: (bot: BotDomain) => void;
  onEdit?: (bot: BotDomain) => void;
  onConversation?: (bot: BotDomain) => void;
  canOpenConversation?: (bot: BotDomain) => boolean;
  canEnterDetail?: (bot: BotDomain) => boolean;
  onOpenLogs?: (bot: BotDomain) => void;
  onOpenTaskInstances?: (bot: BotDomain) => void;
  canOpenTaskInstances?: (bot: BotDomain) => boolean;
  onOpenContainers?: (bot: BotDomain) => void;
  canViewContainers?: (bot: BotDomain) => boolean;
  containerUserId?: string;
  onEngineVersionUpdated?: (bot: BotDomain) => Promise<void> | void;
  onHealthCheck?: (bot: BotDomain) => void;
  onChangeSpace?: (bot: BotDomain) => void;
  onAuthorize?: (bot: BotDomain) => void;
  onManagePublication?: (bot: BotDomain) => void;
  onAction?: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  onClaimLock?: (bot: BotDomain) => Promise<void>;
  onReleaseLock?: (bot: BotDomain) => Promise<void>;
  getInventoryActions?: (bot: BotDomain) => BotInventoryActions | undefined;
  getHealthCheckAvailability?: (bot: BotDomain) => BotActionAvailability | undefined;
  getLogAction?: (bot: BotDomain) => BotActionAvailability | undefined;
  getCollaborationMode?: (bot: BotDomain) => 'authorize' | 'request' | undefined;
  getGeneralConfigAvailability?: (bot: BotDomain) => { enabled: boolean; disabledReason?: string } | undefined;
  onGeneralConfig?: (bot: BotDomain) => void;
}

export type BotTableColumnOptions = Pick<
  BotTableProps,
  | 'onView'
  | 'onEdit'
  | 'onConversation'
  | 'canOpenConversation'
  | 'onOpenLogs'
  | 'onOpenTaskInstances'
  | 'canOpenTaskInstances'
  | 'onOpenContainers'
  | 'canViewContainers'
  | 'containerUserId'
  | 'onHealthCheck'
  | 'onChangeSpace'
  | 'onAuthorize'
  | 'onManagePublication'
  | 'onAction'
  | 'onClaimLock'
  | 'onReleaseLock'
  | 'getInventoryActions'
  | 'getHealthCheckAvailability'
  | 'getLogAction'
  | 'getCollaborationMode'
  | 'getGeneralConfigAvailability'
  | 'onGeneralConfig'
> & {
  showOwner: boolean;
  showDescriptionColumn: boolean;
  expandedProgressIds: Set<string>;
  toggleProgress: (botId: string) => void;
  openMenuKey?: string;
  setOpenMenuKey: Dispatch<SetStateAction<string | undefined>>;
  engineVersionStates: Record<string, BotEngineVersionState | undefined>;
  engineVersionUpdateAvailability: Record<string, EngineVersionUpdateAvailability>;
  onOpenEngineInfo: (bot: BotDomain) => void;
  onUpdateEngineVersion: (bot: BotDomain) => void;
};
