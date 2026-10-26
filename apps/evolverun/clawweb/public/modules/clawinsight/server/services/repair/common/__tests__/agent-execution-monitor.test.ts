import { expect, it, vi } from "vitest";
import { createServer } from "node:http";
import { reconcileAgentExecutions } from "../agent-execution-monitor.js";

function fixture(count = 106) {
  const rows = Array.from({ length: count }, (_, index) => ({ id: index + 1, task_id: `task-${index + 1}`, step_id: `step-${index + 1}` }));
  const configs = new Map(rows.map(row => [row.task_id, { controlPlaneEnvironment: "pre", execution: {
    executionId: `exec-${row.id}`, state: "running", leaseExpiresAt: 200,
  } }]));
  const provider = { getJobStatusDetail: vi.fn(async (_job: string) => ({ status: "running", rawStatus: "running" })), stopExecution: vi.fn() };
  const handlers = { repair: vi.fn(), session_recovery: vi.fn() };
  const deps = {
    controlPlaneEnvironment: "pre" as const, nowSeconds: () => 100,
    db: { query: vi.fn(async (_sql: string, params: unknown[]) => rows.filter(row => row.id > Number(params[0])).slice(0, 100)) },
    repo: {
      findTask: vi.fn(async (id: string) => ({ task_id: id, task_type: "repair", config_json: JSON.stringify(configs.get(id)) })),
      findStep: vi.fn(async (id: string) => {
        const number = Number(id.split("-")[1]);
        return { step_id: id, task_id: `task-${number}`, status: "running", bot_run_id: `job-${number}`,
          ext_json: JSON.stringify({ aisExecution: { stepId: id, artifacts: {}, aisBase: {
            executionId: `exec-${number}`, statusHandler: number % 2 ? "repair" : "session_recovery",
          } } }) };
      }),
    },
    handlers, providers: { repair: provider, session_recovery: provider },
  };
  return { deps: deps as unknown as Parameters<typeof reconcileAgentExecutions>[0], provider, handlers, configs };
}

it("continues through later pages while one AIS status request is slow", async () => {
  const { deps, provider } = fixture();
  let release!: () => void;
  provider.getJobStatusDetail.mockImplementation(async job => {
    if (job === "job-1") await new Promise<void>(resolve => { release = resolve; });
    return { status: "running", rawStatus: "running" };
  });
  const checking = reconcileAgentExecutions(deps);
  try {
    await vi.waitFor(() => expect(provider.getJobStatusDetail).toHaveBeenCalledWith("job-106"), { timeout: 1000 });
  } finally { release(); await checking; }
});

it("bounds in-flight work while allowing independent records to run concurrently", async () => {
  const { deps, provider } = fixture(20);
  let active = 0, maximum = 0;
  provider.getJobStatusDetail.mockImplementation(async () => {
    active++; maximum = Math.max(active, maximum);
    await new Promise(resolve => setImmediate(resolve));
    active--;
    return { status: "running", rawStatus: "running" };
  });
  await reconcileAgentExecutions(deps);
  expect(maximum).toBeGreaterThan(1);
  expect(maximum).toBeLessThanOrEqual(4);
  expect(provider.getJobStatusDetail).toHaveBeenCalledTimes(20);
});

it("serves an HTTP request while the AIS status request is pending", async () => {
  const { deps, provider } = fixture(1);
  let release!: () => void;
  provider.getJobStatusDetail.mockImplementation(async () => {
    await new Promise<void>(resolve => { release = resolve; });
    return { status: "running", rawStatus: "running" };
  });
  const server = createServer((_req, res) => { res.end("responsive"); });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const checking = reconcileAgentExecutions(deps);
  try {
    await vi.waitFor(() => expect(provider.getJobStatusDetail).toHaveBeenCalledOnce());
    const address = server.address();
    if (!address || typeof address === "string") throw new Error("Missing test HTTP port");
    const response = await fetch(`http://127.0.0.1:${address.port}`, { signal: AbortSignal.timeout(1000) });
    expect(await response.text()).toBe("responsive");
  } finally {
    release(); await checking;
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});

it("isolates an unavailable AIS status without failing that task or skipping other records", async () => {
  const { deps, provider, handlers } = fixture(3);
  provider.getJobStatusDetail.mockRejectedValueOnce(new Error("AIS status timeout"));
  const log = vi.spyOn(console, "error").mockImplementation(() => {});
  try {
    await reconcileAgentExecutions(deps);
    expect(provider.getJobStatusDetail).toHaveBeenCalledTimes(3);
    expect(provider.stopExecution).not.toHaveBeenCalled();
    expect(handlers.repair).not.toHaveBeenCalled();
    expect(handlers.session_recovery).not.toHaveBeenCalled();
  } finally { log.mockRestore(); }
});

it("keeps Repair and recovery interruption handling separate", async () => {
  const { deps, provider, handlers } = fixture(2);
  provider.getJobStatusDetail.mockResolvedValue({ status: "failed", rawStatus: "failed" });
  await reconcileAgentExecutions(deps);
  expect(handlers.repair).toHaveBeenCalledOnce();
  expect(handlers.session_recovery).toHaveBeenCalledOnce();
});

it("does not stop a job or report failure after losing scan ownership during an AIS query", async () => {
  const { deps, provider, handlers, configs } = fixture(1);
  configs.get("task-1")!.execution.leaseExpiresAt = 99;
  let active = true;
  deps.isActive = async () => active;
  provider.getJobStatusDetail.mockImplementation(async () => {
    active = false;
    return { status: "running", rawStatus: "running" };
  });
  await reconcileAgentExecutions(deps);
  expect(provider.stopExecution).not.toHaveBeenCalled();
  expect(handlers.repair).not.toHaveBeenCalled();
  expect(handlers.session_recovery).not.toHaveBeenCalled();
});
