import type { RequestIdentity } from "../../contracts/request-identity.js";
import type { AccessibleSpace, SpaceDirectory } from "../../contracts/space-directory.js";
import type { ErrorRequestHandler } from "express";

const localSpaceError = (status: number, message: string) => Object.assign(new Error(message), {
  status, code: "EVOLVE_SPACE_ACCESS_REJECTED",
});

/** Preserve safe domain errors at the module boundary, before a host's generic 500 handler. */
export const spaceAccessErrorHandler: ErrorRequestHandler = (error, _request, response, next) => {
  const status = error?.status ?? error?.statusCode;
  const codes = new Set(["EVOLVE_SPACE_ACCESS_REJECTED", "HOST_SPACE_UNAVAILABLE", "HOST_SPACE_REQUEST_FAILED",
    "HOST_SPACE_INVALID_RESPONSE", "HOST_PERSONAL_SPACE_NOT_INITIALIZED"]);
  if (!codes.has(error?.code) || !Number.isInteger(status) || status < 400 || status > 599
    || typeof error?.message !== "string") { next(error); return; }
  response.status(status).json({ code: error.code, error: error.message });
};

export type SpaceOwnedRecord = {
  owner_user_id: string;
  space_id?: string | null;
  space_type?: "PERSONAL" | "TEAM" | null;
};

/** Unassigned records remain owner-only, including historical private records. */
export function canReadSpaceRecord(record: SpaceOwnedRecord, userId: string, spaces: readonly AccessibleSpace[]): boolean {
  if (!record.space_id) return record.owner_user_id === userId;
  const space = spaces.find((item) => item.id === record.space_id && item.type === record.space_type);
  return !!space && (space.type === "TEAM" || record.owner_user_id === userId);
}

export async function registrationSpace(
  port: SpaceDirectory | undefined, identity: RequestIdentity, requested: unknown,
): Promise<AccessibleSpace | null> {
  if (requested != null && typeof requested !== "string" && typeof requested !== "number") {
    throw localSpaceError(400, "空间 ID 不合法");
  }
  const id = requested == null ? "" : String(requested).trim();
  // No space is an explicit owner-only choice, independent of the directory.
  if (!id) return null;
  if (!port) throw localSpaceError(503, "空间服务未配置");
  const spaces = await port.listAccessibleSpaces({ identity });
  const space = spaces.find((item) => item.id === id);
  if (!space) throw localSpaceError(403, "无权访问所选空间");
  return space;
}

export function spaceColumns(space: AccessibleSpace | null) {
  return { spaceId: space?.id ?? null, spaceType: space?.type ?? null, spaceName: space?.name ?? null };
}
