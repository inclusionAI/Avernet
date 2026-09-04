import {
  Badge,
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
import { Check, Copy, RefreshCw } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';

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
  const [mode, setMode] = useState<AccessMode>('self');
  const [copied, setCopied] = useState(false);
  const copiedTimeoutRef = useRef<number | null>(null);
  const { token, expiresAt, isLoading, error, bcsEndpoint, retry } = useBotRegistrationToken(open);

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
      <ModalContent size="lg" className="min-w-0 p-5">
        <ModalHeader>
          <Badge tone="primary" className="w-fit">
            Bot 接入
          </Badge>
          <ModalTitle className="text-lg">接入新 Bot</ModalTitle>
          <ModalDescription className="leading-5">
            选择接入方式，复制命令后在对应环境中执行，即可完成新 Bot 的初始化接入。
          </ModalDescription>
        </ModalHeader>

        <Segmented
          value={mode}
          onChange={(next) => {
            setMode(next);
            setCopied(false);
          }}
          className="w-full"
          options={[
            { value: 'self', label: '用户自助接入' },
            { value: 'auto', label: 'Bot 自动接入' },
          ]}
        />

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
                  {mode === 'self' ? '方式一：用户自助接入' : '方式二：Bot 自动接入'}
                </p>
                <p className="mt-0.5 truncate text-xs text-muted-foreground">
                  {mode === 'self' ? '在 Bot 运行环境中执行以下命令。' : '将以下指引发送给目标 Bot 执行。'}
                </p>
              </div>
              <Button variant="secondary" size="sm" className="shrink-0 gap-1.5" onClick={copyCommand}>
                {copied ? <Check aria-hidden className="size-3.5" /> : <Copy aria-hidden className="size-3.5" />}
                {copied ? '已复制' : '复制'}
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
      </ModalContent>
    </Modal>
  );
}
