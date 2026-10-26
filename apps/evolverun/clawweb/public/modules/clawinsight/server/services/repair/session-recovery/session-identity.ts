/** Only the complete OpenClaw Cron-run key proves a physical transcript identity. */
export function cronRunSessionId(sessionKey: string): string | undefined {
  return /^agent:[A-Za-z0-9_-]+:cron:[A-Za-z0-9_-]+:run:([A-Za-z0-9_-]+)$/.exec(sessionKey)?.[1];
}

/** A direct Cron execution may be stored under its job-level conversation key. */
export function cronConversationKey(sessionKey: string, sessionId: string | undefined): string {
  const match = /^(agent:[A-Za-z0-9_-]+:cron:[A-Za-z0-9_-]+):run:([A-Za-z0-9_-]+)$/.exec(sessionKey);
  return match && match[2] === sessionId ? match[1] : sessionKey;
}
