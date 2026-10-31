import { getCapabilities } from '@/capabilities';
import { Button, Input, notifyError, notifySuccess } from '@/components/ui';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useBotRegistrationToken } from '@/pages/Workspace/hooks/useBotRegistrationToken';
import {
  buildDeepSeekHarnessRegistrationCommand,
  buildDeepSeekHarnessStartCommand,
  DSH_INSTALL_COMMAND,
  DSH_VERSION,
  getDeepSeekHarnessInputError,
} from '@/services/workspace/deepSeekHarnessRegistrationCommand';
import { useState } from 'react';

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

export function DeepSeekHarnessRegistrationPanel({ open }: { open: boolean }) {
  const guide = getCapabilities().getDeepSeekHarnessRegistrationGuideUrl();
  const guideUrl = guide.status === 'available' ? guide.value : null;
  const { token, expiresAt, isLoading, error, bcsEndpoint, retry } = useBotRegistrationToken(open);
  const { identity } = useHumanIdentity();
  const [editedBotName, setEditedBotName] = useState<string | null>(null);
  const nameSuffix = '的Deepseek harness';
  const displayName = identity?.displayName?.trim();
  const botName =
    editedBotName ??
    (displayName
      ? `${Array.from(displayName)
          .slice(0, 64 - Array.from(nameSuffix).length)
          .join('')}${nameSuffix}`
      : '我的Deepseek harness');
  const [profile, setProfile] = useState('web');
  const inputError = getDeepSeekHarnessInputError({ botName, profile });
  const scriptBuilder = getCapabilities().getDeepSeekHarnessOnboardingCommand?.().value;
  const command = token && bcsEndpoint && !inputError
    ? scriptBuilder
      ? scriptBuilder({ token, bcsEndpoint, botName, profile, environment: getCapabilities().getRuntimeEnvironment().value })
      : buildDeepSeekHarnessRegistrationCommand({ token, bcsEndpoint, botName, profile })
    : '';

  return (
    <div className="space-y-4 text-xs leading-5">
      <p>将本机 DeepSeek Harness 接入协作网络。需要 Node.js 22.19+，支持 DSH 0.2.0-rc.2 至 {DSH_VERSION}。</p>
      <details className="space-y-2">
        <summary className="cursor-pointer font-medium">尚未安装 DSH 或需要升级？</summary>
        <p>已安装兼容版本可跳过。需要安装或升级时，先停止 DSH 进程，再在 Bot 所在设备的终端执行。</p>
        <Command label="DSH 安装命令" value={DSH_INSTALL_COMMAND} />
      </details>
      <section className="space-y-2" aria-label="接入 DeepSeek Harness Bot">
        <h3 className="font-medium">1. 安装插件并接入</h3>
        <label className="block">
          Bot 名称
          <Input value={botName} onChange={(event) => setEditedBotName(event.target.value)} />
        </label>
        <label className="block">
          DSH Profile
          <Input value={profile} maxLength={64} onChange={(event) => setProfile(event.target.value)} />
        </label>
        <p>使用已有 Profile 可保留模型配置；使用新 Profile 时，请先在 DSH 中配置模型。避免同一 Bot 重复运行。</p>
        <p>默认安装最新版 BCN 插件（latest）。</p>
        {isLoading ? (
          <p role="status">正在获取接入 Token…</p>
        ) : error ? (
          <div className="space-y-2">
            <p role="alert">{error}</p>
            <Button size="sm" variant="secondary" onClick={retry}>
              重新获取接入 Token
            </Button>
          </div>
        ) : inputError ? (
          <p role="alert">{inputError}</p>
        ) : command ? (
          <Command label="接入命令" value={command} />
        ) : (
          <p role="alert">缺少有效的 BCS 地址或接入 Token，请检查部署配置。</p>
        )}
        {expiresAt ? (
          <p className="text-muted-foreground">Token 有效期至：{new Date(expiresAt).toLocaleString('zh-CN')}</p>
        ) : null}
        {token && !isLoading && !error ? (
          <Button size="sm" variant="secondary" onClick={retry}>
            刷新接入 Token
          </Button>
        ) : null}
        <p>接入命令会安装插件、配置 Profile 并启动 DSH；请保持进程运行，随后回到外部 Bot 列表刷新确认在线状态。</p>
      </section>
      <section className="space-y-2" aria-label="重新启动 DeepSeek Harness">
        <h3 className="font-medium">2. 后续启动</h3>
        <p>首次注册成功后，同一 Profile 再次启动无需接入 Token。</p>
        <Command label="启动命令" value={buildDeepSeekHarnessStartCommand(profile)} />
      </section>
      {guideUrl ? (
        <a href={guideUrl} target="_blank" rel="noopener noreferrer" className="text-primary underline">
          完整接入指南与兼容说明
        </a>
      ) : null}
    </div>
  );
}
