import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Popover, PopoverTrigger } from '@/components/ui/Popover';
import type { EngineVersionUpdateAvailability } from '@/domain/botEngineVersion';
import { restartPublishStageLabel, restartPublishStageOf } from '@/domain/botWorkshop';
import type { BotActionAvailability, BotDomain } from '@/services/botWorkshop';
import { MoreVertical } from 'lucide-react';
import { useState } from 'react';
import { BotManagementMenuItems } from './BotManagementMenuItems';
import { actionLabel, type BotCardManagementAction } from './config';
interface BotManagementMenuProps {
  bot: BotDomain;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  collaborationMode?: 'authorize' | 'request';
  lockedByOther: boolean;
  onAction: (action: BotCardManagementAction, bot: BotDomain) => Promise<void>;
  onManagePublication?: (bot: BotDomain) => void;
  /** 以下两项与任务实例原为行内图标按钮，按 dmore 稿 087 收口进「三个点」气泡。 */
  onHealthCheck?: (bot: BotDomain) => void;
  healthCheckAvailability?: BotActionAvailability;
  onOpenTaskInstances?: (bot: BotDomain) => void;
  canOpenTaskInstances?: boolean;
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
    onHealthCheck,
    healthCheckAvailability,
    onOpenTaskInstances,
    canOpenTaskInstances,
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
  const [confirming, setConfirming] = useState(false);
  if (bot.lifecycle === 'deploying') return null;
  const restartPublishStage = confirmAction === 'restart_publish' ? restartPublishStageOf(bot.lifecycle) : undefined;
  return (
    <>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            aria-label={`管理 ${bot.name}`}
            className="h-7 w-7 text-primary hover:bg-primary/10 hover:text-primary"
            leftIcon={<MoreVertical className="size-4" />}
          />
        </PopoverTrigger>
        <BotManagementMenuItems
          bot={bot}
          collaborationMode={collaborationMode}
          lockedByOther={lockedByOther}
          onAction={onAction}
          onManagePublication={onManagePublication}
          onHealthCheck={onHealthCheck}
          healthCheckAvailability={healthCheckAvailability}
          onOpenTaskInstances={onOpenTaskInstances}
          canOpenTaskInstances={canOpenTaskInstances}
          onChangeSpace={onChangeSpace}
          onAuthorize={onAuthorize}
          canGeneralConfig={canGeneralConfig}
          generalConfigDisabledReason={generalConfigDisabledReason}
          onGeneralConfig={onGeneralConfig}
          engineVersionUpdateAvailability={engineVersionUpdateAvailability}
          onOpenEngineInfo={
            onOpenEngineInfo
              ? (selected) => {
                  setOpen(false);
                  onOpenEngineInfo(selected);
                }
              : undefined
          }
          onUpdateEngineVersion={
            onUpdateEngineVersion
              ? (selected) => {
                  setOpen(false);
                  onUpdateEngineVersion(selected);
                }
              : undefined
          }
          onMenuClose={() => setOpen(false)}
          onRequestConfirm={setConfirmAction}
        />
      </Popover>
      <ConfirmDialog
        open={Boolean(confirmAction)}
        loading={confirming}
        title={confirmAction === 'delete' ? '确认删除 Bot' : confirmAction ? actionLabel[confirmAction] : '确认'}
        description={
          confirmAction === 'delete'
            ? `删除「${bot.name}」后无法恢复。`
            : confirmAction === 'upgrade'
            ? '开启后不可逆，确认将此 Bot 转换为服务 Bot？'
            : confirmAction === 'engine_restart'
            ? '仅重启引擎进程，不重建容器。'
            : confirmAction === 'restart_publish'
            ? restartPublishStage
              ? `将重启该服务在${restartPublishStageLabel[restartPublishStage]}环境发布的运行时，进行中的服务会话可能短暂中断；草稿机器与草稿数据不受影响。`
              : '当前发布状态不支持重启发布，请刷新 Bot 列表后重试。'
            : '将重新拉起整个 Bot 容器，现有会话可能中断。'
        }
        confirmText={confirmAction === 'delete' ? '删除' : '确认'}
        confirmVariant={confirmAction === 'delete' ? 'destructive' : 'primary'}
        onCancel={() => setConfirmAction(undefined)}
        onConfirm={async () => {
          if (!confirmAction) return;
          setConfirming(true);
          try {
            await onAction(confirmAction, bot);
            setConfirmAction(undefined);
          } catch {
            // 失败提示已由 Hook toast 统一负责；保留弹窗供用户取消或重试，同时避免 rethrow 变成未处理拒绝。
          } finally {
            setConfirming(false);
          }
        }}
      />
    </>
  );
}