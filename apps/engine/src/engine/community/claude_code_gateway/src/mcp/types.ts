// Wire types for mcp.* RPC methods.
//
// Shape is chosen to match `AiCodingMCPPlugin` in OCB
// (src/engine/src/engine/engines/aicoding/mcp.py):
//   - camelCase `serverCode` on the wire
//   - `type` (aliased to `transport` in storage)
//   - `timeout_seconds` (snake_case, since Python plugin sends it that way)
//   - `enabled` boolean

export type McpTransport = 'stdio' | 'http' | 'sse';

export type McpServerConfig = {
  serverCode: string;
  type: McpTransport;
  url?: string;
  command?: string;
  args: string[];
  env: Record<string, string>;
  headers: Record<string, string>;
  /** Kept snake_case to match the Python plugin's param shape. */
  timeout_seconds: number;
  enabled: boolean;
  /** Optional human description. Surfaced to frontend, not consumed by SDK. */
  description?: string;
};

export type McpError = { code: string; message: string };

export type McpResult<T = unknown> =
  | { ok: true; payload: T }
  | { ok: false; error: McpError };

/**
 * Result type for mcp.filter_servers RPC method.
 *
 * This method is called by OCB's AiCodingMCPPlugin when syncing MCP servers
 * to the device. It filters the local mcporter.json to enable only the
 * specified server_codes and disables all others.
 *
 * The return shape matches what OpenClaw's mcporter CLI returns:
 * - serverCodes: list of codes that should be enabled
 * - command: the effective command that was run (for logging)
 * - returnCode: 0 for success, non-zero for error
 * - stdout/stderr: output from the operation
 */
export type FilterServersResult = {
  serverCodes: string[];
  command: string[];
  returnCode: number;
  stdout: string;
  stderr: string;
};
