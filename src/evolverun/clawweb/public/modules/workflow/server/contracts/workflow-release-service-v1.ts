/** Service API contract for ClawMind and the workflow release service. */
export const WORKFLOW_RELEASE_SERVICE_V1 = {
  version: "v1",
  reservePath: "/releases/v1/reserve",
  completePath: "/releases/v1/complete",
} as const;

export type WorkflowReleaseReserveRequestV1 = {
  serviceApiVersion: typeof WORKFLOW_RELEASE_SERVICE_V1.version;
  workflowId: string;
  packId: string;
  snapshotCommit: string;
  specJson: string;
  minDeployNumber: number;
  note?: string;
  botId?: string;
  ownerId?: string;
};

export type WorkflowReleaseReservationV1 = {
  serviceApiVersion: typeof WORKFLOW_RELEASE_SERVICE_V1.version;
  workflowId: string;
  packId: string;
  snapshotCommit: string;
  version: number;
  deployNumber: number;
  tagName: string;
  completed: boolean;
};

type ObjectRecord = Record<string, unknown>;
const MAX_INT = 2_147_483_647;
const isObject = (value: unknown): value is ObjectRecord => value !== null && typeof value === "object";
const isWorkflowId = (value: unknown): value is string => typeof value === "string" && /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}$/.test(value);
const isCommit = (value: unknown): value is string => typeof value === "string" && /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(value);
const isPositiveInt = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 1 && value <= MAX_INT;
const optionalText = (value: unknown): value is string | undefined => value === undefined || typeof value === "string";

export function parseWorkflowReleaseReserveRequestV1(value: unknown): WorkflowReleaseReserveRequestV1 | undefined {
  if (!isObject(value) || value.serviceApiVersion !== WORKFLOW_RELEASE_SERVICE_V1.version || !isWorkflowId(value.workflowId)
    || typeof value.packId !== "string" || !value.packId || value.packId.length > 255 || !isCommit(value.snapshotCommit)
    || typeof value.specJson !== "string" || !isPositiveInt(value.minDeployNumber)
    || !optionalText(value.note) || !optionalText(value.botId) || !optionalText(value.ownerId)) return undefined;
  return value as WorkflowReleaseReserveRequestV1;
}

export function parseWorkflowReleaseReservationV1(value: unknown): WorkflowReleaseReservationV1 | undefined {
  if (!isObject(value) || value.serviceApiVersion !== WORKFLOW_RELEASE_SERVICE_V1.version || !isWorkflowId(value.workflowId)
    || typeof value.packId !== "string" || !value.packId || value.packId.length > 255 || !isCommit(value.snapshotCommit)
    || !isPositiveInt(value.version) || !isPositiveInt(value.deployNumber)
    || value.tagName !== `deploy/${value.workflowId}/#${value.deployNumber}` || typeof value.completed !== "boolean") return undefined;
  return value as WorkflowReleaseReservationV1;
}
