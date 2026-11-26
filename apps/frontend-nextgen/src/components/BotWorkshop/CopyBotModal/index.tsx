import { Button } from '@/components/ui/Button';
import {
  Modal,
  ModalContent,
  ModalDescription,
  ModalHeader,
  ModalTitle,
} from '@/components/ui/Modal';
import type {
  BotCopyInput,
  BotCreateAuthorization,
  BotCreateSpace,
  BotDomain,
} from '@/services/botWorkshop';
import { useExcludeFileTree } from '@/hooks/useExcludeFileTree';
import { Spin } from '@/components/ui/Spin';
import { cn } from '@/utils/cn';
import { Check, Copy as CopyIcon } from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { AuthorizationPanel } from '../CreateBotModal/AuthorizationPanel';
import { CopyBotFormFields } from './CopyBotFormFields';
import { ExcludeFileTree } from './ExcludeFileTree';

interface CopyBotModalProps {
  sourceBot?: BotDomain;
  /** 复制时归属空间列表(用于第 2 步只读展示源 Bot 空间名)。 */
  spaces: BotCreateSpace[];
  copying: boolean;
  /** detail-wait 在跑(pendingIssuedBotId 已设)。区别于 copying(copyBot API in-flight 也真):
   * 「正在等待新 Bot 创建」罩只在 awaitingDetail 时弹,提交后/auth 出来前那段不弹。 */
  awaitingDetail: boolean;
  authorization?: BotCreateAuthorization & { message?: string; error?: string };
  onClose: () => void;
  onSubmit: (input: BotCopyInput) => Promise<void>;
}

type Step = 'exclude' | 'confirm';

const STEP_LABELS: Array<{ key: Step; label: string }> = [
  { key: 'exclude', label: '排除文件' },
  { key: 'confirm', label: '重命名与确认' },
];

const initialValues = (bot: BotDomain): BotCopyInput => ({
  sourceBotId: bot.id,
  sourceOwnerId: bot.ownerId,
  name: `${bot.name} 副本`.slice(0, 40),
  description: bot.description ?? '',
  engine: bot.runtime?.engine,
  spaceId: bot.spaceId,
  ownership: bot.ownership,
  serviceMode: bot.serviceMode,
  avatarUrl: bot.avatarUrl,
  excludes: [],
});

const CopyBotModal: React.FC<CopyBotModalProps> = ({
  sourceBot,
  spaces,
  copying,
  awaitingDetail,
  authorization,
  onClose,
  onSubmit,
}) => {
  const open = Boolean(sourceBot);
  const [step, setStep] = useState<Step>('exclude');
  const [values, setValues] = useState<BotCopyInput>(() =>
    sourceBot ? initialValues(sourceBot) : initialValues({} as BotDomain),
  );
  const [error, setError] = useState<string>();
  const excludeTree = useExcludeFileTree(sourceBot?.id, sourceBot?.ownerId);

  useEffect(() => {
    if (!sourceBot) {
      setStep('exclude');
      setError(undefined);
      return;
    }
    setValues(initialValues(sourceBot));
    setStep('exclude');
    setError(undefined);
  }, [sourceBot]);

  // 排除集合随勾选变化时同步进 input.excludes,后端按此跳过子树。
  useEffect(() => {
    setValues((current) => ({ ...current, excludes: [...excludeTree.excluded] }));
  }, [excludeTree.excluded]);

  // auth_required:AuthorizationPanel(iframe)全屏承载授权页。授权页 auth 完成后 copy 不再
  // 轮询 auth-status(dst 无该端点),detail-wait 由 useBotWorkshopCopyFlow 的 pendingIssuedBotId
  // effect 承担,detail 可查才 setCopying(false)+onClose()+toast+load。
  if (authorization) return <AuthorizationPanel authorization={authorization} />;
  // 授权浮窗(iframe)被清掉后(AgentPass 倒计时关 / auth 完成等),只要 awaitingDetail
  // (detail-wait 在跑、dst bot 还没查到),弹一个新浮窗(半透明模糊背景 + 卡片 + loading
  // 按钮)接管持住 —— 不让浮窗真关掉,用户也关不掉,直到 detail-wait 可查才
  // setCopying(false)+onClose()+toast+load。issued 路径(无 authorization)同样落到这里。
  // 用 awaitingDetail 而非 copying:copyBot API in-flight 时 copying 也真但 pending 没设,
  // 那段(提交后/auth 出来前)不弹罩,避免"正在等待新 Bot 创建"出现两次。
  if (awaitingDetail) {
    if (typeof document === 'undefined') return null;
    return createPortal(
      <div className="fixed inset-0 z-[200] flex h-dvh w-screen items-center justify-center bg-black/20 p-4 backdrop-blur-[1px]">
        <div
          role="status"
          aria-label="正在等待新 Bot 创建"
          aria-live="polite"
          className="w-full max-w-sm rounded-lg border border-border bg-background px-6 py-5 text-center text-foreground shadow-lg"
        >
          <Spin tip="正在等待新 Bot 创建…" className="py-1" />
          <p className="mt-2 text-xs text-muted-foreground">复制已提交,完成后将自动刷新列表,请勿关闭。</p>
        </div>
      </div>,
      document.body,
    );
  }

  const stepIndex = STEP_LABELS.findIndex((item) => item.key === step);
  const busy = copying || excludeTree.loading;

  const goConfirm = () => {
    setError(undefined);
    setStep('confirm');
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (busy) return;
    const name = values.name.trim();
    if (!name) {
      setError('请输入 Bot 名称');
      return;
    }
    if (name.includes('@')) {
      setError('Bot 名称不能包含 @');
      return;
    }
    if (name.length > 40) {
      setError('Bot 名称不能超过 40 个字符');
      return;
    }
    setError(undefined);
    try {
      await onSubmit({ ...values, name, description: values.description.trim() });
    } catch (submitError) {
      setError(submitError instanceof Error ? submitError.message : '复制失败,请重试');
    }
  };

  return (
    <Modal open={open} onOpenChange={(nextOpen) => !nextOpen && !busy && onClose()}>
      <ModalContent
        size="lg"
        showClose={!busy}
        aria-describedby="copy-bot-description"
        className="overlay-scrollbar max-h-[calc(100vh-3rem)] max-w-[710px] overscroll-y-none p-4"
      >
        <ModalHeader className="flex-row items-center gap-2.5 space-y-0">
          <div className="flex size-10 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <CopyIcon aria-hidden className="size-5" />
          </div>
          <div className="min-w-0 space-y-1">
            <ModalTitle className="text-base leading-6">
              复制 Bot{sourceBot?.name ? ` — ${sourceBot.name}` : ''}
            </ModalTitle>
            <ModalDescription id="copy-bot-description" className="text-xs leading-5">
              复制源 Bot 的基础配置、引擎、资源文件、副屏与能力集;不继承协作者、外部渠道，不修改源 Bot。
            </ModalDescription>
          </div>
        </ModalHeader>

        <ol className="flex items-center gap-2 text-sm text-muted-foreground">
          {STEP_LABELS.map((item, index) => {
            const completed = index < stepIndex;
            const current = index === stepIndex;
            return (
              <li
                key={item.key}
                aria-current={current ? 'step' : undefined}
                className={cn(
                  'flex items-center gap-2',
                  current && 'text-foreground',
                )}
              >
                <span
                  className={cn(
                    'grid size-4 shrink-0 place-items-center rounded-full text-[10px] font-medium',
                    current
                      ? 'bg-primary text-primary-foreground'
                      : completed
                        ? 'bg-primary/10 text-primary'
                        : 'border border-input bg-background text-muted-foreground',
                  )}
                >
                  {completed ? <Check aria-hidden className="size-3" /> : index + 1}
                </span>
                <span
                  className={cn(
                    'shrink-0 font-medium',
                    current ? 'text-primary' : completed ? 'text-foreground' : 'text-muted-foreground',
                  )}
                >
                  {item.label}
                </span>
                {index < STEP_LABELS.length - 1 ? (
                  <span
                    aria-hidden
                    className={cn(
                      'h-0.5 min-w-6 flex-1 rounded-full',
                      completed ? 'bg-primary' : 'bg-border',
                    )}
                  />
                ) : null}
              </li>
            );
          })}
        </ol>

        {step === 'exclude' ? (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              默认复制全部资源文件,请勾选不需要复制的文件或文件夹。
            </p>
            <ExcludeFileTree
              visibleTree={excludeTree.visibleTree}
              expanded={excludeTree.expanded}
              pendingDirs={excludeTree.pendingDirs}
              loading={excludeTree.loading}
              error={excludeTree.error}
              checkState={excludeTree.checkState}
              onExpand={(path) => void excludeTree.expand(path)}
              onCollapse={excludeTree.collapse}
              onToggle={excludeTree.toggle}
            />
            <div className="flex items-center justify-end gap-2">
              <Button type="button" variant="secondary" disabled={busy} onClick={onClose}>
                取消
              </Button>
              <Button type="button" disabled={busy} onClick={goConfirm}>
                下一步
              </Button>
            </div>
          </div>
        ) : (
          <fieldset disabled={busy} className="min-w-0 space-y-4">
            <CopyBotFormFields
              values={values}
              setValues={setValues}
              sourceSpaces={spaces}
              copying={copying}
              error={error}
              onBack={() => setStep('exclude')}
              onCancel={onClose}
              onSubmit={submit}
            />
          </fieldset>
        )}
      </ModalContent>
    </Modal>
  );
};

export default CopyBotModal;
