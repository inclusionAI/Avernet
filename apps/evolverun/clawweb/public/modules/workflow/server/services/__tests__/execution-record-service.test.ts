// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import type { ExecutionRecordFlowRun, ExecutionRecordNodeExecution } from "../../../shared/execution-record.js";
import { readExecutionRecord } from "../execution-record-service.js";

const run: ExecutionRecordFlowRun = {
  id: 1, flow_id: "flow", workflow_id: "workflow", workflow_title: null, status: "running",
  params_json: " [1] ", input_json: "invalid {", result_json: null, node_count: 1,
  succeeded_count: 0, failed_count: 0, total_duration_ms: null, total_token_usage: null,
  triggered_by: null, identity_key: null, current_phase: null, started_at: 1, completed_at: null,
  credentials_json: null, origin_session_key: null, origin_session_id: null, origin_bot_id: null,
  user_id: null, plugin_version: null, gmt_create: 1, gmt_modified: null,
};
const node: ExecutionRecordNodeExecution = {
  id: 2, flow_id: "flow", workflow_id: "workflow", node_id: "node", executor_type: null,
  status: "running", attempt: 2, input_json: " [1] ", error_text: null, duration_ms: null,
  token_usage_json: null, node_title: null, triggered_by: null, branch_id: null, progress_message: null,
  session_key: null, session_id: null, system_context_json: null, embedded_session_key: null,
  started_at: 1, completed_at: null, gmt_create: 1, gmt_modified: null,
};

describe("execution record projection", () => {
  it("projects only callback fields and always redacts credentials even from overbroad repository rows", async () => {
    const unsafeRun = { ...run, credentials_json: "private-credential", state_json: "private-state", engine: "private-engine" };
    const unsafeNode = { ...node, output_json: "private-output", resolved_prompt: "private-prompt", version: 9 };
    const repo = { findExecutionRecordsByFlowIdStrict: vi.fn().mockResolvedValue([unsafeNode]) };
    const result = await readExecutionRecord(unsafeRun as ExecutionRecordFlowRun, repo);
    expect(repo.findExecutionRecordsByFlowIdStrict).toHaveBeenCalledWith("flow", false);
    expect(result).toEqual({
      workflow_id: "workflow", flow_id: "flow", status: "running",
      workflow_yaml: null, workflow_snapshot: { status: "unresolved", reason: "LEGACY_MISSING" },
      ext_info: { flow_runs: run, node_executions: [node] },
    });
    expect(unsafeRun.credentials_json).toBe("private-credential");
    expect(unsafeNode.output_json).toBe("private-output");
  });

  it.each([null, ' { "output" : 1 } ', "not valid JSON {"])("passes enabled output %s through verbatim", async output => {
    const repo = { findExecutionRecordsByFlowIdStrict: vi.fn().mockResolvedValue([{ ...node, output_json: output }]) };
    const result = await readExecutionRecord(run, repo, true);
    expect(repo.findExecutionRecordsByFlowIdStrict).toHaveBeenCalledWith("flow", true);
    expect(result.ext_info.node_executions).toEqual([{ ...node, output_json: output }]);
  });

  it("omits output for explicit false even if the repository row includes it", async () => {
    const repo = { findExecutionRecordsByFlowIdStrict: vi.fn().mockResolvedValue([{ ...node, output_json: "stored" }]) };
    const result = await readExecutionRecord(run, repo, false);
    expect(result.ext_info.node_executions).toEqual([node]);
  });

  it("adds explicit top-level executed workflow metadata without extending the callback projections", async () => {
    const workflow = {
      workflow_yaml: "id: workflow\nnodes: []\n",
      workflow_snapshot: { status: "resolved" as const, snapshot: {
        kind: "deployment" as const, workflowId: "workflow", workflowVersion: 1, deployNumber: 2, configDigest: "a".repeat(64),
      } },
      state_json: "must not leak", credentials_json: "must not leak",
    };
    const repo = { findExecutionRecordsByFlowIdStrict: vi.fn().mockResolvedValue([node]) };
    const result = await readExecutionRecord(run, repo, false, workflow);
    expect(result.workflow_yaml).toBe(workflow.workflow_yaml);
    expect(result.workflow_snapshot).toEqual(workflow.workflow_snapshot);
    expect(result.ext_info).toEqual({ flow_runs: run, node_executions: [node] });
    expect(Object.keys(result).sort()).toEqual(["workflow_id", "flow_id", "status", "workflow_yaml", "workflow_snapshot", "ext_info"].sort());
    expect(JSON.stringify(result)).not.toContain("must not leak");
  });

  it("propagates database errors instead of converting them into an empty snapshot", async () => {
    const error = new Error("database unavailable");
    const repo = { findExecutionRecordsByFlowIdStrict: vi.fn().mockRejectedValue(error) };
    await expect(readExecutionRecord(run, repo)).rejects.toBe(error);
  });
});
