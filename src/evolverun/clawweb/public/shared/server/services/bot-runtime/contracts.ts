export type BotEnvironment = "dev" | "pre" | "prod";
export type BotTarget = { environment: BotEnvironment; ownerId: string; botId: string };
/** Provider metadata is an internal routing contract, not an HTTP request schema. */
export type ResolvedBotTarget = BotTarget & {
  provider: string; bindingId: string; deviceId: string;
  sandboxId?: string; arcaInstanceId?: string; botType?: string; activeEngine?: string;
};
export interface BotTargetResolver { resolve(target: BotTarget): Promise<ResolvedBotTarget> }
/** Trusted callers can supply their already validated/frozen target. */
export type RuntimeTarget = BotTarget | ResolvedBotTarget;
export type ShellInput = { target: RuntimeTarget; command: string; timeoutMs?: number; deviceAffinity?: string };
export type ShellResult = {
  status: "success" | "failed" | "unknown"; exitCode: number | null;
  stdout: string; stderr: string; durationMs: number | null; error?: string;
};
export type HttpInput = {
  target: RuntimeTarget; method: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  path: string; port?: number; body?: unknown; timeoutMs?: number;
};
export type HttpResult = { status: number; body: unknown };
export type MessageInput = {
  expectedEngine?: "openclaw" | "teclaw";
  target: RuntimeTarget; sessionKey: string; message: string; deliveryKey: string; messageMarker?: string;
};
export type MessageReceipt = {
  status: "accepted" | "unknown" | "failed"; error?: string; acceptedAt?: number;
  runId?: string; sessionId?: string; platformMessageId?: string;
  locationStatus?: "confirmed" | "unconfirmed" | "ambiguous" | "session_changed";
  messageId?: string; messageTimestamp?: string; locatedAt?: number;
};
export interface BotRuntime {
  executeShell(input: ShellInput): Promise<ShellResult>;
  request(input: HttpInput): Promise<HttpResult>;
  sendMessage(input: MessageInput): Promise<MessageReceipt>;
}
export type ResolvedShellInput = Omit<ShellInput, "target"> & { target: ResolvedBotTarget };
export type ResolvedHttpInput = Omit<HttpInput, "target"> & { target: ResolvedBotTarget };
export type ResolvedMessageInput = Omit<MessageInput, "target"> & { target: ResolvedBotTarget; deliveryId: string };
export interface BotRuntimeProvider {
  executeShell(input: ResolvedShellInput): Promise<ShellResult>;
  request(input: ResolvedHttpInput): Promise<HttpResult>;
  sendMessage(input: ResolvedMessageInput): Promise<MessageReceipt>;
}
export interface ArcaConnectionProvider {
  getConnection(input: {
    environment: "pre" | "prod"; bindingId: string; sandboxId: string;
    arcaInstanceId?: string; ttlSeconds: number; port?: number;
  }): Promise<{ target: string; token: string; localOwnerIdentity?: { cookie: string; userId: string } }>;
}
