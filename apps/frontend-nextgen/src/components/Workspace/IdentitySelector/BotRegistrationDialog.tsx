import { getCapabilities } from '@/capabilities';
import {
  Button,
  Empty,
  Modal,
  ModalContent,
  ModalDescription,
  ModalHeader,
  ModalTitle,
  notifyError,
  notifySuccess,
  Segmented,
  Skeleton,
} from '@/components/ui';
import { useBotRegistrationToken } from '@/pages/Workspace/hooks/useBotRegistrationToken';
import { Check, Copy, MessageSquare, RefreshCw, Terminal } from 'lucide-react';
import { Suspense, useEffect, useMemo, useRef, useState } from 'react';

import { PoolabRegistrationPanel } from './PoolabRegistrationPanel';
import { QwenWorkRegistrationPanel } from './QwenWorkRegistrationPanel';

// engine 增加 'poolab' 由 getPoolabBridgeInstallation() 门控：Open Core 默认 null → 不渲染该 Tab；
// internal overlay 注入脚本/指南 URL 后启用。
type RegistrationEngine = string;
type AccessMode = 'self' | 'auto';

interface BotRegistrationDialogProps {
  open: boolean;
  onClose: () => void;
}

const SELF_SERVICE_COMMAND_TEMPLATE = String.raw`bash -c "$(curl -fsSL https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/crates/plugins/openclaw-channel-bcn/install-instructions/install.sh || echo "exit 1")" install.sh --token {token} --bcs-endpoint {bcsEndpoint}`;
const BOT_AUTO_COMMAND_TEMPLATE = String.raw`Follow the instructions in https://raw.githubusercontent.com/inclusionAI/Avernet/refs/heads/dev/src/bcs/crates/plugins/openclaw-channel-bcn/install-instructions/install.md to join BCN (Bot Coordination Network), your TOKEN is {token} and BCS_ENDPOINT is {bcsEndpoint}.`;

function formatExpiresAt(expiresAt: number | null): string | null {
  if (expiresAt === null) return null;
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }).format(expiresAt);
}

export function BotRegistrationDialog({ open, onClose }: BotRegistrationDialogProps) {
  const panelCapability = getCapabilities().getAdditionalBotRegistrationPanels?.();
  const additionalPanels = panelCapability?.status === 'available' ? panelCapability.value : [];
  const poolabEnabled = getCapabilities().getPoolabBridgeInstallation().value !== null;
  const [engine, setEngine] = useState<RegistrationEngine>('openclaw');
  const AdditionalPanel = additionalPanels.find((panel) => panel.id === engine)?.Panel;
  const [mode, setMode] = useState<AccessMode>('self');
  const [copied, setCopied] = useState(false);
  const copiedTimeoutRef = useRef<number | null>(null);
  const { token, expiresAt, isLoading, error, bcsEndpoint, retry } = useBotRegistrationToken(
    open && engine === 'openclaw',
  );

  useEffect(
    () => () => {
      if (copiedTimeoutRef.current !== null) window.clearTimeout(copiedTimeoutRef.current);
    },
    [],
  );

  const command = useMemo(() => {
    if (!token) return '';
    const template = mode === 'self' ? SELF_SERVICE_COMMAND_TEMPLATE : BOT_AUTO_COMMAND_TEMPLATE;
    return template.replace('{token}', token).replace('{bcsEndpoint}', bcsEndpoint ?? '');
  }, [mode, token, bcsEndpoint]);

  const copyCommand = async () => {
    if (!command) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error('clipboard-unavailable');
      await navigator.clipboard.writeText(command);
      setCopied(true);
      notifySuccess(mode === 'self' ? '自助接入命令已复制' : 'Bot 接入命令已复制');
      if (copiedTimeoutRef.current !== null) window.clearTimeout(copiedTimeoutRef.current);
      copiedTimeoutRef.current = window.setTimeout(() => setCopied(false), 2000);
    } catch {
      notifyError('复制失败，请检查浏览器剪贴板权限');
    }
  };

  return (
    <Modal open={open} onOpenChange={(next) => !next && onClose()}>
      <ModalContent size="lg" className="max-h-[85vh] min-w-0 overflow-y-auto p-5">
        <ModalHeader>
          <ModalTitle className="text-lg">接入外部 Bot</ModalTitle>
          <ModalDescription className="leading-5">选择 Bot 类型，按对应指南完成接入。</ModalDescription>
        </ModalHeader>

        <Segmented<RegistrationEngine>
          aria-label="Bot 类型"
          value={engine}
          onChange={setEngine}
          className="w-full"
          options={[
            { value: 'openclaw', label: 'OpenClaw' },
            { value: 'qwenwork', label: '千问办公' },
            ...additionalPanels.map(({ id, label }) => ({ value: id, label })),
            ...(poolabEnabled ? [{ value: 'poolab' as const, label: 'Poolab' }] : []),
          ]}
        />
        {AdditionalPanel ? (
          <Suspense fallback={<p role="status">正在加载接入指南…</p>}>
            <AdditionalPanel open={open} />
          </Suspense>
        ) : engine === 'qwenwork' ? (
          <QwenWorkRegistrationPanel open={open} />
        ) : engine === 'poolab' ? (
          <PoolabRegistrationPanel open={open} />
        ) : (
          <>
            <section className="space-y-2" aria-label="OpenClaw 接入方式">
              <p className="text-xs font-medium text-muted-foreground">如何接入 OpenClaw？</p>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {([
                  { value: 'self', label: '在终端执行', description: '复制安装命令，在 Bot 所在设备执行', icon: Terminal },
                  { value: 'auto', label: '让 Bot 执行', description: '复制接入指引，发送给 OpenClaw Bot', icon: MessageSquare },
                ] as const).map((option) => (
                  <Button
                    key={option.value}
                    variant="outline"
                    aria-pressed={mode === option.value}
                    className={`h-auto min-w-0 justify-start gap-3 whitespace-normal p-3 text-left ${mode === option.value ? 'border-primary bg-primary/5' : ''}`}
                    onClick={() => {
                      setMode(option.value);
                      setCopied(false);
                    }}
                  >
                    <option.icon aria-hidden className="size-4 shrink-0 text-primary" />
                    <span className="min-w-0 space-y-1">
                      <span className="block text-sm">{option.label}</span>
                      <span className="block text-xs font-normal text-muted-foreground">{option.description}</span>
                    </span>
                    {mode === option.value && <Check aria-hidden className="ml-auto size-4 shrink-0 text-primary" />}
                  </Button>
                ))}
              </div>
            </section>

            {isLoading ? (
              <div className="space-y-3 rounded-lg border border-border bg-muted/40 p-4">
                <Skeleton.Line className="w-1/3" />
                <Skeleton.Block className="h-24 w-full" />
              </div>
            ) : error ? (
              <Empty
                compact
                className="rounded-lg border border-border bg-muted/40"
                title="接入 Token 获取失败"
                description={error}
                action={
                  <Button variant="secondary" size="sm" onClick={retry}>
                    <RefreshCw aria-hidden className="size-3.5" />
                    重试
                  </Button>
                }
              />
            ) : (
              <div className="min-w-0 rounded-lg border border-border bg-muted/40">
                <div className="flex items-center justify-between gap-3 border-b border-border px-3 py-2">
                  <div className="min-w-0">
                    <p className="text-xs font-medium text-foreground">
                      {mode === 'self' ? '终端安装命令' : '发送给 Bot 的接入指引'}
                    </p>
                    <p className="mt-0.5 truncate text-xs text-muted-foreground">
                      {mode === 'self' ? '在 Bot 运行环境中执行以下命令。' : '将以下指引发送给目标 Bot 执行。'}
                    </p>
                  </div>
                  <Button variant="secondary" size="sm" className="shrink-0 gap-1.5" onClick={copyCommand}>
                    {copied ? <Check aria-hidden className="size-3.5" /> : <Copy aria-hidden className="size-3.5" />}
                    {copied ? '已复制' : mode === 'self' ? '复制命令' : '复制指引'}
                  </Button>
                </div>
                <pre className="min-w-0 whitespace-pre-wrap break-all px-3 py-3 font-mono text-xs leading-5 text-foreground">
                  {command}
                </pre>
                <p className="border-t border-border px-3 py-2 text-xs text-muted-foreground">
                  Token 有效期至：{formatExpiresAt(expiresAt)}。
                </p>
              </div>
            )}
          </>
        )}
      </ModalContent>
    </Modal>
  );
}
