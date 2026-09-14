import type { OcbRequestIdentity, OcbSpace, OcbSpacePort } from "../../internal/module-api.js";
import type { ErrorRequestHandler } from "express";

const localSpaceError = (status: number, message: string) => Object.assign(new Error(message), {
  status, code: "EVOLVE_SPACE_ACCESS_REJECTED",
});

/** Preserve safe domain errors at the module boundary, before a host's generic 500 handler. */
export const spaceAccessErrorHandler: ErrorRequestHandler = (error, _request, response, next) => {
  const status = error?.status ?? error?.statusCode;
  const codes = new Set(["EVOLVE_SPACE_ACCESS_REJECTED", "OCB_SPACE_UNAVAILABLE", "OCB_SPACE_REQUEST_FAILED",
    "OCB_SPACE_INVALID_RESPONSE", "OCB_PERSONAL_SPACE_NOT_INITIALIZED"]);
  if (!codes.has(error?.code) || !Number.isInteger(status) || status < 400 || status > 599
    || typeof error?.message !== "string") { next(error); return; }
  response.status(status).json({ code: error.code, error: error.message });
};

export type SpaceOwnedRecord = {
  owner_user_id: string;
  space_id?: string | null;
  space_type?: "PERSONAL" | "TEAM" | null;
};

/** A missing space is a historical private record, never a shared scope. */
export function canReadSpaceRecord(record: SpaceOwnedRecord, userId: string, spaces: readonly OcbSpace[]): boolean {
  if (!record.space_id) return record.owner_user_id === userId;
  const space = spaces.find((item) => item.id === record.space_id && item.type === record.space_type);
  return !!space && (space.type === "TEAM" || record.owner_user_id === userId);
}

export async function registrationSpace(
  port: OcbSpacePort | undefined, identity: OcbRequestIdentity, requested: unknown,
): Promise<OcbSpace | null> {
  // Standalone deployments without OCB retain their existing private-only mode.
  // An explicit space must never silently fall back to private on such deployments.
  if (!port) {
    if (requested != null && requested !== "") throw localSpaceError(503, "OCB 空间服务未配置");
    return null;
  }
  if (requested != null && typeof requested !== "string" && typeof requested !== "number") {
    throw localSpaceError(400, "空间 ID 不合法");
  }
  const id = requested == null ? "" : String(requested).trim();
  const spaces = await port.listAccessibleSpaces({ identity });
  const space = id ? spaces.find((item) => item.id === id) : spaces.find((item) => item.type === "PERSONAL");
  if (!space) throw localSpaceError(id ? 403 : 409, id ? "无权访问所选空间" : "请先在 OCB 初始化个人空间");
  return space;
}

export function spaceColumns(space: OcbSpace | null) {
  return { spaceId: space?.id ?? null, spaceType: space?.type ?? null, spaceName: space?.name ?? null };
}
