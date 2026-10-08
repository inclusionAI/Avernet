import type { WorkflowRuntimeUser, WorkflowSpec } from "../types.js";

type ResolveRuntimeUserParams = {
  deliveryContext?: Record<string, unknown>;
  workflowDefaults?: WorkflowSpec["defaults"];
  env?: Record<string, string | undefined>;
};

function stringField(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() !== "" ? value : undefined;
}

function userFrom(value: unknown): { id?: string; name?: string } | undefined {
  if (!value || typeof value !== "object" || Array.isArray(value)) return undefined;
  const raw = value as Record<string, unknown>;
  const id = stringField(raw.id);
  const name = stringField(raw.name);
  return id || name ? { id, name } : undefined;
}

function withSource(
  user: { id?: string; name?: string } | undefined,
  source: WorkflowRuntimeUser["source"],
): WorkflowRuntimeUser | undefined {
  if (!user || (!user.id && !user.name)) return undefined;
  return { ...user, source };
}

export function resolveRuntimeUserContext(params: ResolveRuntimeUserParams): WorkflowRuntimeUser | undefined {
  const env = params.env ?? process.env;
  const defaultUser = params.workflowDefaults?.user;

  if (defaultUser?.source === "fixed") {
    return withSource({ id: defaultUser.id, name: defaultUser.name }, "workflow-default");
  }

  const deliveryUser = userFrom(params.deliveryContext?.user);
  if (deliveryUser) return withSource(deliveryUser, "delivery-context");

  const genericEnvUser = {
    id: stringField(env.WORKFLOW_ENGINE_USER_ID),
    name: stringField(env.WORKFLOW_ENGINE_USER_NAME),
  };
  if (genericEnvUser.id || genericEnvUser.name) return withSource(genericEnvUser, "env");

  if (defaultUser) return withSource({ id: defaultUser.id, name: defaultUser.name }, "workflow-default");

  return undefined;
}
