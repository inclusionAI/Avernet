// health.claude / providers.* / models.list handlers.
//
// These expose capability and discovery endpoints. Each handler writes its
// own response frame.

import { probeClaudeCli } from '../../claude-cli-bridge.js';
import type { ConnectionContext } from '../connection-context.js';
import type { GatewayRequestFrame } from '../../types.js';

// Lazy-load SDK bridge so missing SDK doesn't crash the gateway.
type SdkBridge = typeof import('../../claude-sdk-bridge.js');
let _sdkBridge: SdkBridge | null = null;
async function getSdkBridge(): Promise<SdkBridge | null> {
  if (_sdkBridge !== null) return _sdkBridge;
  try {
    _sdkBridge = await import('../../claude-sdk-bridge.js');
    return _sdkBridge;
  } catch {
    return null;
  }
}

export type MetaHandlerDeps = {
  useSdkBridge: boolean;
  providers: unknown[];
  models: unknown[];
};

export type MetaHandler = (
  ctx: ConnectionContext,
  frame: GatewayRequestFrame,
  deps: MetaHandlerDeps,
) => Promise<void>;

export const handleHealthClaude: MetaHandler = async (ctx, frame, deps) => {
  let health;
  if (deps.useSdkBridge) {
    const sdk = await getSdkBridge();
    if (sdk) {
      health = await sdk.probeClaudeSdk();
    } else {
      health = { ok: false, cliExists: false, supportsStreamJson: false, message: 'Claude Agent SDK not installed. Run: npm install @anthropic-ai/claude-agent-sdk' };
    }
  } else {
    health = await probeClaudeCli();
  }
  const payload = { ...health, bridge: deps.useSdkBridge ? 'sdk' : 'cli' };
  ctx.response(frame.id, health.ok, payload, health.ok ? undefined : { message: health.message, code: 'CLAUDE_UNAVAILABLE' });
};

export const handleProvidersAvailable: MetaHandler = async (ctx, frame, deps) => {
  ctx.response(frame.id, true, { providers: deps.providers });
};

export const handleProvidersList: MetaHandler = async (ctx, frame, deps) => {
  ctx.response(frame.id, true, { providers: deps.providers });
};

export const handleModelsList: MetaHandler = async (ctx, frame, deps) => {
  ctx.response(frame.id, true, { models: deps.models });
};

export const META_METHODS: Record<string, MetaHandler> = {
  'health.claude': handleHealthClaude,
  'providers.available': handleProvidersAvailable,
  'providers.list': handleProvidersList,
  'models.list': handleModelsList,
};
