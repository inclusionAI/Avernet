/**
 * Best-effort reconciliation for the task secbaas grant.
 *
 * This deliberately bypasses backendRequest: compensation failures (including
 * auth expiry, a route absent in an older deployment, and transport errors)
 * must not enter the global error/toast/login-redirect pipeline.
 */
export const BOT_GRANT_RECONCILIATION_ENDPOINT = '/openapi/v1/collaboration/tasks/bot-grants/reconcile';

export async function reconcileOwnedBotGrants(botId?: string): Promise<void> {
  try {
    await fetch(BOT_GRANT_RECONCILIATION_ENDPOINT, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(botId ? { bot_id: botId } : {}),
    });
  } catch {
    // Best-effort compensation is intentionally invisible to the initiating UI.
  }
}
