import { getCapabilities } from '@/capabilities';
import { Button, Input, notifyError, notifySuccess } from '@/components/ui';
import { useHumanIdentity } from '@/hooks/useHumanIdentity';
import { useBotRegistrationToken } from '@/pages/Workspace/hooks/useBotRegistrationToken';
import { getBotRegistrationNameError } from '@/services/workspace/botRegistrationCommand';
import { resolveBcsEndpoint } from '@/services/workspace/botRegistrationService';
import { BRIDGE_RELEASES_URL, VERIFIED_BRIDGE_BETA } from '@/services/workspace/bridgeReleaseService';
import {
  buildBridgeDownloadCommand,
  buildBridgeInstallCommand,
  buildQwenWorkOnboardingCommand,
} from '@/services/workspace/qwenWorkRegistrationCommand';
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

export function QwenWorkRegistrationPanel({ open }: { open: boolean }) {
  const endpoint = resolveBcsEndpoint();
  const installation = getCapabilities().getQwenWorkBridgeInstallation().value;
  const environment = getCapabilities().getRuntimeEnvironment().value;
  const profile = installation?.profiles
    ? environment === 'LOCAL'
      ? 'local'
      : environment === 'PROD'
      ? 'prod'
      : 'pre'
    : undefined;
  const capability = getCapabilities().getQwenWorkBotRegistrationConfig(endpoint ?? '');
  const {
    token,
    expiresAt,
    error: tokenError,
    isLoading,
    retry: retryToken,
  } = useBotRegistrationToken(open && capability.status === 'available', capability.value.providerSlug);
  const { identity } = useHumanIdentity();
  const [editedBotName, setBotName] = useState<string | null>(null);
  const displayName = installation?.personalizeBotName ? identity?.displayName.trim() : undefined;
  const botName = editedBotName ?? (displayName ? `${displayName.slice(0, 59)}的千问办公` : '我的千问办公');
  const command =
    token && endpoint && capability.status === 'available'
      ? installation?.buildOnboardingCommand
        ? installation.buildOnboardingCommand({ token, botName, apiUrl: capability.value.apiUrl, bcsEndpoint: endpoint, environment, profile })
        : buildQwenWorkOnboardingCommand({
          token,
          botName,
          apiUrl: capability.value.apiUrl,
          bcsEndpoint: endpoint,
          binary: installation?.binary,
          installCommand: installation?.installCommand,
          prepareCommand: installation?.prepareCommand,
          profile,
        })
      : '';
  return (
    <div className="space-y-4 text-xs leading-5">
      <p>
        千问办公通过 bcs-bridge 接入协作网络。填写 Bot 信息后，在千问办公所在设备的终端执行接入命令，完成注册与启动。
      </p>
      <p>支持 macOS 13+（Apple Silicon / Intel）。先安装并打开千问办公；Bridge 运行期间请保持桌面端在线。</p>
      {installation ? (
        <p>接入命令会复用已有 Bridge；未安装时自动安装，请确保 acli 已加入 PATH。</p>
      ) : (
        <section className="space-y-2" aria-label="安装 Bridge">
          <h3 className="font-medium">1. 安装 Bridge</h3>
          <p>
            推荐版本：{VERIFIED_BRIDGE_BETA.version}（Beta）。先下载安装脚本和校验文件，再执行安装脚本；无需安装
            Rust。
          </p>
          <div className="flex flex-wrap gap-3">
            <a
              href={VERIFIED_BRIDGE_BETA.installerUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary underline"
            >
              下载安装脚本
            </a>
            <a
              href={VERIFIED_BRIDGE_BETA.checksumUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="underline"
            >
              下载校验文件
            </a>
            <a href={BRIDGE_RELEASES_URL} target="_blank" rel="noopener noreferrer" className="underline">
              查看其他发布版本
            </a>
          </div>
          <p>
            在千问办公所在的 Mac 终端中依次执行以下命令。脚本会选择 Apple Silicon 或 Intel 制品，安装到 ~/.local/bin。
          </p>
          <Command label="下载命令" value={buildBridgeDownloadCommand(VERIFIED_BRIDGE_BETA)} />
          <Command label="校验与安装命令" value={buildBridgeInstallCommand(VERIFIED_BRIDGE_BETA)} />
        </section>
      )}
      <section className="space-y-2" aria-label="接入千问 Bot">
        <h3 className="font-medium">{installation ? '1. 接入千问 Bot' : '2. 接入千问 Bot'}</h3>
        <label className="block">
          Bot 名称
          <Input value={botName} maxLength={64} onChange={(e) => setBotName(e.target.value)} />
        </label>
        <p>
          {profile
            ? `配置由 Bridge 保存在 profile ${profile} 中。重复执行注册命令会复用该环境已有的 Bot；若凭据所属 Provider 或 BCS 地址不一致，会拒绝覆盖。`
            : '配置保存在 ~/.bcn-bridge/qwenwork.toml。已有千问 Bot 请复用现有配置，不要重复注册。'}
          实际模型和执行目录由千问办公控制，当前使用千问办公自动创建的会话工作区。
        </p>
        {capability.status !== 'available' ? (
          <p role="alert">当前部署未开放千问接入。</p>
        ) : tokenError ? (
          <>
            <p role="alert">{tokenError}</p>
            <Button size="sm" onClick={retryToken}>
              重新获取接入 Token
            </Button>
          </>
        ) : isLoading ? (
          <p role="status">正在获取接入 Token…</p>
        ) : command ? (
          <Command label="接入命令" value={command} />
        ) : (
          <p>{getBotRegistrationNameError(botName) ?? '缺少有效的 BCS 地址或接入 Token。'}</p>
        )}
        {expiresAt ? (
          <p className="text-muted-foreground">Token 有效期至：{new Date(expiresAt).toLocaleString('zh-CN')}</p>
        ) : null}
        {!installation?.guideUrl && (
          <>
            <p>
              复制接入命令，在终端执行一次即可完成注册、初始化和启动。任一步失败都会停止，请根据终端提示处理后重试。
            </p>
            {!installation && (
              <p className="text-muted-foreground">
                推荐的 0.1.4-beta.2 已包含 init 命令。若本机仍是旧版本，请先执行上面的安装命令。
              </p>
            )}
            <p>
              在千问办公的企业微信 Bot 渠道中填写命令输出的 WebSocket URL、Bot ID 和
              Secret，并启用渠道。密钥只在本机显示，不要发送给其他人。
            </p>
            <p>
              按千问版本要求重新加载 Hooks。无需手工编辑 TOML 或复制 Hooks 模板；重复初始化会保留原密钥及其他 Hooks。
            </p>
            <details>
              <summary className="cursor-pointer font-medium">数据库无法自动识别时</summary>
              <p>
                在接入命令的 <code>init --engine qwenwork</code> 后加{' '}
                <code>--option database_path=/实际路径/agents.db</code>。可用{' '}
                <code>--option settings_path=/实际路径/settings.json</code> 指定设置文件。若已有其他 Bridge Hooks
                占用同一地址，复用原 Bridge 配置或选择不同的本机端口。
              </p>
            </details>
            <a
              href="https://github.com/inclusionAI/Avernet/blob/dev/apps/bcs/crates/tools/bcs-bridge/qwenwork/README.md"
              target="_blank"
              rel="noopener noreferrer"
              className="underline"
            >
              完整接入指南与版本兼容说明
            </a>

            <p>
              {profile
                ? `接入命令在注册和初始化成功后，会先停止当前 Bridge（正在执行的任务会中止），再启动 profile ${profile}；其他环境的配置和状态保留。`
                : '同一用户只能运行一个 Bridge；已有实例需先停止，再执行接入命令。'}
              停止命令：<code>{installation ? `${installation.binary} stop` : '~/.local/bin/bcs-bridge stop'}</code>。
            </p>
            <p>
              执行初始化输出的 Check: 命令检查本机 health（地址随监听端口变化），应返回
              qwen_connected=true、faulted=false。它只检查千问连接；BCN 在线状态请关闭接入指南，列表会自动刷新。
            </p>
          </>
        )}
      </section>
      {installation?.guideUrl && (
        <section className="space-y-2" aria-label="配置千问办公渠道">
          <h3 className="font-medium">2. 配置千问办公渠道</h3>
          <p className="text-muted-foreground">
            请选择千问办公的「企业微信」渠道。Bridge 复用该渠道的 Bot 通信协议，通过本机 WebSocket
            连接千问办公并转接 BCN 消息，无需准备企业微信账号或创建企业微信机器人。
          </p>
          <p>
            执行接入命令后，请按照
            <a href={installation.guideUrl} target="_blank" rel="noopener noreferrer" className="text-primary underline">
              千问办公渠道配置指南
            </a>
            在千问办公中配置并启用渠道，完成本步骤后才能连接 Bot。
          </p>
        </section>
      )}
    </div>
  );
}
