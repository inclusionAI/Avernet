import { getBotRegistrationNameError, quoteShellArg } from './botRegistrationCommand';

export const DSH_VERSION = '0.2.1-alpha.2';
export const DSH_PLUGIN_PACKAGE = '@avernet-plugin/deepseek-harness-channel-bcn@latest';
export const DSH_INSTALL_COMMAND = `npm install --global @deepseek-ai/dsh@${DSH_VERSION}`;

export interface DeepSeekHarnessRegistrationInput {
  token: string;
  bcsEndpoint: string;
  botName: string;
  profile: string;
}

export function getDeepSeekHarnessInputError({
  botName,
  profile,
}: Pick<DeepSeekHarnessRegistrationInput, 'botName' | 'profile'>): string | null {
  const nameError = getBotRegistrationNameError(botName);
  if (nameError) return nameError;
  if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(profile.trim()))
    return 'Profile 需为 1–64 位小写字母、数字或连字符，并以字母或数字开头。';
  return null;
}

export function buildDeepSeekHarnessRegistrationCommand(input: DeepSeekHarnessRegistrationInput): string {
  if (!input.token.trim() || getDeepSeekHarnessInputError(input)) return '';
  try {
    const endpoint = new URL(input.bcsEndpoint);
    if (!['http:', 'https:'].includes(endpoint.protocol) || endpoint.username || endpoint.password) return '';
  } catch {
    return '';
  }
  const profile = quoteShellArg(input.profile.trim());
  // Pass the onboarding token only to the final DSH process, after installation and configuration succeed.
  return (
    `dsh plugin --profile ${profile} add -- ${DSH_PLUGIN_PACKAGE} &&\n` +
    `dsh plugin --profile ${profile} exec dsh-bcn-configure -- ` +
    `--profile ${profile} --endpoint ${quoteShellArg(input.bcsEndpoint.trim())} ` +
    `--bot-name ${quoteShellArg(input.botName.trim())} &&\n` +
    `BCN_ONBOARDING_TOKEN=${quoteShellArg(input.token.trim())} dsh --profile ${profile}`
  );
}

export function buildDeepSeekHarnessStartCommand(profile: string): string {
  return /^[a-z0-9][a-z0-9-]{0,63}$/.test(profile.trim()) ? `dsh --profile ${quoteShellArg(profile.trim())}` : '';
}
