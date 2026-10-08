import type { Request } from "express";

export type VerifiedRequestActor = {
  userId: string;
  source: "request" | "local_dev";
};

function isLoopbackHost(host: string): boolean {
  const normalized = host.trim().toLowerCase();
  const hostname = normalized.startsWith("[")
    ? normalized.slice(1, normalized.indexOf("]"))
    : normalized.split(":", 1)[0];
  return hostname === "localhost" || hostname === "127.0.0.1" || hostname === "::1";
}

/** Keep Repair identity resolution aligned with the other ClawWeb browser APIs. */
export async function resolveRepairRequestActor(req: Request): Promise<VerifiedRequestActor | null> {
  const cookies = req.cookies as Record<string, string> | undefined;
  const userId = [
    req.header("X-Staff-Id"),
    req.header("staff_id"),
    req.header("X-User-Id"),
    cookies?.staff_id,
  ].map((value) => value?.trim()).find(Boolean);
  if (userId) return { userId, source: "request" };
  return isLoopbackHost(req.get("host") ?? "")
    ? { userId: "dev_local", source: "local_dev" }
    : null;
}
