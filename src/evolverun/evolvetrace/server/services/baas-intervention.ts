/**
 * Stub BaaS intervention service for Evolvetrace open-source version.
 * The original clawweb implementation sends messages via BaaS OpenAPI (Ant internal).
 * In the open-source version, intervention is not available unless the user
 * configures an external message transport.
 */

export type InterventionParams = {
  botId: string;
  sessionKey: string;
  sessionId: string | null;
  message: string;
  transportConfig?: { apiKey: string; iamtoken: string; baseUrl: string };
};

export type InterventionResult = {
  ok: boolean;
  messageId?: string;
  sessionId?: string;
  error?: string;
  tokenExpired?: boolean;
};

export async function sendIntervention(_params: InterventionParams): Promise<InterventionResult> {
  return {
    ok: false,
    error: "BaaS intervention is not available in the open-source version. Configure an external transport to enable interventions.",
  };
}
