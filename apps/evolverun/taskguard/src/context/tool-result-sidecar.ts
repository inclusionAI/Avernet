/**
 * 无损工具结果 sidecar writer。
 *
 * tool_result_persist hook 在把工具输出写入主 session JSONL 之前会先截断
 * (以控制 agent 上下文成本)。这种截断是有损且不可逆的,会破坏 ClawBench 评测
 * —— transcript 是唯一的打分数据源。本模块把原始的、未截断的工具结果消息双写到
 * 一个同目录的 sidecar 文件,让评测拿到完整记录。它不修改入参,也绝不向 hook 抛错。
 *
 * @module context/tool-result-sidecar
 */

import { appendFile } from "node:fs/promises";

/**
 * 推导与主 session 文件同目录的 sidecar 路径。
 * `foo.jsonl` -> `foo.full.jsonl`;否则追加 `.full.jsonl`。
 */
export function resolveSidecarPath(sessionFile: string): string {
  if (sessionFile.endsWith(".jsonl")) {
    return `${sessionFile.slice(0, -".jsonl".length)}.full.jsonl`;
  }
  return `${sessionFile}.full.jsonl`;
}

/** 写入 sidecar 的一条无损工具结果记录。 */
export type ToolResultSidecarInput = {
  toolCallId?: string;
  toolName?: string;
  isError?: boolean;
  ts: number;
  /** 原始的、未截断的工具结果消息对象。 */
  message: unknown;
};

/**
 * 把一条 sidecar 记录序列化为一行以换行结尾的 JSON。
 * 返回新字符串;不修改入参。
 */
export function serializeToolResultRecord(input: ToolResultSidecarInput): string {
  const record = {
    type: "tool_result_full",
    toolCallId: input.toolCallId ?? null,
    toolName: input.toolName ?? null,
    isError: input.isError ?? false,
    ts: input.ts,
    message: input.message,
  };
  return `${JSON.stringify(record)}\n`;
}

/** fire-and-forget、按路径串行化的 sidecar writer。 */
export type SidecarWriter = {
  /** 入队一条记录待追加。绝不抛错;失败被吞掉并记日志。 */
  append: (sessionFile: string, input: ToolResultSidecarInput) => void;
  /** 等待某个 session 文件的所有待写入完成(用于测试 / 关闭)。 */
  drain: (sessionFile: string) => Promise<void>;
};

/**
 * 创建一个 sidecar writer。写入 `resolveSidecarPath(sessionFile)`。
 * 同一路径的追加通过 promise 链串行化,保证行不交错、顺序保留。
 * 错误按写入逐条隔离。
 */
export function createSidecarWriter(): SidecarWriter {
  const chains = new Map<string, Promise<void>>();

  const append = (sessionFile: string, input: ToolResultSidecarInput): void => {
    const path = resolveSidecarPath(sessionFile);
    const line = serializeToolResultRecord(input);
    const prev = chains.get(path) ?? Promise.resolve();
    const next = prev
      .then(() => appendFile(path, line, "utf-8"))
      .catch((err) => {
        console.warn(
          `[tool-result-sidecar] append failed path=${path}: ` +
          `${err instanceof Error ? err.message : String(err)}`,
        );
      });
    chains.set(path, next);
  };

  const drain = (sessionFile: string): Promise<void> => {
    const path = resolveSidecarPath(sessionFile);
    return chains.get(path) ?? Promise.resolve();
  };

  return { append, drain };
}

/** tool_result_persist hook 使用的进程级单例。 */
export const sidecarWriter: SidecarWriter = createSidecarWriter();
