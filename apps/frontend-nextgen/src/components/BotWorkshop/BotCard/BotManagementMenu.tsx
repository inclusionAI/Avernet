import { Button } from '@/components/ui/Button';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import type { EngineVersionUpdateAvailability } from '@/domain/botEngineVersion';
import type { BotDomain } from '@/domain/botWorkshop';
import { MapPin, MoreHorizontal, ShieldCheck, Users } from 'lucide-react';
import { useState } from 'react';
import { BotEngineVersionMenuItems } from './BotEngineVersionMenuItems';
import { BotManagementConfirmDialog } from './BotManagementConfirmDialog';
import { BotManagementRuntimeItems } from './BotManagementRuntimeItems';
import { actionIcon, actionLabel, type BotCardManagementAction } from './config';
import { ActionHelp, MenuSection } from './menuPrimitives';
interface BotManagementMenuProps {
  bot: BotDomain;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  collaborationMode?: 'authorize' | 'request';
  lockedByOther: boolean;
  onAction: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  onManagePublication?: (bot: BotDomain) => void;
  onChangeSpace?: (bot: BotDomain) => void;
  onAuthorize?: (bot: BotDomain) => void;
  canGeneralConfig?: boolean;
  generalConfigDisabledReason?: string;
  onGeneralConfig?: (bot: BotDomain) => void;
  engineVersionUpdateAvailability?: EngineVersionUpdateAvailability;
  onOpenEngineInfo?: (bot: BotDomain) => void;
  onUpdateEngineVersion?: (bot: BotDomain) => void;
}
export function BotManagementMenu(props: BotManagementMenuProps) {
  const {
    bot,
    collaborationMode,
    lockedByOther,
    onAction,
    onManagePublication,
    onChangeSpace,
    onAuthorize,
    canGeneralConfig,
    generalConfigDisabledReason,
    onGeneralConfig,
    engineVersionUpdateAvailability,
    onOpenEngineInfo,
    onUpdateEngineVersion,
  } = props;
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const open = props.open ?? uncontrolledOpen;
  const setOpen = props.onOpenChange ?? setUncontrolledOpen;
  const [confirmAction, setConfirmAction] = useState<BotCardManagementAction>();
  if (bot.lifecycle === 'deploying') return null;
  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`管理 ${bot.name}`}
            leftIcon={<MoreHorizontal className="size-4" />}
          />
        </PopoverTrigger>
        <PopoverContent align="end" className="w-52 space-y-1 p-2">
          {/* 配置类：Bot 的配置与形态变更操作（验收修正：配置类同组置顶） */}
          <MenuSection>
            {onGeneralConfig ? (
              <Button
                variant="ghost"
                size="sm"
                className="w-full justify-start"
                leftIcon={<ShieldCheck className="size-4" />}
                disabled={!canGeneralConfig}
                onClick={() => {
                  setOpen(false);
                  onGeneralConfig(bot);
                }}
              >
                {canGeneralConfig ? (
                  '通用配置'
                ) : (
                  <ActionHelp description={generalConfigDisabledReason ?? '当前不可用'}>通用配置</ActionHelp>
                )}
              </Button>
            ) : null}
            <Button
              variant="ghost"
              size="sm"
              className="w-full justify-start"
              leftIcon={<MapPin className="size-4" />}
              disabled={lockedByOther}
              onClick={() => {
                setOpen(false);
                onChangeSpace?.(bot);
              }}
            >
              变更归属空间
            </Button>
            {bot.serviceMode === 'non-service' && bot.deployment === 'cloud' && bot.canUpgradeToService ? (
              <Button
                variant="ghost"
                size="sm"
                className="w-full justify-start"
                leftIcon={actionIcon.upgrade}
                disabled={lockedByOther}
                onClick={() => {
                  setOpen(false);
                  setConfirmAction('upgrade');
                }}
              >
                {actionLabel.upgrade}
              </Button>
            ) : null}
          </MenuSection>
          {/* 日常操作（协作与查看） */}
          <MenuSection divider>
            {collaborationMode && onAuthorize ? (
              <Button
                variant="ghost"
                size="sm"
                className="w-full justify-start"
                leftIcon={<Users className="size-4" />}
                onClick={() => {
                  setOpen(false);
                  onAuthorize(bot);
                }}
              >
                {collaborationMode === 'authorize' ? '授权' : '申请操作权限'}
              </Button>
            ) : null}
            {bot.deployment === 'local' && bot.actions.includes('open_folder') ? (
              <Button
                variant="ghost"
                size="sm"
                className="w-full justify-start"
                onClick={() => {
                  setOpen(false);
                  void onAction('open_folder', bot);
                }}
              >
                打开本地目录
              </Button>
            ) : null}
          </MenuSection>
          <BotEngineVersionMenuItems
            bot={bot}
            availability={engineVersionUpdateAvailability}
            onOpenInfo={
              onOpenEngineInfo
                ? (selected) => {
                    setOpen(false);
                    onOpenEngineInfo(selected);
                  }
                : undefined
            }
            onUpdate={
              onUpdateEngineVersion
                ? (selected) => {
                    setOpen(false);
                    onUpdateEngineVersion(selected);
                  }
                : undefined
            }
          />
          {/* 发布与运行态操作：按相同的权限和实例规则集中处理。 */}
          <BotManagementRuntimeItems
            bot={bot}
            lockedByOther={lockedByOther}
            onClose={() => setOpen(false)}
            onConfirm={setConfirmAction}
            onManagePublication={onManagePublication}
          />
        </PopoverContent>
      </Popover>
      <BotManagementConfirmDialog
        bot={bot}
        action={confirmAction}
        onAction={onAction}
        onCancel={() => setConfirmAction(undefined)}
      />
    </>
  );
}
