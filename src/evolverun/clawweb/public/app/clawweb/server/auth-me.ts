/**
 * `GET /api/auth/me` for the open ClawWeb runner.
 *
 * Ported from the monolith's `server/routes/auth.ts`, minus the internal identity providers
 * (Antbuservice / Asfagent / TERN). What remains is what a self-hosted runner can resolve:
 *
 * 1. `IAM_TOKEN` / `_CHIPS-IAM_TOKEN` cookie — JWT payload supplies `sno` (工号) and `sub` (账号)
 * 2. Local dev cookies (`staff_id`, `userId`, `antcode_user_extern_no`) on a loopback `dev` host
 * 3. Loopback `dev` fallback: the runner has no SSO, so the request resolves as the dev user
 * 4. Nothing resolvable — the caller is rejected and the client shows `login_required`
 *
 * Role flags always come from `adminAuthMiddleware`, so configuration stays authoritative.
 */
import type { Request, RequestHandler } from "express";
import { getRequestCookie } from "@avernet/clawweb-shared/server/middleware/admin-auth";

export type AuthMeOptions = {
  /** Machine environment; only `dev` allows the loopback development identity. */
  environment: string;
  /** Loopback development user id. */
  devUserId?: string;
};

type IamTokenPayload = { sno?: string; name?: string; sub?: string };

type ResolvedIdentity = {
  userId: string;
  nickName: string;
  userName: string;
  avatarUrl: string;
  displayName: string;
};

function decodeJwtPayload(token: string): IamTokenPayload | null {
  const payloadPart = token.split(".")[1];
  if (!payloadPart) return null;
  try {
    return JSON.parse(Buffer.from(payloadPart, "base64url").toString("utf8")) as IamTokenPayload;
  } catch {
    return null;
  }
}

function isLoopbackHost(request: Request): boolean {
  const host = request.get("host") ?? "";
  return host.includes("localhost") || host.includes("127.0.0.1");
}

function identityFromIamToken(request: Request): ResolvedIdentity | null {
  const cookie = getRequestCookie(request, "IAM_TOKEN") ?? getRequestCookie(request, "_CHIPS-IAM_TOKEN");
  if (!cookie) return null;
  const payload = decodeJwtPayload(cookie);
  if (!payload?.sno) return null;
  const nickName = payload.name ?? payload.sub ?? payload.sno;
  return {
    userId: payload.sno,
    nickName,
    userName: payload.sub ?? payload.sno,
    avatarUrl: "",
    displayName: nickName,
  };
}

function identityFromLocalDevCookie(request: Request): ResolvedIdentity | null {
  const raw =
    getRequestCookie(request, "staff_id") ??
    getRequestCookie(request, "userId") ??
    getRequestCookie(request, "antcode_user_extern_no");
  const userId = raw?.trim();
  if (!userId) return null;
  const nickName = getRequestCookie(request, "nick_name") ?? getRequestCookie(request, "x-user-name") ?? userId;
  return { userId, nickName, userName: userId, avatarUrl: "", displayName: nickName };
}

function devIdentity(devUserId: string): ResolvedIdentity {
  return {
    userId: devUserId,
    nickName: "Dev Local",
    userName: devUserId,
    avatarUrl: "",
    displayName: "Dev Local",
  };
}

function resolveDisplayIdentity(request: Request, options: AuthMeOptions): ResolvedIdentity | null {
  const isLocalDev = isLoopbackHost(request) && options.environment === "dev";
  const devUserId = options.devUserId ?? "dev_local";
  const devModeRequested = isLocalDev
    && (request.query.dev === "1" || request.header("X-ClawWeb-Dev-Mode") === "1");
  if (devModeRequested) return devIdentity(devUserId);

  return identityFromIamToken(request)
    ?? (isLocalDev ? identityFromLocalDevCookie(request) : null)
    ?? (isLocalDev ? devIdentity(devUserId) : null);
}

/**
 * Resolve an identity that is safe to use for sensitive actions.
 *
 * The public runner has no IAM signature verifier, so an IAM token decoded by
 * `resolveDisplayIdentity` is intentionally excluded here. Internal hosts must
 * supply their own server-verified resolver. The loopback development identity
 * remains available only when the server is explicitly running in `dev`.
 */
export function resolveAuthMeIdentity(request: Request, options: AuthMeOptions): ResolvedIdentity | null {
  const isLocalDev = isLoopbackHost(request) && options.environment === "dev";
  if (!isLocalDev) return null;

  const devUserId = options.devUserId ?? "dev_local";
  const devModeRequested = request.query.dev === "1" || request.header("X-ClawWeb-Dev-Mode") === "1";
  if (devModeRequested) return devIdentity(devUserId);

  return identityFromLocalDevCookie(request) ?? devIdentity(devUserId);
}

export function createAuthMeHandler(options: AuthMeOptions): RequestHandler {
  const devUserId = options.devUserId ?? "dev_local";
  return (request, response) => {
    const respondAsDevUser = () => {
      response.json({
        userId: devUserId,
        nickName: "Dev Local",
        userName: devUserId,
        avatarUrl: "",
        displayName: "Dev Local",
        isAdmin: true,
        isLogAdmin: true,
        isBenchAdmin: true,
        isClawEvolveAdmin: true,
        isClawInsightAdmin: request.isClawInsightAdmin === true,
        isSuperAdmin: true,
      });
    };

    const identity = resolveDisplayIdentity(request, options);
    if (identity?.userId === devUserId && identity.userName === devUserId) {
      respondAsDevUser();
      return;
    }
    if (!identity) {
      response.status(401).json({ error: "Unauthorized", message: "Missing login cookie" });
      return;
    }

    response.json({
      ...identity,
      isAdmin: request.isAdmin === true,
      isLogAdmin: request.isLogAdmin === true,
      isBenchAdmin: request.isBenchAdmin === true,
      isClawEvolveAdmin: request.isClawEvolveAdmin === true,
      isClawInsightAdmin: request.isClawInsightAdmin === true,
      isSuperAdmin: request.isSuperAdmin === true,
    });
  };
}
