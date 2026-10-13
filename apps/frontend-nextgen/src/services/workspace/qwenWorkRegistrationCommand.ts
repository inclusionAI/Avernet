import { getCodexRegistrationInputError, quoteShellArg } from './botRegistrationCommand';
import type { BridgeRelease } from './bridgeReleaseService';

export const QWEN_CONFIG_ARGUMENT = '"$HOME/.bcn-bridge/qwenwork.toml"';
const BINARY = '"$HOME/.local/bin/bcs-bridge"';
export const QWEN_START_COMMAND = `${BINARY} --config ${QWEN_CONFIG_ARGUMENT} start --daemon\n${BINARY} status\ncurl --fail http://127.0.0.1:52491/health`;
export const QWEN_INIT_COMMAND = `if ${BINARY} init --help >/dev/null 2>&1; then\n  ${BINARY} --config ${QWEN_CONFIG_ARGUMENT} init --engine qwenwork\nelse\n  printf '%s\\n' '当前 Bridge 不支持自动初始化，请先升级到包含 init 的版本。' >&2\n  false\nfi`;

function downloadDirectory(release: BridgeRelease): string {
  return '"$HOME"/Downloads/' + quoteShellArg(`bcs-bridge-${release.version}`);
}

export function buildBridgeDownloadCommand(release: BridgeRelease): string {
  const directory = downloadDirectory(release);
  return `mkdir -p ${directory} &&\ncurl --fail --location ${quoteShellArg(
    release.installerUrl,
  )} -o ${directory}/bcs-bridge-installer.sh &&\ncurl --fail --location ${quoteShellArg(
    release.checksumUrl,
  )} -o ${directory}/sha256.sum`;
}

export function buildBridgeInstallCommand(release: BridgeRelease): string {
  const directory = downloadDirectory(release);
  return `(cd ${directory} && awk '$2=="bcs-bridge-installer.sh" {print}' sha256.sum | shasum -a 256 -c -) &&\nsh ${directory}/bcs-bridge-installer.sh &&\n${BINARY} --version`;
}

export function buildQwenWorkRegistrationCommand({
  token,
  botName,
  cwd,
  apiUrl,
  bcsEndpoint,
}: {
  token: string;
  botName: string;
  cwd: string;
  apiUrl: string;
  bcsEndpoint: string;
}): string {
  if (!token || getCodexRegistrationInputError(botName, cwd)) return '';
  let upstream: URL;
  try {
    const api = new URL(apiUrl);
    upstream = new URL('/ws/bot', bcsEndpoint);
    if (
      ![api.protocol, upstream.protocol].every((p) => p === 'http:' || p === 'https:') ||
      api.username ||
      api.password ||
      upstream.username ||
      upstream.password
    )
      return '';
  } catch {
    return '';
  }
  upstream.protocol = upstream.protocol === 'https:' ? 'wss:' : 'ws:';
  const directory = cwd.trim();
  const path = directory.startsWith('~/') ? '"$HOME"' + quoteShellArg(directory.slice(1)) : quoteShellArg(directory);
  const args = [
    'register',
    '--token',
    token,
    '--bot-name',
    botName.trim(),
    '--mode',
    'plugin',
    '--provider-auth',
    'static-bearer',
    '--engine',
    'qwenwork',
    '--api-url',
    apiUrl,
    '--upstream-url',
    upstream.href,
  ];
  return `${BINARY} --config ${QWEN_CONFIG_ARGUMENT} ${args
    .map(quoteShellArg)
    .join(' ')} --provider-bot-ref "qwenwork-$(openssl rand -hex 12)" --cwd ${path}`;
}
