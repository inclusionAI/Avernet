import type { Context } from '@deepseek-ai/cordis';
import { credentialRef } from '@deepseek-ai/dsh-credentials';
import type { Config } from './config.js';
import { canonicalizeEndpoint, resolveEndpoint, type DnsResolver, type ResolvedEndpoint } from './endpoint.js';
import { BcnOnboardingClient, NodeHttpTransport, type HttpTransport } from './http-client.js';
import { parseBotSession, type BotSession } from './protocol.js';

export interface ResolvedBotSession {
  session: BotSession;
  endpoint: ResolvedEndpoint;
}

export async function loadOrOnboardBotSession(
  ctx: Context,
  config: Config,
  options: { resolver?: DnsResolver; transport?: HttpTransport } = {},
): Promise<ResolvedBotSession> {
  const botSessionRef = credentialRef(config.botSessionRef);
  const stored = await ctx.credentials.resolve(botSessionRef);
  if (stored) {
    const session = parseBotSession(stored.value);
    const endpoint = await resolveEndpoint(session.endpoint, options.resolver);
    if (config.endpoint && canonicalizeEndpoint(config.endpoint) !== endpoint.baseUrl.toString()) {
      throw new Error(
        'Configured BCN endpoint differs from the endpoint bound into BCN_BOT_SESSION; clear or rotate the Bot Session before changing endpoints',
      );
    }
    const client = new BcnOnboardingClient(
      endpoint,
      options.transport ?? new NodeHttpTransport(),
      config.connectionTimeoutMs,
    );
    await client.onboard(session, config);
    return { session, endpoint };
  }

  if (!config.endpoint || !config.botName) {
    throw new Error('First-time BCN onboarding requires endpoint and botName configuration');
  }
  const endpoint = await resolveEndpoint(config.endpoint, options.resolver);
  const onboardingToken = await ctx.credentials.resolve(credentialRef(config.onboardingTokenRef));
  if (!onboardingToken) {
    throw new Error(`BCN onboarding token credential ${config.onboardingTokenRef} is not configured`);
  }

  const client = new BcnOnboardingClient(
    endpoint,
    options.transport ?? new NodeHttpTransport(),
    config.connectionTimeoutMs,
  );
  const registration = await client.register(onboardingToken.value, config.botName);
  const session: BotSession = {
    version: 1,
    endpoint: endpoint.baseUrl.toString(),
    botUuid: registration.bot_uuid,
    botToken: registration.bot_token,
    botName: registration.bot_name,
  };

  // Persist immediately after exchange: a later descriptor failure is
  // recoverable on restart and never requires logging or copying the Bot token.
  await ctx.credentials.set(botSessionRef, JSON.stringify(session));
  await client.onboard(session, config);
  return { session, endpoint };
}

export async function persistBotSession(ctx: Context, config: Config, session: BotSession): Promise<void> {
  await ctx.credentials.set(credentialRef(config.botSessionRef), JSON.stringify(session));
}
