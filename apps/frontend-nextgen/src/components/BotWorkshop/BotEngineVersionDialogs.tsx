import { Button } from '@/components/ui/Button';
import { Modal, ModalContent, ModalDescription, ModalFooter, ModalHeader, ModalTitle } from '@/components/ui/Modal';
import {
  currentEngineVersionLabel,
  engineVersionNoUpdateMessage,
  engineVersionUiState,
  targetEngineVersionLabel,
} from '@/domain/botEngineVersion';
import type { BotEngineVersionState } from '@/hooks/useBotEngineVersions';
import { AlertTriangle, ArrowRight, Bot as BotIcon } from 'lucide-react';

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
  onConfirmUpdate: (onRestartSubmitted: () => void) => Promise<boolean>;
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
  if (uiState === 'updatable') return '检测到可更新版本。更新会重启 Bot 并加载最新引擎。';
  if (uiState === 'no_update') return engineVersionNoUpdateMessage(state.observation);
  if (state.observation.detection.status === 'read_failed') return '暂时无法读取当前版本，请稍后重试。';
  if (state.observation.release_target.status === 'unavailable') return '目标引擎版本尚未配置。';
  return '当前版本信息不足，暂时无法判断是否可更新。';
}

/**
 * 更新确认弹窗主体（dmore image 19 还原）：Bot 名 chip + 版本跃迁条 + 更新内容 + 中断提醒。
 * release notes 无真实数据源，更新内容如实描述更新动作，不虚构变更明细。
 */
function BotEngineVersionUpdateBody({
  botName,
  currentVersion,
  targetVersion,
}: {
  botName: string;
  currentVersion: string;
  targetVersion: string;
}) {
  return (
    <section className="flex flex-col gap-4" aria-label="引擎版本更新详情">
      <span className="inline-flex w-fit items-center gap-1.5 rounded-full bg-muted px-2.5 py-1 text-xs font-medium text-foreground">
        <BotIcon aria-hidden className="size-3.5 text-muted-foreground" />
        {botName}
      </span>

      <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 rounded-md border border-input bg-muted/40 px-4 py-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-xs text-muted-foreground">当前版本</span>
          <span className="text-sm font-semibold tabular-nums break-all">{currentVersion}</span>
        </div>
        <ArrowRight aria-hidden className="size-4 text-muted-foreground" />
        <div className="flex flex-col gap-0.5">
          <span className="text-xs text-muted-foreground">最新版本</span>
          <span className="text-sm font-semibold tabular-nums break-all text-primary">{targetVersion}</span>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <p className="text-sm font-medium">更新内容</p>
        <ul className="flex flex-col gap-1.5">
          <li className="flex items-start gap-2 text-sm text-muted-foreground">
            <span aria-hidden className="mt-[7px] size-1 shrink-0 rounded-full bg-primary/60" />
            重启 Bot 并加载最新引擎，无需重新部署。
          </li>
          <li className="flex items-start gap-2 text-sm text-muted-foreground">
            <span aria-hidden className="mt-[7px] size-1 shrink-0 rounded-full bg-primary/60" />
            更新完成后自动校验运行状态，确认版本生效。
          </li>
        </ul>
      </div>

      <p className="flex items-start gap-2 rounded-md border border-warning/30 bg-warning/10 px-3 py-2.5 text-sm text-foreground">
        <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0 text-warning" />
        重启将短暂中断 Bot 服务，请确认当前任务可中断。
      </p>
    </section>
  );
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
              {updateDisabledReason || '当前账号没有 Bot 重启权限，无法执行版本更新。'}
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

      <Modal open={updateOpen} onOpenChange={(open) => !open && onCloseUpdate()}>
        <ModalContent>
          <ModalHeader>
            <ModalTitle className="text-base">更新引擎版本</ModalTitle>
            <ModalDescription>确认目标版本后重启更新，不改变 Bot 配置的发布版本。</ModalDescription>
          </ModalHeader>

          {observation ? (
            <BotEngineVersionUpdateBody
              botName={botName}
              currentVersion={currentVersion ?? '未提供'}
              targetVersion={targetVersion ?? '未配置'}
            />
          ) : (
            <p className="text-sm text-muted-foreground">{statusDescription(state)}</p>
          )}

          {state.kind === 'ready' && state.updateError ? (
            <p className="text-sm text-destructive">{state.updateError}</p>
          ) : null}

          {observation && !canRestart ? (
            <p className="text-sm text-muted-foreground">
              {updateDisabledReason || '当前账号没有 Bot 重启权限，无法执行版本更新。'}
            </p>
          ) : null}

          <ModalFooter>
            <Button type="button" variant="secondary" onClick={onCloseUpdate}>
              取消
            </Button>
            <Button
              type="button"
              loading={state.kind === 'updating' && !state.error}
              disabled={!canRestart || !updateAvailable}
              onClick={() => {
                void onConfirmUpdate(onCloseUpdate);
              }}
            >
              重启并更新
            </Button>
          </ModalFooter>
        </ModalContent>
      </Modal>
    </>
  );
}
