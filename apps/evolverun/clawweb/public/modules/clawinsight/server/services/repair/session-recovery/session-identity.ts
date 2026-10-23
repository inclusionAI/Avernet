/** Only the complete OpenClaw Cron-run key proves a physical transcript identity. */
export function cronRunSessionId(sessionKey: string): string | undefined {
  return /^agent:[A-Za-z0-9_-]+:cron:[A-Za-z0-9_-]+:run:([A-Za-z0-9_-]+)$/.exec(sessionKey)?.[1];
}
