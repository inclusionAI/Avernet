export type BotEnvironment = "dev" | "pre" | "prod";
export type BotRuntimeIdentity = { stage: "draft" | "verify" | "online"; deviceUuid?: string; version?: string };
export type BotTarget = { environment: BotEnvironment; ownerId: string; botId: string; runtimeIdentity?: BotRuntimeIdentity };
/** Provider metadata is an internal routing contract, not an HTTP request schema. */
export type ResolvedBotTarget = BotTarget & {
  provider: string; bindingId: string; deviceId: string;
  /** Exact BaaS instance, validated against the Bot binding by the host. */
  deviceUuid?: string; providerDeviceId?: string;
  entityId?: string; entityType?: string; sandboxId?: string; arcaInstanceId?: string; botType?: string; activeEngine?: string;
};
export interface BotSessionTrajectoryReader {
  /** Explicit opt-in: implementations must enforce until on lists and all detail/content reads. */
  readonly supportsHistoricalBoundary?: boolean;
  /** With maxBytes (32–128 KiB), list trace summaries or page observations for traceId. Full input/output
   * is read explicitly with field and optional observationId, using UTF-16 offsets.
   * Results must fit within 128 KiB; next_offset / next_content_offset enable continuation.
   * Without maxBytes, preserve existing full-record reads. Missing uploads are not proof of failure. */
  read(input: { target: BotTarget; sessionKey: string; offset: number; limit: number; traceId?: string;
    maxBytes?: number; observationId?: string; field?: "input" | "output"; contentOffset?: number; contentLimit?: number;
    /** Optional exact user-message match in the scoped trace input; never infer it from previews or tool output. */
    messageMarker?: string; notBeforeMs?: number;
    /** Inclusive historical upper bound. Unknown or unlocatable nodes must not fall back to latest. */
    until?: { atMs: number; traceId?: string; observationId?: string; sessionId?: string } }): Promise<Record<string, unknown>>;
}
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
  path: string; port?: number; body?: unknown; upload?: { targetPath: string; content: string; filename?: string; encoding?: "utf8" | "base64"; fields?: Record<string, string> }; timeoutMs?: number;
};
export type HttpResult = { status: number; body: unknown };
export type MessageInput = {
  expectedEngine?: "openclaw" | "claude_code" | "teclaw";
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
