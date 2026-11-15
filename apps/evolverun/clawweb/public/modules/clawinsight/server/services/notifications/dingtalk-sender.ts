export type DingTalkNotificationConfig = {
  appKey: string;
  appSecret: string;
  robotCode: string;
  apiBaseUrl: string;
  publicBaseUrl: string;
};

type DingTalkTokenResponse = {
  accessToken?: string;
  access_token?: string;
  expireIn?: number;
  expires_in?: number;
};

function describeNetworkError(error: unknown): string {
  if (!(error instanceof Error)) return String(error);
  const cause = error.cause;
  if (!cause || typeof cause !== "object") return error.message;
  const code = "code" in cause ? String(cause.code) : "";
  const message = "message" in cause ? String(cause.message) : "";
  const detail = [code, message].filter(Boolean).join(": ");
  return detail ? `${error.message} (${detail})` : error.message;
}

export class DingTalkCardNotDispatchedError extends Error {
  constructor(cause: unknown) {
    super(cause instanceof Error ? cause.message : "DingTalk card request could not be prepared", { cause });
    this.name = "DingTalkCardNotDispatchedError";
  }
}

export class DingTalkNotificationSender {
  private cachedToken: { token: string; expiresAt: number } | null = null;

  constructor(protected readonly config: DingTalkNotificationConfig) {}

  get enabled(): boolean {
    return Boolean(
      this.config.appKey
      && this.config.appSecret
      && this.config.robotCode
      && this.config.publicBaseUrl,
    );
  }

  private async getAccessToken(): Promise<string> {
    if (this.cachedToken && this.cachedToken.expiresAt > Date.now() + 60_000) {
      return this.cachedToken.token;
    }

    let response: Response;
    try {
      response = await fetch(`${this.config.apiBaseUrl}/v1.0/oauth2/accessToken`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          appKey: this.config.appKey,
          appSecret: this.config.appSecret,
        }),
      });
    } catch (error) {
      throw new Error(`DingTalk access token request failed: ${describeNetworkError(error)}`, { cause: error });
    }
    const body = await response.json() as DingTalkTokenResponse & Record<string, unknown>;
    if (!response.ok) {
      throw new Error(`DingTalk access token HTTP ${response.status}`);
    }
    const token: string | undefined = body.accessToken ?? body.access_token;
    if (!token) throw new Error("DingTalk access token missing");
    const expireIn = Number(body.expireIn ?? body.expires_in ?? 7200);
    this.cachedToken = {
      token,
      expiresAt: Date.now() + Math.max(60, expireIn) * 1000,
    };
    return token;
  }

  private normalizeUserId(userId: string): string {
    const normalized = userId.trim();
    return /^\d+$/.test(normalized) ? normalized.padStart(6, "0") : normalized;
  }

  /** Standard native card API; uses the same robot credentials and token cache. */
  async sendCard(input: { recipientUserId: string; outTrackId: string; templateId: string; callbackRouteKey?: string; cardParamMap: Record<string, string> }): Promise<void> {
    if (![input.recipientUserId, input.outTrackId, input.templateId].every(value => typeof value === "string" && value.trim())) throw new Error("DingTalk card configuration is incomplete");
    if (input.callbackRouteKey !== undefined && (typeof input.callbackRouteKey !== "string" || !input.callbackRouteKey.trim())) throw new Error("DingTalk callback route is empty");
    const body = await this.cardRequest("POST", "/v1.0/card/instances/createAndDeliver", {
      cardTemplateId: input.templateId, outTrackId: input.outTrackId,
      ...(input.callbackRouteKey ? { callbackType: "HTTP", callbackRouteKey: input.callbackRouteKey } : {}), userIdType: 1,
      openSpaceId: `dtv1.card//IM_ROBOT.${this.normalizeUserId(input.recipientUserId)}`,
      imRobotOpenDeliverModel: { robotCode: this.config.robotCode },
      imRobotOpenSpaceModel: { supportForward: false }, cardData: { cardParamMap: input.cardParamMap },
    }, input.cardParamMap);
    const deliveries = (body.result as { deliverResults?: Array<{ success?: boolean }> } | undefined)?.deliverResults;
    if (!deliveries?.length || deliveries.some(item => item.success !== true)) throw new Error("DingTalk card delivery is unconfirmed");
  }

  async updateCard(input: { outTrackId: string; cardParamMap: Record<string, string> }): Promise<void> {
    if (!input.outTrackId?.trim()) throw new Error("DingTalk card identity missing");
    await this.cardRequest("PUT", "/v1.0/card/instances", { outTrackId: input.outTrackId,
      cardData: { cardParamMap: input.cardParamMap }, cardUpdateOptions: { updateCardDataByKey: true }, userIdType: 1 }, input.cardParamMap);
  }

  private async cardRequest(method: string, path: string, input: Record<string, unknown>, params: Record<string, string>): Promise<Record<string, unknown>> {
    let token: string;
    let payload: string;
    try {
      if (!this.enabled) throw new Error("DingTalk notification is not configured");
      if (Object.entries(params).some(([key, value]) => Buffer.byteLength(key) > 100 || typeof value !== "string" || Buffer.byteLength(value) > 1024)) throw new Error("DingTalk card parameters exceed platform limits");
      payload = JSON.stringify(input);
      token = await this.getAccessToken();
    } catch (error) {
      throw new DingTalkCardNotDispatchedError(error);
    }
    const response = await fetch(`${this.config.apiBaseUrl}${path}`, { method,
      headers: { "Content-Type": "application/json", "x-acs-dingtalk-access-token": token },
      body: payload, signal: AbortSignal.timeout(10000) });
    const body = await response.json() as Record<string, unknown>;
    if (!response.ok || body.success === false) throw new Error(`DingTalk card request failed (HTTP ${response.status})`);
    return body;
  }

  /** Business callers provide content; credentials, token caching and delivery stay here. */
  async sendNotification(input: {
    recipientUserId: string;
    title: string;
    text: string;
  }): Promise<{ processQueryKey: string | null }> {
    if (!this.enabled) throw new Error("DingTalk notification is not configured");
    if (!input.recipientUserId.trim() || !input.title.trim() || !input.text.trim()) {
      throw new Error("DingTalk notification requires recipient, title and text");
    }
    const accessToken = await this.getAccessToken();
    const recipientUserId = this.normalizeUserId(input.recipientUserId);
    let response: Response;
    try {
      response = await fetch(`${this.config.apiBaseUrl}/v1.0/robot/oToMessages/batchSend`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "x-acs-dingtalk-access-token": accessToken,
        },
        body: JSON.stringify({
          robotCode: this.config.robotCode,
          userIds: [recipientUserId],
          msgKey: "sampleMarkdown",
          msgParam: JSON.stringify({ title: input.title, text: input.text }),
        }),
      });
    } catch (error) {
      throw new Error(`DingTalk send message request failed: ${describeNetworkError(error)}`, { cause: error });
    }
    const body = await response.json() as Record<string, unknown>;
    if (!response.ok) {
      throw new Error(`DingTalk send message HTTP ${response.status}`);
    }
    if (body.success === false && !body.processQueryKey) {
      throw new Error(`DingTalk send message rejected: ${String(body.errmsg ?? body.message ?? "unknown error")}`);
    }
    return { processQueryKey: typeof body.processQueryKey === "string" ? body.processQueryKey : null };
  }

}
