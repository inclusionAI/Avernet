import type {
  CollaborationDefinitionAssignee,
  CollaborationDefinitionDiagnostic,
  CollaborationDefinitionGraph,
  CollaborationDefinitionGraphEdge,
  CollaborationDefinitionGraphNode,
  CollaborationDefinitionLoop,
  CollaborationDefinitionParticipant,
  CollaborationDefinitionSummary,
  CollaborationDefinitionValidationResult,
} from './types';
import { collaborationApiUrl } from './api';

const VALIDATION_TIMEOUT_MS = 15_000;

export class CollaborationDefinitionValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'CollaborationDefinitionValidationError';
  }
}

function invalid(message: string): never {
  throw new CollaborationDefinitionValidationError(
    `校验服务响应无效：${message}`,
  );
}

function record(value: unknown, path: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    invalid(`${path} 必须是对象`);
  }
  return value as Record<string, unknown>;
}

function string(value: unknown, path: string): string {
  return typeof value === 'string' ? value : invalid(`${path} 必须是字符串`);
}

function optionalString(value: unknown, path: string): string | undefined {
  return value === undefined ? undefined : string(value, path);
}

function boolean(value: unknown, path: string): boolean {
  return typeof value === 'boolean' ? value : invalid(`${path} 必须是布尔值`);
}

function integer(value: unknown, path: string, minimum = 0): number {
  return typeof value === 'number' && Number.isInteger(value) && value >= minimum
    ? value
    : invalid(`${path} 必须是大于等于 ${minimum} 的整数`);
}

function strings(value: unknown, path: string): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) invalid(`${path} 必须是数组`);
  return value.map((item, index) => string(item, `${path}[${index}]`));
}

function diagnostics(
  value: unknown,
  path: string,
): CollaborationDefinitionDiagnostic[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) invalid(`${path} 必须是数组`);
  return value.map((item, index) => {
    const entry = record(item, `${path}[${index}]`);
    return {
      code: string(entry.code, `${path}[${index}].code`),
      path: string(entry.path, `${path}[${index}].path`),
      message: string(entry.message, `${path}[${index}].message`),
      ...(entry.hint === undefined
        ? {}
        : { hint: string(entry.hint, `${path}[${index}].hint`) }),
    };
  });
}

function summary(value: unknown): CollaborationDefinitionSummary {
  const entry = record(value, 'summary');
  return {
    participants: integer(entry.participants, 'summary.participants'),
    nodes: integer(entry.nodes, 'summary.nodes'),
    initial_nodes: strings(entry.initial_nodes, 'summary.initial_nodes'),
    ...(entry.final_output_node === undefined
      ? {}
      : {
          final_output_node: string(
            entry.final_output_node,
            'summary.final_output_node',
          ),
        }),
  };
}

function participants(value: unknown): CollaborationDefinitionParticipant[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) invalid('participants 必须是数组');
  return value.map((item, index) => {
    const path = `participants[${index}]`;
    const entry = record(item, path);
    return {
      binding: string(entry.binding, `${path}.binding`),
      required: boolean(entry.required, `${path}.required`),
      assigned: boolean(entry.assigned, `${path}.assigned`),
      ...(entry.display_name === undefined
        ? {}
        : { display_name: string(entry.display_name, `${path}.display_name`) }),
      ...(entry.description === undefined
        ? {}
        : { description: string(entry.description, `${path}.description`) }),
    };
  });
}

function assignee(
  value: unknown,
  path: string,
): CollaborationDefinitionAssignee | undefined {
  if (value === undefined) return undefined;
  const entry = record(value, path);
  const type = string(entry.type, `${path}.type`);
  if (type === 'bot_binding') {
    return {
      type,
      binding: string(entry.binding, `${path}.binding`),
    };
  }
  if (type === 'runtime_actor') {
    return { type, actor: string(entry.actor, `${path}.actor`) };
  }
  return invalid(`${path}.type 不受支持`);
}

function node(value: unknown, path: string): CollaborationDefinitionGraphNode {
  const entry = record(value, path);
  let execution: CollaborationDefinitionGraphNode['execution'];
  if (entry.execution !== undefined) {
    const raw = record(entry.execution, `${path}.execution`);
    execution = {
      definition_node_id: string(
        raw.definition_node_id,
        `${path}.execution.definition_node_id`,
      ),
      loop_id: string(raw.loop_id, `${path}.execution.loop_id`),
      iteration: integer(raw.iteration, `${path}.execution.iteration`, 1),
      max_iterations: integer(
        raw.max_iterations,
        `${path}.execution.max_iterations`,
        1,
      ),
    };
    if (raw.loop_path !== undefined) {
      if (!Array.isArray(raw.loop_path) || !raw.loop_path.length) {
        invalid(`${path}.execution.loop_path 必须是非空数组`);
      }
      execution.loop_path = raw.loop_path.map((value, index) => {
        const framePath = `${path}.execution.loop_path[${index}]`;
        const frame = record(value, framePath);
        return {
          loop_id: string(frame.loop_id, `${framePath}.loop_id`),
          iteration: integer(frame.iteration, `${framePath}.iteration`, 1),
          max_iterations: integer(frame.max_iterations, `${framePath}.max_iterations`, 1),
        };
      });
    }
  }
  const parsedAssignee = assignee(entry.assignee, `${path}.assignee`);
  return {
    node_id: string(entry.node_id, `${path}.node_id`),
    display_name: string(entry.display_name, `${path}.display_name`),
    kind: string(entry.kind, `${path}.kind`),
    final_output: boolean(entry.final_output, `${path}.final_output`),
    judge: boolean(entry.judge, `${path}.judge`),
    ...(parsedAssignee ? { assignee: parsedAssignee } : {}),
    ...(execution ? { execution } : {}),
  };
}

function edge(value: unknown, path: string): CollaborationDefinitionGraphEdge {
  const entry = record(value, path);
  let loopRoute: CollaborationDefinitionGraphEdge['loop_route'];
  if (entry.loop_route !== undefined) {
    const raw = record(entry.loop_route, `${path}.loop_route`);
    const kind = string(raw.kind, `${path}.loop_route.kind`);
    if (!['continue', 'break', 'exhausted'].includes(kind)) {
      invalid(`${path}.loop_route.kind 不受支持`);
    }
    loopRoute = {
      kind: kind as 'continue' | 'break' | 'exhausted',
      logical_outcome: string(
        raw.logical_outcome,
        `${path}.loop_route.logical_outcome`,
      ),
    };
  }
  const displayName = optionalString(entry.display_name, `${path}.display_name`);
  return {
    source: string(entry.source, `${path}.source`),
    target: string(entry.target, `${path}.target`),
    outcome: string(entry.outcome, `${path}.outcome`),
    ...(displayName === undefined ? {} : { display_name: displayName }),
    ...(loopRoute ? { loop_route: loopRoute } : {}),
  };
}

function loop(value: unknown, path: string): CollaborationDefinitionLoop {
  const entry = record(value, path);
  return {
    display_name: string(entry.display_name, `${path}.display_name`),
    max_iterations: integer(entry.max_iterations, `${path}.max_iterations`, 1),
    entry_node_id: string(entry.entry_node_id, `${path}.entry_node_id`),
    result_node_id: string(entry.result_node_id, `${path}.result_node_id`),
    body_node_ids: strings(entry.body_node_ids, `${path}.body_node_ids`),
    continue_outcomes: strings(
      entry.continue_outcomes,
      `${path}.continue_outcomes`,
    ),
    break_outcomes: strings(entry.break_outcomes, `${path}.break_outcomes`),
    exhausted_outcome: string(
      entry.exhausted_outcome,
      `${path}.exhausted_outcome`,
    ),
    ...(entry.continue_display_name === undefined
      ? {}
      : {
          continue_display_name: string(
            entry.continue_display_name,
            `${path}.continue_display_name`,
          ),
        }),
  };
}

function graph(value: unknown): CollaborationDefinitionGraph {
  const entry = record(value, 'graph');
  if (!Array.isArray(entry.nodes) || !Array.isArray(entry.edges)) {
    invalid('graph.nodes 和 graph.edges 必须是数组');
  }
  let loops: Record<string, CollaborationDefinitionLoop> | undefined;
  if (entry.loops !== undefined) {
    loops = Object.fromEntries(
      Object.entries(record(entry.loops, 'graph.loops')).map(([id, value]) => [
        id,
        loop(value, `graph.loops.${id}`),
      ]),
    );
  }
  const executionGraphMode = optionalString(
    entry.execution_graph_mode,
    'graph.execution_graph_mode',
  );
  if (executionGraphMode !== undefined && executionGraphMode !== 'acyclic') {
    invalid('graph.execution_graph_mode 不受支持');
  }
  return {
    graph_mode: string(entry.graph_mode, 'graph.graph_mode'),
    nodes: entry.nodes.map((item, index) => node(item, `graph.nodes[${index}]`)),
    edges: entry.edges.map((item, index) => edge(item, `graph.edges[${index}]`)),
    ...(executionGraphMode ? { execution_graph_mode: 'acyclic' as const } : {}),
    ...(loops ? { loops } : {}),
  };
}

function unwrapEnvelope(value: unknown): unknown {
  if (
    value &&
    typeof value === 'object' &&
    !Array.isArray(value) &&
    ('code' in value || 'request_id' in value) &&
    'data' in value
  ) {
    return (value as Record<string, unknown>).data;
  }
  return value;
}

export function decodeCollaborationDefinitionValidationResult(
  value: unknown,
): CollaborationDefinitionValidationResult {
  const response = record(unwrapEnvelope(value), 'response');
  return {
    valid: boolean(response.valid, 'valid'),
    errors: diagnostics(response.errors, 'errors'),
    warnings: diagnostics(response.warnings, 'warnings'),
    summary: summary(response.summary),
    participants: participants(response.participants),
    ...(response.graph === undefined ? {} : { graph: graph(response.graph) }),
  };
}

function httpFailureMessage(status: number): string {
  if (status === 401) return '登录状态已失效，无法校验协作定义';
  if (status === 403) return '当前身份无权校验协作定义';
  return `校验服务请求失败（HTTP ${status}）`;
}

export async function validateCollaborationDefinition(
  baseUrl: string,
  definitionYaml: string,
  signal?: AbortSignal,
): Promise<CollaborationDefinitionValidationResult> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  const timeout = window.setTimeout(() => controller.abort(), VALIDATION_TIMEOUT_MS);
  try {
    const response = await fetch(collaborationApiUrl(baseUrl, 'definitions/validate'), {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ definition_yaml: definitionYaml }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new CollaborationDefinitionValidationError(
        httpFailureMessage(response.status),
      );
    }
    return decodeCollaborationDefinitionValidationResult(await response.json());
  } catch (error) {
    if (error instanceof CollaborationDefinitionValidationError) throw error;
    if (controller.signal.aborted) {
      if (signal?.aborted) throw error;
      throw new CollaborationDefinitionValidationError('校验请求超时，请重试');
    }
    throw new CollaborationDefinitionValidationError(
      error instanceof Error && error.message
        ? `校验请求失败：${error.message}`
        : '校验请求失败',
    );
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener('abort', abort);
  }
}
