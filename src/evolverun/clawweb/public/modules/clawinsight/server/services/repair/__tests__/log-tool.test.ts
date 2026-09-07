import { describe, expect, it, vi } from "vitest";
import type { AntLogsCollector, AntLogsCollectResult, LogEntry } from "../../antlogs-collector.js";
import type { RepairTaskContext } from "../contracts.js";
import { RepairLogTool, selectRepairLogSources } from "../log-tool.js";

const context = {
  target: { botId: "bot-1", ownerId: "user-1" },
  issue: {
    traceId: null,
    relatedTaskId: null,
    errorText: null,
    timeRange: { from: 1_000, to: 2_000 },
  },
} as RepairTaskContext;

function collector(entries: LogEntry[]): AntLogsCollector {
  const source = {
    name: "agentclaw",
    region: "cn-hangzhou",
    app: "agentclaw",
    tenant: "alipay",
  };
  const result: AntLogsCollectResult = {
    entries,
    sourceResults: [{
      source,
      status: "success",
      entries,
      totalAvailable: entries.length,
      entriesCount: entries.length,
      errorEntriesCount: 0,
      durationMs: 10,
      batonRounds: 1,
    }],
    totalEntries: entries.length,
    totalErrors: 0,
    durationMs: 10,
    allSourcesSucceeded: true,
    collectorType: "antlogs",
  };
  return { search: vi.fn(async () => result) } as unknown as AntLogsCollector;
}

describe("RepairLogTool result bounds", () => {
  it("keeps small results inline without truncation", async () => {
    const tool = new RepairLogTool(collector([{
      timestamp: "2026-08-19T00:00:00Z",
      level: "INFO",
      source: "agentclaw",
      message: "binding_id=binding-1",
      metadata: { bindingId: "binding-1" },
    }]), ["agentclaw"]);

    const result = await tool.search(context, { identifiers: ["botId"], limit: 200 });

    expect(result).toMatchObject({ status: "success", truncated: false, totalEntries: 1, returnedEntries: 1 });
    expect(result.discoveredIdentifiers).toEqual([{ kind: "bindingId", value: "binding-1" }]);
  });

  it("redacts signed-query credentials found in historical log messages", async () => {
    const secret = "historical-log-signature-canary";
    const tool = new RepairLogTool(collector([{
      timestamp: "2026-08-19T00:00:00Z",
      level: "ERROR",
      source: "agentclaw",
      message: `download failed: https://oss.example/object?X-Amz-Signature=${secret}&part=1`,
      metadata: {},
    }]), ["agentclaw"]);

    const result = await tool.search(context, { identifiers: ["botId"], limit: 20 });
    const serialized = JSON.stringify(result);
    expect(serialized).toContain("[REDACTED_SECRET_TEXT]");
    expect(serialized).not.toContain(secret);
  });

  it("redacts one credential-bearing entry without rejecting the historical result batch", async () => {
    const entries: LogEntry[] = Array.from({ length: 16 }, (_, index) => ({
      timestamp: `2026-08-19T00:00:${String(index).padStart(2, "0")}Z`,
      level: "INFO",
      source: "agentclaw",
      message: index === 14 ? "authorization=historical-secret" : `safe-entry-${index}`,
      metadata: {},
    }));
    const tool = new RepairLogTool(collector(entries), ["agentclaw"]);

    const result = await tool.search(context, { identifiers: ["botId"], limit: 20 });
    const returned = result.entries as Array<{ message: string }>;

    expect(returned).toHaveLength(16);
    expect(returned[14]?.message).toBe("[REDACTED_SECRET_TEXT]");
    expect(returned[15]?.message).toBe("safe-entry-15");
    expect(JSON.stringify(result)).not.toContain("historical-secret");
  });

  it("preserves useful AntLogs context when only one field contains a credential", async () => {
    const tool = new RepairLogTool(collector([{
      timestamp: "2026-08-20T13:48:00Z",
      level: "INFO",
      source: "agentclaw",
      message: "session=mcp-setup authorization=historical-secret action=engine_config_patch status=succeeded",
      metadata: {},
    }]), ["agentclaw"]);

    const result = await tool.search(context, { identifiers: ["botId"], limit: 20 });
    const [entry] = result.entries as Array<{ message: string }>;
    expect(entry?.message).toContain("session=mcp-setup");
    expect(entry?.message).toContain("action=engine_config_patch status=succeeded");
    expect(entry?.message).toContain("[REDACTED_SECRET_TEXT]");
    expect(JSON.stringify(result)).not.toContain("historical-secret");
  });

  it("truncates worst-case AntLogs output below the repository hard limit", async () => {
    const entries: LogEntry[] = Array.from({ length: 200 }, (_, index) => ({
      timestamp: `2026-08-19T00:00:${String(index % 60).padStart(2, "0")}Z`,
      level: "INFO",
      source: "agentclaw",
      message: `${index}:${"x".repeat(1_024)}`,
      metadata: { traceId: `trace-${index}-${"y".repeat(240)}` },
    }));
    const tool = new RepairLogTool(collector(entries), ["agentclaw"]);

    const result = await tool.search(context, { identifiers: ["botId"], limit: 200 });

    expect(Buffer.byteLength(JSON.stringify(result), "utf8")).toBeLessThanOrEqual(192 * 1024);
    expect(result).toMatchObject({ status: "partial", truncated: true, totalEntries: 200 });
    expect(Number(result.returnedEntries)).toBeGreaterThan(0);
    expect(Number(result.returnedEntries)).toBeLessThan(200);
  });

  it("separates covered sources from a READ-ACL evidence gap", async () => {
    const antlogs = collector([]);
    vi.mocked(antlogs.search).mockResolvedValueOnce({
      entries: [],
      sourceResults: [
        {
          source: { name: "agentclaw", region: "et15", app: "agentclaw", tenant: "alipay" },
          status: "success",
          entries: [],
          totalAvailable: 0,
          entriesCount: 0,
          errorEntriesCount: 0,
          durationMs: 8,
          batonRounds: 1,
        },
        {
          source: { name: "clawweb", region: "et15", app: "clawweb", tenant: "alipay" },
          status: "failed",
          entries: [],
          totalAvailable: 0,
          entriesCount: 0,
          errorEntriesCount: 0,
          durationMs: 3,
          error: "region et15 ACL READ 被拒",
          batonRounds: 0,
        },
      ],
      totalEntries: 0,
      totalErrors: 0,
      durationMs: 8,
      allSourcesSucceeded: false,
      collectorType: "antlogs",
    });
    const tool = new RepairLogTool(antlogs, ["agentclaw", "clawweb"]);

    const result = await tool.search(context, { identifiers: ["botId"] });

    expect(result.sourceCoverage).toEqual({
      coveredSources: [{ name: "agentclaw", status: "success", entriesCount: 0 }],
      unavailableSources: [{ name: "clawweb", reasonCode: "read_acl_denied", reason: "READ 权限不足" }],
      interpretation: "未覆盖的日志源仅表示本次未取得该来源的证据，不代表对应服务异常。",
    });
  });
});

describe("RepairLogTool query safety", () => {
  it("uses the issue time range only as the default when no explicit range is requested", async () => {
    const antlogs = collector([]);
    const tool = new RepairLogTool(antlogs, ["agentclaw"]);

    await tool.search(context, { identifiers: ["botId"] });

    expect(antlogs.search).toHaveBeenCalledWith(expect.objectContaining({
      from: 1_000,
      to: 2_000,
    }));
  });

  it("allows explicit historical log ranges outside the issue window without a duration limit", async () => {
    const antlogs = collector([]);
    const tool = new RepairLogTool(antlogs, ["agentclaw"]);

    await tool.search(context, {
      identifiers: ["botId"],
      from: 100,
      to: 100 + 30 * 24 * 60 * 60,
    });

    expect(antlogs.search).toHaveBeenCalledWith(expect.objectContaining({
      from: 100,
      to: 2_592_100,
    }));
  });

  it("rejects only malformed or reversed explicit log ranges", async () => {
    const antlogs = collector([]);
    const tool = new RepairLogTool(antlogs, ["agentclaw"]);

    await expect(tool.search(context, {
      identifiers: ["botId"],
      from: 3_000,
      to: 2_000,
    })).rejects.toMatchObject({ code: "invalid_log_time_range" });
    expect(antlogs.search).not.toHaveBeenCalled();
  });

  it("uses explicit Repair defaults and excludes clawweb from both default and allowed sources", async () => {
    const selected = selectRepairLogSources([
      { name: "后端", app: "agentclaw", defaultEnabled: true },
      { name: "ARCA", app: "arcaagentclaw", defaultEnabled: true },
      { name: "BCN", app: "agentclawscs", defaultEnabled: false },
      { name: "secbaas", app: "secbaas", defaultEnabled: true },
      { name: "renamed-control-plane", app: "clawweb", defaultEnabled: true },
      { name: "clawweb", app: "another-app", defaultEnabled: true },
    ]);
    expect(selected).toEqual({
      allowedSourceNames: ["后端", "ARCA", "BCN", "secbaas"],
      defaultSourceNames: ["后端", "BCN"],
    });
    const antlogs = collector([]);
    const tool = new RepairLogTool(
      antlogs,
      selected.allowedSourceNames,
      selected.defaultSourceNames,
    );

    await tool.search(context, { identifiers: ["botId"] });

    expect(antlogs.search).toHaveBeenCalledWith(expect.objectContaining({ sources: ["后端", "BCN"] }));
    await expect(tool.search(context, { identifiers: ["botId"], sources: ["clawweb"] }))
      .rejects.toMatchObject({ code: "invalid_log_sources" });
  });

  it("fails closed on duplicate source names instead of relying on collector first-match resolution", () => {
    const selected = selectRepairLogSources([
      { name: "BACKEND", app: "clawweb", defaultEnabled: true },
      { name: " backend ", app: "agentclaw", defaultEnabled: true },
      { name: "BCN", app: "agentclawscs", defaultEnabled: true },
    ]);

    expect(selected).toEqual({
      allowedSourceNames: ["BCN"],
      defaultSourceNames: ["BCN"],
    });
  });

  it("does not misclassify ordinary read-access parse failures as ACL denial", async () => {
    const antlogs = collector([]);
    vi.mocked(antlogs.search).mockResolvedValueOnce({
      entries: [],
      sourceResults: [{
        source: { name: "后端", region: "et15", app: "agentclaw", tenant: "alipay" },
        status: "failed",
        entries: [],
        totalAvailable: 0,
        entriesCount: 0,
        errorEntriesCount: 0,
        durationMs: 3,
        error: "read access log parse failed",
        batonRounds: 0,
      }],
      totalEntries: 0,
      totalErrors: 0,
      durationMs: 3,
      allSourcesSucceeded: false,
      collectorType: "antlogs",
    });
    const tool = new RepairLogTool(antlogs, ["后端"], ["后端"]);

    const result = await tool.search(context, { identifiers: ["botId"] });

    expect(result.sourceCoverage).toMatchObject({
      unavailableSources: [{ name: "后端", reasonCode: "query_failed", reason: "查询失败" }],
    });
  });

  it("passes a single Bot ID to AntLogs as an unquoted token", async () => {
    const antlogs = collector([]);
    const tool = new RepairLogTool(antlogs, ["agentclaw"]);

    await tool.search({
      ...context,
      target: { ...context.target, botId: "20260814_0c92ekfp" },
    }, { identifiers: ["botId"] });

    expect(antlogs.search).toHaveBeenCalledWith(expect.objectContaining({
      keyword: "20260814_0c92ekfp",
      suppressQueryLog: true,
    }));
  });

  it("combines multiple identifiers with the supported lowercase and operator", async () => {
    const antlogs = collector([]);
    const tool = new RepairLogTool(antlogs, ["agentclaw"]);

    await tool.search(context, { identifiers: ["botId", "ownerId"] });

    expect(antlogs.search).toHaveBeenCalledWith(expect.objectContaining({
      keyword: "bot-1 and user-1",
    }));
  });

  it("converts arbitrary error text to bounded literal tokens without LogQL syntax", async () => {
    const antlogs = collector([]);
    const tool = new RepairLogTool(antlogs, ["agentclaw"]);

    await tool.search({
      ...context,
      issue: {
        ...context.issue,
        errorText: "Authentication failed\" OR * /home/admin?tokenless=true",
      },
    }, { identifiers: ["errorText"] });

    const invocation = vi.mocked(antlogs.search).mock.calls[0]?.[0];
    expect(invocation?.keyword).toBe("Authentication and failed and home and admin and tokenless");
    expect(invocation?.keyword).not.toMatch(/["'*?/=]/);
    expect(invocation?.keyword.toLowerCase().split(" and ")).not.toContain("or");
  });

  it("fails closed when an identifier contains no safe searchable token", async () => {
    const antlogs = collector([]);
    const tool = new RepairLogTool(antlogs, ["agentclaw"]);

    await expect(tool.search({
      ...context,
      issue: { ...context.issue, errorText: " \t\n* ? / " },
    }, { identifiers: ["errorText"] })).rejects.toMatchObject({
      code: "invalid_log_identifier_value",
      message: "日志标识无法安全转换为查询词",
    });
    expect(antlogs.search).not.toHaveBeenCalled();
  });

  it("rejects secret-bearing error text without echoing it or calling AntLogs", async () => {
    const antlogs = collector([]);
    const tool = new RepairLogTool(antlogs, ["agentclaw"]);
    const secret = "sk-sensitive-value-1234567890";

    let caught: unknown;
    try {
      await tool.search({
        ...context,
        issue: { ...context.issue, errorText: `Authorization: Bearer ${secret}` },
      }, { identifiers: ["errorText"] });
    } catch (error) {
      caught = error;
    }

    expect(caught).toMatchObject({ code: "unsafe_log_identifier_value" });
    expect(String((caught as Error).message)).not.toContain(secret);
    expect(antlogs.search).not.toHaveBeenCalled();
  });

  it("does not echo collector failure details that may contain sensitive input", async () => {
    const leakedDetail = "sensitive-upstream-detail-123456";
    const antlogs = {
      search: vi.fn(async () => {
        throw new Error(leakedDetail);
      }),
    } as unknown as AntLogsCollector;
    const tool = new RepairLogTool(antlogs, ["agentclaw"]);

    let caught: unknown;
    try {
      await tool.search(context, { identifiers: ["botId"] });
    } catch (error) {
      caught = error;
    }

    expect(caught).toMatchObject({ code: "antlogs_query_failed", message: "AntLogs 查询失败" });
    expect(String((caught as Error).message)).not.toContain(leakedDetail);
  });
});
