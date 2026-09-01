import type { Context } from '@deepseek-ai/cordis';
import { BcnBridge } from './bridge.js';
import { Config, normalizeConfig, type Config as PluginConfig } from './config.js';
import { loadOrOnboardBotSession, persistBotSession } from './credentials.js';
import { BcnWsClient } from './ws-client.js';

export const name = 'deepseek-harness-channel-bcn';
export const inject = ['agents', 'credentials', 'sessions', 'sessionPersistence', 'tools'];
export { Config };
export type { Config as DeepSeekHarnessBcnConfig } from './config.js';

export async function apply(ctx: Context, rawConfig: PluginConfig): Promise<() => Promise<void>> {
  const config = normalizeConfig(rawConfig);
  const log = ctx.logger(name);
  if (!config.enabled) {
    log.info('BCN channel is installed but disabled');
    return async () => {};
  }

  const { session, endpoint } = await loadOrOnboardBotSession(ctx, config);
  let bridge: BcnBridge | undefined;
  const client = new BcnWsClient({
    endpoint,
    session,
    config,
    status: () => bridge?.busy ? 'busy' : 'idle',
    onSessionChanged: rotated => persistBotSession(ctx, config, rotated),
    log,
  });
  bridge = new BcnBridge(ctx, client, config, log);
  bridge.start();
  client.start();

  return async () => {
    await bridge?.dispose();
    await client.stop();
  };
}
