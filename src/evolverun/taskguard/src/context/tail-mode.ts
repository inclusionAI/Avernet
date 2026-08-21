/**
 * Tail mode implementation for session file context management.
 *
 * Implements the declared-but-unimplemented `contextPolicy.history=tail` mode:
 * reads the existing session JSONL file, keeps only the last N messages,
 * optionally filters out tool injection messages, and writes a trimmed session.
 *
 * @module context/tail-mode
 */

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";
import type { BuiltNodeExecutionContext } from "../execution-context.js";
import { parseSessionLine, estimateSessionTokens } from "./session-reader.js";
import { applyToolOutputPrepass } from "./tool-output-prepass.js";
import type { SessionMessage } from "./session-reader.js";

// ── Types ──

/** Options for building a tail-mode session file. */
export type TailModeOptions = {
  /** Path to the current session file to read. */
  currentSessionFile: string;
  /** Workflow ID for file path construction. */
  workflowId: string;
  /** Flow execution ID for file path construction. */
  flowId: string;
  /** Node ID for file path construction. */
  nodeId: string;
  /** Current attempt number for file path construction. */
  attempt: number;
  /** Number of messages to keep from the tail. Default: 10 */
  tailMessages: number;
  /** Whether to exclude tool injection messages. Default: false */
  excludeInjectMessages: boolean;
  /** Root directory for session files. Default: ~/.openclaw/logs/clawmind/embedded-sessions */
  rootDir?: string;
  /** Whether to apply tool-output-prepass before tailing. Default: true */
  tailPrepass?: boolean;
};

// ── File path utilities ──

function safePathPart(value: string): string {
  return value.replace(/[^a-zA-Z0-9._-]/g, "_");
}

function tailContextRoot(rootDir?: string): string {
  return rootDir ?? join(
    homedir(),
    ".openclaw",
    "logs",
    "clawmind",
    "embedded-sessions",
  );
}

// ── Tail mode session builder ──

/**
 * Build a tail-trimmed session file from the current session.
 *
 * Reads the JSONL session file, optionally applies tool-output-prepass
 * compression, keeps the last `tailMessages` messages, and writes a new
 * per-attempt session file.
 *
 * When `tailPrepass` is enabled (default), tool_output_prepass runs on
 * all messages before the tail slice, ensuring verbose tool results in
 * the retained window are also compressed.
 *
 * @returns BuiltNodeExecutionContext with the tail session file path
 */
export async function buildTailSessionFile(
  options: TailModeOptions,
): Promise<BuiltNodeExecutionContext> {
  const {
    currentSessionFile,
    workflowId,
    flowId,
    nodeId,
    attempt,
    tailMessages,
    excludeInjectMessages,
    rootDir,
    tailPrepass = true,
  } = options;

  // 1. Read and parse the current session file into structured SessionMessages
  let rawContent: string;
  try {
    rawContent = await readFile(currentSessionFile, "utf8");
  } catch {
    rawContent = "";
  }

  const allMessages: SessionMessage[] = rawContent
    .split(/\r?\n/)
    .map((line) => parseSessionLine(line))
    .filter((msg): msg is SessionMessage => msg !== null);

  // 2. Separate system messages from the rest
  const systemMessages: SessionMessage[] = [];
  const otherMessages: SessionMessage[] = [];

  for (const msg of allMessages) {
    if (msg.role === "system") {
      systemMessages.push(msg);
    } else {
      otherMessages.push(msg);
    }
  }

  // 3. Optionally filter out tool injection messages
  const filteredMessages = excludeInjectMessages
    ? otherMessages.filter((msg) => !msg.isToolResult)
    : otherMessages;

  // 4. Apply tool-output-prepass to filtered messages (compresses verbose tool results)
  let processedMessages = filteredMessages;
  if (tailPrepass) {
    const prepassResult = applyToolOutputPrepass(filteredMessages);
    processedMessages = prepassResult.messages;
  }

  // 5. Keep only the last `tailMessages` messages from the tail
  const tailSlice = processedMessages.slice(-tailMessages);

  // 6. Build the new session file content
  const sessionLines: string[] = [];

  // Add a system message explaining the tail trimming
  const tailSystemMessage = JSON.stringify({
    type: "message",
    timestamp: new Date().toISOString(),
    message: {
      role: "system",
      content: [{
        type: "text",
        text: `这是 tail 模式的上下文窗口。为节省 token，只保留了最近 ${tailMessages} 条消息（共 ${filteredMessages.length} 条）。更早的历史消息已被裁剪。${tailPrepass ? " 冗长的工具输出已被自动截断。" : ""}`,
      }],
    },
  });
  sessionLines.push(tailSystemMessage);

  // Add original system messages (important for agent behavior)
  for (const msg of systemMessages) {
    sessionLines.push(msg.raw);
  }

  // Add the tail messages
  for (const msg of tailSlice) {
    sessionLines.push(msg.raw);
  }

  // 7. Write to a new per-attempt session file
  const dir = join(
    tailContextRoot(rootDir),
    safePathPart(workflowId),
    safePathPart(flowId),
  );
  const sessionFile = join(
    dir,
    `${safePathPart(nodeId)}-attempt-${attempt}-tail.jsonl`,
  );

  await mkdir(dir, { recursive: true });
  await writeFile(sessionFile, `${sessionLines.join("\n")}\n`, "utf8");

  return {
    history: "tail",
    sessionFile,
    inheritedSessionFile: false,
    includedNodeOutputs: [],
    compressionStats: undefined,
    workflowContext: undefined,
  };
}