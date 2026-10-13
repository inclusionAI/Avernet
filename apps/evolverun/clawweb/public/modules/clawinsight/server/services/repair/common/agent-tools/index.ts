import type { BotRuntime, BotSessionTrajectoryReader } from "@avernet/clawweb-shared/server/services/bot-runtime";
import type { LogAnalysisConfig } from "@avernet/clawweb-shared/server/db";
import { AntLogsCollector } from "../../../antlogs-collector.js";
import { AgentLogTool, selectAgentLogSources } from "./log-tool.js";
import { AgentRuntimeTool } from "./runtime-tool.js";

export function createAgentTools(config: LogAnalysisConfig, runtime: Pick<BotRuntime, "executeShell"> & Partial<Pick<BotRuntime, "request">>, trajectory?: BotSessionTrajectoryReader) {
  const sources = selectAgentLogSources(config.antlogs?.sources ?? []);
  return { runtimeTool: new AgentRuntimeTool(runtime, trajectory), logs: new AgentLogTool(
    config.antlogs ? new AntLogsCollector(config.antlogs) : null, sources.allowedSourceNames, sources.defaultSourceNames) };
}
