/**
 * Dynamic Workflow Observability — event types and emitter service.
 *
 * Re-exports DynamicWorkflowEvent types from the core types module
 * and provides an EventEmitter that dual-writes events to:
 * 1. Channel notifications (for real-time UI updates)
 * 2. ExecutionStepLogger (for persistent step log storage)
 *
 * Channel writes are best-effort (fire-and-forget).
 * Step log writes are best-effort (never throw).
 */
import type {
  DynamicWorkflowEvent,
  DynamicWorkflowEventType,
} from "../types.js";
import type { ExecutionStepLogger, StepLogEntry } from "../execution-log/logger.js";

// Re-export types for convenient importing
export type { DynamicWorkflowEvent, DynamicWorkflowEventType } from "../types.js";

// ── Channel notification interface ──

/**
 * Minimal channel notification interface.
 * The actual implementation is provided by the OpenClaw Plugin SDK
 * or the MCP adapter, depending on the runtime platform.
 */
export type ChannelNotifier = {
  /** Send a notification to the channel (e.g., for real-time UI updates). */
  send(event: DynamicWorkflowEvent): void;
};

// ── Event type → step type mapping ──

const EVENT_TO_STEP_TYPE: Record<DynamicWorkflowEventType, StepLogEntry["stepType"]> = {
  node_materialized: "materialize",
  node_injected: "inject",
  llm_evaluation: "llm_evaluate",
  orchestrator_iteration: "replan",
  budget_warning: "budget_warning",
  budget_exhausted: "budget_exhausted",
  yaml_synthesized: "yaml_synthesized",
  synthesis_validated: "synthesis_validated",
  synthesis_rejected: "synthesis_rejected",
  human_approval_requested: "human_approval_requested",
  human_approval_granted: "human_approval_granted",
  human_approval_denied: "human_approval_denied",
};

// ── EventEmitter ──

export class DynamicWorkflowEventEmitter {
  private channel?: ChannelNotifier;
  private logger?: ExecutionStepLogger;

  constructor(deps?: { channel?: ChannelNotifier; logger?: ExecutionStepLogger }) {
    this.channel = deps?.channel;
    this.logger = deps?.logger;
  }

  /**
   * Update the channel notifier (e.g., when session/channel changes).
   */
  setChannel(channel: ChannelNotifier | undefined): void {
    this.channel = channel;
  }

  /**
   * Emit a dynamic workflow event.
   * Dual-writes to Channel (fire-and-forget) and ExecutionStepLogger (best-effort).
   */
  async emit(event: DynamicWorkflowEvent): Promise<void> {
    // 1. Fire-and-forget channel notification
    try {
      this.channel?.send(event);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[observability] Channel notification failed: ${msg}`);
    }

    // 2. Persist to execution step log
    try {
      const stepType = EVENT_TO_STEP_TYPE[event.type];
      if (stepType && this.logger) {
        const entry: StepLogEntry = {
          flowId: event.flowId,
          nodeId: event.nodeId,
          stepType,
          timestamp: event.timestamp,
          decisionPath: event.data,
          metadata: { eventType: event.type, workflowId: event.workflowId },
        };
        await this.logger.log(entry);
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[observability] Step log write failed: ${msg}`);
    }
  }

  /**
   * Convenience: emit a node_materialized event.
   */
  async emitMaterialized(
    flowId: string,
    workflowId: string,
    nodeId: string,
    data: { templateName: string; sourceNodeId: string; index: number },
  ): Promise<void> {
    await this.emit({
      type: "node_materialized",
      flowId,
      workflowId,
      nodeId,
      timestamp: Date.now(),
      data,
    });
  }

  /**
   * Convenience: emit a node_injected event.
   */
  async emitInjected(
    flowId: string,
    workflowId: string,
    nodeId: string,
    data: { action: string; reason: string; stepNum: number },
  ): Promise<void> {
    await this.emit({
      type: "node_injected",
      flowId,
      workflowId,
      nodeId,
      timestamp: Date.now(),
      data,
    });
  }

  /**
   * Convenience: emit an llm_evaluation event.
   */
  async emitLlmEvaluation(
    flowId: string,
    workflowId: string,
    nodeId: string,
    data: { condition: string; result: string; reason: string; model: string; tokenUsage?: number },
  ): Promise<void> {
    await this.emit({
      type: "llm_evaluation",
      flowId,
      workflowId,
      nodeId,
      timestamp: Date.now(),
      data,
    });
  }

  /**
   * Convenience: emit an orchestrator_iteration event.
   */
  async emitOrchestratorIteration(
    flowId: string,
    workflowId: string,
    nodeId: string,
    data: { iteration: number; action: string; reason: string },
  ): Promise<void> {
    await this.emit({
      type: "orchestrator_iteration",
      flowId,
      workflowId,
      nodeId,
      timestamp: Date.now(),
      data,
    });
  }

  /**
   * Convenience: emit a budget_warning event.
   */
  async emitBudgetWarning(
    flowId: string,
    workflowId: string,
    nodeId: string,
    data: { budgetType: string; used: number; limit: number; ratio: number },
  ): Promise<void> {
    await this.emit({
      type: "budget_warning",
      flowId,
      workflowId,
      nodeId,
      timestamp: Date.now(),
      data,
    });
  }

  /**
   * Convenience: emit a budget_exhausted event.
   */
  async emitBudgetExhausted(
    flowId: string,
    workflowId: string,
    nodeId: string,
    data: { budgetType: string; used: number; limit: number; ratio: number },
  ): Promise<void> {
    await this.emit({
      type: "budget_exhausted",
      flowId,
      workflowId,
      nodeId,
      timestamp: Date.now(),
      data,
    });
  }

  // ── Synthesis event convenience methods ──

  /**
   * Convenience: emit a yaml_synthesized event.
   * Fired after each LLM call in the synthesis loop produces a YAML output.
   */
  async emitYamlSynthesized(
    flowId: string,
    workflowId: string,
    nodeId: string,
    data: { round: number; model: string; tokenUsage: number; goalLength: number },
  ): Promise<void> {
    await this.emit({
      type: "yaml_synthesized",
      flowId,
      workflowId,
      nodeId,
      timestamp: Date.now(),
      data,
    });
  }

  /**
   * Convenience: emit a synthesis_validated event.
   * Fired when the three-stage validation pipeline passes.
   */
  async emitSynthesisValidated(
    flowId: string,
    workflowId: string,
    nodeId: string,
    data: { round: number; nodeCount: number; warningCount: number },
  ): Promise<void> {
    await this.emit({
      type: "synthesis_validated",
      flowId,
      workflowId,
      nodeId,
      timestamp: Date.now(),
      data,
    });
  }

  /**
   * Convenience: emit a synthesis_rejected event.
   * Fired when validation fails (per correction round).
   */
  async emitSynthesisRejected(
    flowId: string,
    workflowId: string,
    nodeId: string,
    data: { round: number; errorCount: number; errorStages: string[] },
  ): Promise<void> {
    await this.emit({
      type: "synthesis_rejected",
      flowId,
      workflowId,
      nodeId,
      timestamp: Date.now(),
      data,
    });
  }

  /**
   * Convenience: emit a human_approval_requested event.
   * Fired when the human gate determines approval is needed.
   */
  async emitHumanApprovalRequested(
    flowId: string,
    workflowId: string,
    nodeId: string,
    data: { strategy: string; triggeredWarnings: string[]; reason: string },
  ): Promise<void> {
    await this.emit({
      type: "human_approval_requested",
      flowId,
      workflowId,
      nodeId,
      timestamp: Date.now(),
      data,
    });
  }

  /**
   * Convenience: emit a human_approval_granted event.
   * Fired when the user approves the synthesized workflow.
   */
  async emitHumanApprovalGranted(
    flowId: string,
    workflowId: string,
    nodeId: string,
    data: { approvedBy: string; note?: string },
  ): Promise<void> {
    await this.emit({
      type: "human_approval_granted",
      flowId,
      workflowId,
      nodeId,
      timestamp: Date.now(),
      data,
    });
  }

  /**
   * Convenience: emit a human_approval_denied event.
   * Fired when the user rejects the synthesized workflow.
   */
  async emitHumanApprovalDenied(
    flowId: string,
    workflowId: string,
    nodeId: string,
    data: { deniedBy: string; reason?: string },
  ): Promise<void> {
    await this.emit({
      type: "human_approval_denied",
      flowId,
      workflowId,
      nodeId,
      timestamp: Date.now(),
      data,
    });
  }
}