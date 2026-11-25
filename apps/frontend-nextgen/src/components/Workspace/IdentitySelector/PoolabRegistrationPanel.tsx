import { getCapabilities } from '@/capabilities';
import { Button, Input, notifyError, notifySuccess, Segmented, Skeleton } from '@/components/ui';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useBotRegistrationToken } from '@/pages/Workspace/hooks/useBotRegistrationToken';
import { getBotRegistrationNameError } from '@/services/workspace/botRegistrationCommand';
import {
  buildPoolabInstallCommand,
  buildPoolabOneClickCommand,
  buildPoolabRegisterCommand,
  buildPoolabStartCommand,
  buildPoolabStatusCommand,
} from '@/services/workspace/poolabRegistrationCommand';
import { ExternalLink, RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';

const POOLAB_PROVIDER_SLUG = 'poolab';
type PoolabAccessMode = 'quick' | 'manual';

/** 与 QwenWorkRegistrationPanel 中的 Command 块一致：标题 + 等宽代码 + 复制按钮。 */
function Command({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-muted/40">
      <div className="flex items-center justify-between gap-2 border-b border-divider px-3 py-2">
        <span className="text-xs font-medium">{label}</span>
        <Button
          size="sm"
          variant="secondary"
          disabled={!value}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(value);
              notifySuccess(`${label}已复制`);
            } catch {
              notifyError('复制失败，请检查浏览器剪贴板权限');
            }
          }}
        >
          复制{label}
        </Button>
      </div>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all p-3 text-xs leading-5">{value}</pre>
    </div>
  );
}

export function PoolabRegistrationPanel({ open }: { open: boolean }) {
  const installation = getCapabilities().getPoolabBridgeInstallation().value;
  const environment = getCapabilities().getRuntimeEnvironment().value;
  const { identity } = useHumanIdentity();
  const isSupportedEnv = environment === 'PRE' || environment === 'PROD';
  const {
    token,
    expiresAt,
    error: tokenError,
    isLoading,
    retry: retryToken,
  } = useBotRegistrationToken(open && isSupportedEnv, POOLAB_PROVIDER_SLUG);
  const [editedBotName, setEditedBotName] = useState<string | null>(null);
  const [mode, setMode] = useState<PoolabAccessMode>('quick');
  useEffect(() => {
    if (!open) setMode('quick');
  }, [open]);

  // Open Core 默认 null → 父级 Segmented 不会渲染 Poolab Tab；兜底防御直挂场景。
  if (!installation) return null;

  const displayName = identity?.displayName?.trim();
  // displayName 最多填充到 64 长度的边界（'的Poolab' 占 7 字符）
  const fallbackBotName = displayName ? `${displayName.slice(0, 57)}的Poolab` : '我的Poolab';
  const botName = editedBotName ?? fallbackBotName;
  const botNameError = getBotRegistrationNameError(botName);

  const installCommand = buildPoolabInstallCommand();
  const startCommand = buildPoolabStartCommand();
  const statusCommand = buildPoolabStatusCommand();
  const oneClickCommand = token
    ? buildPoolabOneClickCommand({
        token,
        botName,
        environment,
        scriptUrl: installation.oneClickScriptUrl,
      })
    : '';
  const registerCommand = token
    ? buildPoolabRegisterCommand({ token, botName, environment })
    : '';

  return (
    <div className="space-y-4 text-xs leading-5">
      {!isSupportedEnv ? (
        <div role="alert" className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-muted-foreground">
          当前部署环境不支持 Poolab 自助接入（仅预发与生产可用）。请切换到预发或生产环境后重试。
        </div>
      ) : (
        <>
          <p>
            Poolab 通过 bcs-bridge 接入协作网络。请先在 Poolab 终端安装 Bridge，然后选择「一键接入」或「手动接入」完成注册与启动。
          </p>
          <section className="space-y-2" aria-label="安装 Bridge">
            <h3 className="font-medium">1. 安装 bcs-bridge</h3>
            <p>在 Poolab 终端执行以下命令安装 Bridge（需 acli 已加入 PATH）。</p>
            <Command label="安装命令" value={installCommand} />
          </section>

          <section className="space-y-2" aria-label="接入 Poolab Bot">
            <h3 className="font-medium">2. 接入 Poolab Bot</h3>
            <label className="block">
              Bot 名称
              <Input value={botName} maxLength={64} onChange={(event) => setEditedBotName(event.target.value)} />
            </label>
            {botNameError ? <p role="alert">{botNameError}</p> : null}
            {tokenError ? (
              <div className="space-y-2">
                <p role="alert">{tokenError}</p>
                <Button size="sm" onClick={retryToken}>
                  <RefreshCw aria-hidden className="size-3.5" />
                  重新获取接入 Token
                </Button>
              </div>
            ) : isLoading ? (
              <div className="space-y-2 rounded-lg border border-border bg-muted/40 p-4" role="status">
                <Skeleton.Line className="w-1/3" />
                <Skeleton.Block className="h-20 w-full" />
              </div>
            ) : !botNameError && token ? (
              <div className="space-y-3">
                <Segmented<PoolabAccessMode>
                  aria-label="Poolab 接入方式"
                  value={mode}
                  onChange={setMode}
                  options={[
                    { value: 'quick', label: '一键接入（推荐）' },
                    { value: 'manual', label: '手动接入' },
                  ]}
                />
                {mode === 'quick' ? (
                  <div className="space-y-2">
                    <p>在 Poolab 终端粘贴以下命令，一次完成 安装 → 注册 → 后台启动 → 状态检查。</p>
                    <Command label="一键接入命令" value={oneClickCommand} />
                  </div>
                ) : (
                  <div className="space-y-3">
                    <Command label="注册命令" value={registerCommand} />
                    <Command label="启动命令" value={startCommand} />
                    <Command label="状态命令" value={statusCommand} />
                  </div>
                )}
                {expiresAt ? (
                  <p className="text-muted-foreground">
                    Token 有效期至：{new Date(expiresAt).toLocaleString('zh-CN')}
                  </p>
                ) : null}
              </div>
            ) : null}
          </section>

          <footer className="border-t border-divider pt-3">
            <a
              href={installation.guideUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-primary underline"
            >
              Poolab 接入 BCN 指南
              <ExternalLink aria-hidden className="size-3" />
            </a>
          </footer>
        </>
      )}
    </div>
  );
}
