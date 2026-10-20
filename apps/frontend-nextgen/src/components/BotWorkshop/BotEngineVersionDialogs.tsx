import { Button } from '@/components/ui/Button';
import { ConfirmDialog } from '@/components/ui/ConfirmDialog';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import {
  currentEngineVersionLabel,
  engineVersionNoUpdateMessage,
  engineVersionUiState,
  targetEngineVersionLabel,
} from '@/domain/botEngineVersion';
import type { BotEngineVersionState } from '@/hooks/useBotEngineVersions';

export interface BotEngineVersionDialogsProps {
  botName: string;
  state: BotEngineVersionState;
  infoOpen: boolean;
  updateOpen: boolean;
  canRestart: boolean;
  updateDisabledReason?: string;
  onCloseInfo: () => void;
  onCloseUpdate: () => void;
  onSelectInstance: (deviceUuid: string) => void;
  onRequestUpdate: () => void;
  onConfirmUpdate: () => Promise<boolean>;
}

function statusDescription(state: BotEngineVersionState): string {
  if (state.kind === 'loading') return '正在读取当前引擎版本…';
  if (state.kind === 'error') return state.message;
  if (state.kind === 'not_applicable') return '该引擎不支持版本检查。';
  if (state.kind === 'select_instance') return '此 Bot 有多个运行实例，请选择要查看的实例。';
  if (state.kind === 'idle') return '当前 Bot 暂不可读取引擎版本。';
  if (state.kind === 'updating') return state.error || '正在更新引擎并确认运行状态…';
  if (state.kind === 'ready' && state.updateError) return state.updateError;

  const uiState = engineVersionUiState(state.observation);
  if (uiState === 'updatable') return '检测到可更新版本。更新会重启草稿引擎进程。';
  if (uiState === 'no_update') return engineVersionNoUpdateMessage(state.observation);
  if (state.observation.detection.status === 'read_failed') return '暂时无法读取当前版本，请稍后重试。';
  if (state.observation.release_target.status === 'unavailable') return '目标引擎版本尚未配置。';
  return '当前版本信息不足，暂时无法判断是否可更新。';
}

export default function BotEngineVersionDialogs({
  botName,
  state,
  infoOpen,
  updateOpen,
  canRestart,
  updateDisabledReason,
  onCloseInfo,
  onCloseUpdate,
  onSelectInstance,
  onRequestUpdate,
  onConfirmUpdate,
}: BotEngineVersionDialogsProps) {
  const observation = state.kind === 'ready' || state.kind === 'updating' ? state.observation : undefined;
  const updateAvailable = observation ? engineVersionUiState(observation) === 'updatable' : false;
  const currentVersion = observation ? currentEngineVersionLabel(observation) : undefined;
  const targetVersion = observation ? targetEngineVersionLabel(observation) : undefined;

  return (
    <>
      <Modal open={infoOpen} onOpenChange={(open) => !open && onCloseInfo()}>
        <ModalContent>
          <ModalHeader>
            <ModalTitle>引擎信息 · {botName}</ModalTitle>
            <ModalDescription>{statusDescription(state)}</ModalDescription>
          </ModalHeader>
          {updateAvailable && !canRestart ? (
            <p className="mt-3 text-sm text-muted-foreground">
              {updateDisabledReason || '当前账号没有重启引擎权限，无法执行版本更新。'}
            </p>
          ) : null}

          {observation ? (
            <dl className="my-4 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              <dt className="text-muted-foreground">当前版本</dt>
              <dd className="break-all font-medium">{currentVersion}</dd>
              <dt className="text-muted-foreground">目标版本</dt>
              <dd className="break-all font-medium">{targetVersion}</dd>
            </dl>
          ) : null}

          {state.kind === 'select_instance' ? (
            <div className="my-4 space-y-2" aria-label="引擎实例">
              <p className="text-sm font-medium">请选择引擎实例</p>
              {state.instances.map((instance) => (
                <Button
                  key={instance.id}
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="w-full justify-start"
                  onClick={() => onSelectInstance(instance.id)}
                >
                  {instance.id} · {instance.engine} · {instance.status}
                </Button>
              ))}
            </div>
          ) : null}

          <ModalFooter>
            <Button type="button" variant="secondary" onClick={onCloseInfo}>
              关闭
            </Button>
            {updateAvailable ? (
              <Button
                type="button"
                disabled={!canRestart || state.kind === 'updating'}
                onClick={() => {
                  onCloseInfo();
                  onRequestUpdate();
                }}
              >
                更新引擎版本
              </Button>
            ) : null}
          </ModalFooter>
        </ModalContent>
      </Modal>

      <ConfirmDialog
        open={updateOpen}
        loading={state.kind === 'updating' && !state.error}
        title={`更新「${botName}」的引擎版本`}
        description={
          state.kind === 'ready' && state.updateError
            ? state.updateError
            : `将草稿引擎从 ${currentVersion ?? '当前版本'} 更新至 ${targetVersion ?? '目标版本'}，并重启该引擎进程。`
        }
        confirmText="更新并重启引擎"
        onCancel={onCloseUpdate}
        onConfirm={async () => {
          if (await onConfirmUpdate()) onCloseUpdate();
        }}
      />
    </>
  );
}
