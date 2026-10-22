import type { Context } from '@deepseek-ai/cordis';
import { randomUUID } from 'node:crypto';
import { credentialRef } from '@deepseek-ai/dsh-credentials';
import type { Config } from './config.js';
import { canonicalizeEndpoint, resolveEndpoint, type DnsResolver, type ResolvedEndpoint } from './endpoint.js';
import { BcnRegistrationClient, NodeHttpTransport, type HttpTransport } from './http-client.js';
import { asRecord, asNonEmptyString, parseBotSession, type BotSession } from './protocol.js';
import { registrationTokenScope } from './registration.js';

export interface ResolvedBotSession {
  session: BotSession;
  endpoint: ResolvedEndpoint;
}

export async function loadOrRegisterBotSession(
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

  const client = new BcnRegistrationClient(
    endpoint,
    options.transport ?? new NodeHttpTransport(),
    config.connectionTimeoutMs,
  );
  const scope = registrationTokenScope(onboardingToken.value);
  let providerBotRef: string | undefined;
  if (scope) {
    // Persist before HTTP: a lost response or failed Session write must not create another Bot.
    const pendingRef = credentialRef(`${config.botSessionRef}_REGISTRATION`);
    const pending = await ctx.credentials.resolve(pendingRef);
    if (pending) {
      let record: Record<string, unknown> | undefined;
      try { record = asRecord(JSON.parse(pending.value)); } catch { /* Report without credential contents. */ }
      providerBotRef = asNonEmptyString(record?.providerBotRef);
      if (!providerBotRef || record?.endpoint !== endpoint.baseUrl.toString()
        || record.providerId !== scope.providerId || record.owner !== scope.owner
        || record.botName !== config.botName) {
        throw new Error('Pending BCN Provider registration differs from this endpoint, owner, Provider or Bot name; use a separate DSH profile');
      }
    } else {
      providerBotRef = `dsh-${randomUUID()}`;
      await ctx.credentials.set(pendingRef, JSON.stringify({
        ...scope, endpoint: endpoint.baseUrl.toString(), botName: config.botName, providerBotRef,
      }));
    }
  }
  const registration = await client.register(onboardingToken.value, config.botName,
    scope && providerBotRef ? { scope, providerBotRef } : undefined);
  const session: BotSession = {
    version: 1,
    endpoint: endpoint.baseUrl.toString(),
    botUuid: registration.bot_uuid,
    botToken: registration.bot_token,
    botName: registration.bot_name,
    ...(registration.registration ? { registration: registration.registration } : {}),
  };

  // /register already creates the Bot, sets its name, and establishes ownership.
  // Persist before connecting so restarts reuse this identity without registering again.
  await ctx.credentials.set(botSessionRef, JSON.stringify(session));
  return { session, endpoint };
}

export async function persistBotSession(ctx: Context, config: Config, session: BotSession): Promise<void> {
  await ctx.credentials.set(credentialRef(config.botSessionRef), JSON.stringify(session));
}
