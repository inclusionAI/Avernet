type FlowRecord = Record<string, unknown>;

function hasValue(value: unknown): boolean {
  return value !== undefined && value !== null;
}

function readFirst(flow: FlowRecord, camelKey: string, snakeKey: string): unknown {
  const camelValue = flow[camelKey];
  if (hasValue(camelValue)) return camelValue;
  return flow[snakeKey];
}

export function readFlowId(flow: FlowRecord): string {
  const value = readFirst(flow, "flowId", "flow_id");
  if (typeof value === "string" && value.length > 0) return value;
  throw new Error("TaskFlow record missing flow id");
}

export function readStateJson(flow: FlowRecord): unknown {
  const value = readFirst(flow, "stateJson", "state_json");
  if (hasValue(value)) return value;
  throw new Error("TaskFlow record missing state JSON");
}

export function readWaitJson(flow: FlowRecord): unknown {
  return readFirst(flow, "waitJson", "wait_json");
}

export function readCurrentStep(flow: FlowRecord): string | undefined {
  const value = readFirst(flow, "currentStep", "current_step");
  return typeof value === "string" ? value : undefined;
}

export function parseJsonValue(value: unknown): unknown {
  if (value === null || value === undefined) return null;
  if (typeof value === "string") return JSON.parse(value);
  if (typeof value === "object") return value;
  return value;
}

export function parseRawFlowState(flow: FlowRecord): unknown {
  return parseJsonValue(readStateJson(flow));
}

export function parseWaitJson(flow: FlowRecord): Record<string, unknown> | null {
  const parsed = parseJsonValue(readWaitJson(flow));
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  return parsed as Record<string, unknown>;
}
