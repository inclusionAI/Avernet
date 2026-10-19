import { getBotRegistrationNameError, quoteShellArg } from './botRegistrationCommand';
import type { BridgeRelease } from './bridgeReleaseService';

export const QWEN_CONFIG_ARGUMENT = '"$HOME/.bcn-bridge/qwenwork.toml"';
const BINARY = '"$HOME/.local/bin/bcs-bridge"';
function configSelector(profile?: string): string {
  if (profile !== undefined) {
    if (!/^[A-Za-z0-9_-]{1,64}$/.test(profile)) throw new Error('Invalid bridge profile');
    return `--profile ${quoteShellArg(profile)}`;
  }
  return `--config ${QWEN_CONFIG_ARGUMENT}`;
}

export function buildQwenWorkStartCommand(binary = BINARY, profile?: string): string {
  const stop = profile ? `${binary} stop &&\n` : '';
  return `${stop}${binary} ${configSelector(profile)} start --daemon &&\n${binary} status`;
}
export const QWEN_START_COMMAND = buildQwenWorkStartCommand();
export function buildQwenWorkInitCommand(binary = BINARY, profile?: string): string {
  return `if ${binary} init --help >/dev/null 2>&1; then\n  ${binary} ${configSelector(profile)} init --engine qwenwork\nelse\n  printf '%s\\n' '当前 Bridge 不支持自动初始化，请先升级到包含 init 的版本。' >&2\n  false\nfi`;
}
export const QWEN_INIT_COMMAND = buildQwenWorkInitCommand();

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
  apiUrl,
  bcsEndpoint,
  binary = BINARY,
  profile,
}: {
  token: string;
  botName: string;
  apiUrl: string;
  bcsEndpoint: string;
  binary?: string;
  profile?: string;
}): string {
  if ((profile !== undefined && !/^[A-Za-z0-9_-]{1,64}$/.test(profile)) || !token || getBotRegistrationNameError(botName)) return '';
  let upstream: URL;
  try {
    const api = new URL(apiUrl);
    upstream = new URL(`${bcsEndpoint.replace(/\/+$/, '')}/ws/bot`);
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
  return `${binary} ${configSelector(profile)} ${args
    .map(quoteShellArg)
    .join(' ')}`;
}

export function buildQwenWorkOnboardingCommand(
  input: Parameters<typeof buildQwenWorkRegistrationCommand>[0],
): string {
  const registration = buildQwenWorkRegistrationCommand(input);
  if (!registration) return '';
  return `${registration} &&\n(\n${buildQwenWorkInitCommand(input.binary, input.profile)}\n) &&\n${buildQwenWorkStartCommand(input.binary, input.profile)}`;
}
