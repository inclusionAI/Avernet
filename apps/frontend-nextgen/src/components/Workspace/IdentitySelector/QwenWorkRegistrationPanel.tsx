import { getCapabilities } from '@/capabilities';
import { Button, Input, notifyError, notifySuccess } from '@/components/ui';
import { useBotRegistrationToken } from '@/pages/Workspace/hooks/useBotRegistrationToken';
import { getCodexRegistrationInputError } from '@/services/workspace/botRegistrationCommand';
import { resolveBcsEndpoint } from '@/services/workspace/botRegistrationService';
import { BRIDGE_RELEASES_URL, VERIFIED_BRIDGE_BETA } from '@/services/workspace/bridgeReleaseService';
import {
  buildBridgeDownloadCommand,
  buildBridgeInstallCommand,
  buildQwenWorkRegistrationCommand,
  QWEN_INIT_COMMAND,
  QWEN_START_COMMAND,
} from '@/services/workspace/qwenWorkRegistrationCommand';
import { useState } from 'react';

function Command({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-muted/40">
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
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
  const capability = getCapabilities().getQwenWorkBotRegistrationConfig(endpoint ?? '');
  const {
    token,
    expiresAt,
    error: tokenError,
    isLoading,
    retry: retryToken,
  } = useBotRegistrationToken(open && capability.status === 'available', capability.value.providerSlug);
  const [botName, setBotName] = useState('我的千问办公');
  const [cwd, setCwd] = useState('');
  const command =
    token && endpoint && capability.status === 'available'
      ? buildQwenWorkRegistrationCommand({
          token,
          botName,
          cwd,
          apiUrl: capability.value.apiUrl,
          bcsEndpoint: endpoint,
        })
      : '';
  return (
    <div className="space-y-4 text-xs leading-5">
      <p>支持 macOS 13+（Apple Silicon / Intel）。先安装并打开千问办公；Bridge 运行期间请保持桌面端在线。</p>
      <section className="space-y-2" aria-label="下载 Bridge">
        <h3 className="font-medium">1. 下载并安装 Bridge</h3>
        <p>
          推荐版本：{VERIFIED_BRIDGE_BETA.version}（Beta）。先下载安装脚本和校验文件，再执行安装脚本；无需安装 Rust。
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
          <a href={VERIFIED_BRIDGE_BETA.checksumUrl} target="_blank" rel="noopener noreferrer" className="underline">
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
      <section className="space-y-2" aria-label="注册千问 Bot">
        <h3 className="font-medium">2. 注册千问 Bot</h3>
        <label className="block">
          Bot 名称
          <Input value={botName} maxLength={64} onChange={(e) => setBotName(e.target.value)} />
        </label>
        <label className="block">
          项目目录（绝对路径或 ~/ 开头）
          <Input value={cwd} placeholder="~/projects/my-project" onChange={(e) => setCwd(e.target.value)} />
        </label>
        <p>
          配置保存在 ~/.bcn-bridge/qwenwork.toml。已有千问 Bot
          请复用现有配置，不要重复注册。实际模型和执行目录由千问办公控制。
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
          <Command label="注册命令" value={command} />
        ) : (
          <p>{getCodexRegistrationInputError(botName, cwd) ?? '缺少有效的 BCS 地址或接入 Token。'}</p>
        )}
        {expiresAt ? (
          <p className="text-muted-foreground">Token 有效期至：{new Date(expiresAt).toLocaleString('zh-CN')}</p>
        ) : null}
      </section>
      <section className="space-y-2" aria-label="配置千问办公">
        <h3 className="font-medium">3. 初始化 Bridge，填写千问渠道信息</h3>
        <p>注册后运行一次初始化命令。Bridge 会识别千问数据库、生成并保存本机密钥，备份并合并工具与生命周期 Hooks。</p>
        <p className="text-muted-foreground">
          推荐的 0.1.4-beta.2 已包含 init 命令。若本机仍是旧版本，请先执行上面的安装命令。
        </p>
        <Command label="初始化命令" value={QWEN_INIT_COMMAND} />
        <p>
          在千问办公的企业微信 Bot 渠道中填写命令输出的 WebSocket URL、Bot ID 和
          Secret。密钥只在本机显示，不要发送给其他人。
        </p>
        <p>按千问版本要求重新加载 Hooks。无需手工编辑 TOML 或复制 Hooks 模板；重复初始化会保留原密钥及其他 Hooks。</p>
        <details>
          <summary className="cursor-pointer font-medium">数据库无法自动识别时</summary>
          <p>
            在初始化命令后加 <code>--option database_path=/实际路径/agents.db</code>。可用{' '}
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
      </section>
      <section className="space-y-2" aria-label="启动并检查 Bridge">
        <h3 className="font-medium">4. 启动并检查连接</h3>
        <p>
          完成配置后执行启动命令，再启用千问的企业微信 Bot 渠道。同一用户只能运行一个 Bridge；已有实例请先用{' '}
          <code>~/.local/bin/bcs-bridge stop</code> 停止。
        </p>
        <Command label="启动命令" value={QWEN_START_COMMAND} />
        <p>
          执行初始化输出的 Check: 命令检查本机 health（地址随监听端口变化），应返回
          qwen_connected=true、faulted=false。它只检查千问连接；BCN 在线状态请回到外部 Bot 列表刷新确认。
        </p>
      </section>
    </div>
  );
}
