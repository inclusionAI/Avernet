/** Group robot transport. Business content and configuration lookup stay with the caller. */
export class DingTalkWebhookSender {
  constructor(private readonly request: typeof fetch = fetch) {}

  async send(input: { webhook: string; timeoutSeconds: number; title: string; text: string }): Promise<void> {
    if (new URL(input.webhook).protocol !== "https:") throw new Error("DingTalk webhook requires HTTPS");
    if (!Number.isSafeInteger(input.timeoutSeconds) || input.timeoutSeconds < 1 || input.timeoutSeconds > 60) {
      throw new Error("Invalid DingTalk webhook timeout");
    }
    const response = await this.request(input.webhook, {
      method: "POST", redirect: "error",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ msgtype: "markdown", markdown: { title: input.title, text: input.text } }),
      signal: AbortSignal.timeout(input.timeoutSeconds * 1000),
    });
    if (!response.ok) throw new Error(`DingTalk webhook HTTP ${response.status}`);
    const body: unknown = await response.json();
    if (!body || typeof body !== "object" || !("errcode" in body) || body.errcode !== 0) {
      throw new Error("DingTalk webhook rejected the notification");
    }
  }
}
