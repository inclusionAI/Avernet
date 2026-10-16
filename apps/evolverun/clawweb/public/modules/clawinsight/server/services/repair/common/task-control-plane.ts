export type AgentControlPlaneEnvironment = "pre" | "prod";

const PRE_CONTROL_PLANE_HOSTS = new Set(["clawweb-pre.alipay.com", "clawweb-pre.antgroup-inc.cn"]);
const PROD_CONTROL_PLANE_HOSTS = new Set(["clawweb.alipay.com", "clawweb.antgroup-inc.cn"]);

/** Task ownership is independent of the target Bot environment. Unknown legacy tasks remain unclaimed. */
export function resolveTaskControlPlaneEnvironment(task: {
  controlPlaneEnvironment?: unknown; publicBaseUrl?: unknown;
}): AgentControlPlaneEnvironment | null {
  if (task.controlPlaneEnvironment === "pre" || task.controlPlaneEnvironment === "prod") return task.controlPlaneEnvironment;
  if (typeof task.publicBaseUrl === "string") {
    try {
      const hostname = new URL(task.publicBaseUrl).hostname.toLowerCase();
      if (PRE_CONTROL_PLANE_HOSTS.has(hostname)) return "pre";
      if (PROD_CONTROL_PLANE_HOSTS.has(hostname)) return "prod";
    } catch { /* A missing or local legacy URL does not establish deployment ownership. */ }
  }
  return null;
}
