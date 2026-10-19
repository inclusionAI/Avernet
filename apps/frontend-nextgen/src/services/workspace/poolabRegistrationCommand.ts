import { getBotRegistrationNameError, quoteShellArg } from './botRegistrationCommand';

/**
 * Poolab 接入命令装配。规则与 specs/2026-10-15-poolab-registration-panel/spec.md 对齐：
 * - 预发（PRE）需要 `SERVER_ENV=pre ` 前缀；生产（PROD）省略；
 * - LOCAL/DEV 由调用方在面板处禁用，不进入本模块的命令构建；
 * - token、botName 必须经 quoteShellArg 转义；
 * - botName 不合法（getBotRegistrationNameError 非 null）时返回空串，由 UI 显示校验错误。
 */

const POOLAB_INSTALL_COMMAND = 'acli install bcs-bridge';
const POOLAB_START_COMMAND = 'bcs-bridge start --daemon';
const POOLAB_STATUS_COMMAND = 'bcs-bridge status';

export interface PoolabEnvironmentPrefix {
  /** 'PRE' / 'PROD' / 其他。仅 'PRE' 需要前缀。 */
  environment: string;
}

export function buildPoolabEnvironmentPrefix({ environment }: PoolabEnvironmentPrefix): string {
  return environment === 'PRE' ? 'SERVER_ENV=pre ' : '';
}

export function buildPoolabInstallCommand(): string {
  return POOLAB_INSTALL_COMMAND;
}

export function buildPoolabStartCommand(): string {
  return POOLAB_START_COMMAND;
}

export function buildPoolabStatusCommand(): string {
  return POOLAB_STATUS_COMMAND;
}

export interface PoolabRegistrationCommandInput {
  token: string;
  botName: string;
  environment: string;
}

/** 手动模式的 register 子命令（含可选环境前缀）。 */
export function buildPoolabRegisterCommand(input: PoolabRegistrationCommandInput): string {
  if (!input.token.trim()) return '';
  if (getBotRegistrationNameError(input.botName)) return '';
  const prefix = buildPoolabEnvironmentPrefix({ environment: input.environment });
  return `${prefix}bcs-bridge register --token ${quoteShellArg(input.token.trim())} --bot-name ${quoteShellArg(
    input.botName.trim(),
  )}`;
}

export interface PoolabOneClickCommandInput extends PoolabRegistrationCommandInput {
  /** 一键接入脚本 OSS 地址；来自 getPoolabBridgeInstallation()。 */
  scriptUrl: string;
}

/**
 * 一键接入命令：拉脚本 → poolab-onboard.sh --token … --name …。
 * 失败时 `curl || echo exit 1` 让 bash 退出而不静默继续，与 legacy open-claw 实现保持一致。
 */
export function buildPoolabOneClickCommand(input: PoolabOneClickCommandInput): string {
  if (!input.scriptUrl.trim()) return '';
  if (!input.token.trim()) return '';
  if (getBotRegistrationNameError(input.botName)) return '';
  const prefix = buildPoolabEnvironmentPrefix({ environment: input.environment });
  const quotedToken = quoteShellArg(input.token.trim());
  const quotedName = quoteShellArg(input.botName.trim());
  return (
    `${prefix}bash -c "$(curl -fsSL ${quoteShellArg(input.scriptUrl.trim())} || echo 'exit 1')" ` +
    `poolab-onboard.sh --token ${quotedToken} --name ${quotedName}`
  );
}
