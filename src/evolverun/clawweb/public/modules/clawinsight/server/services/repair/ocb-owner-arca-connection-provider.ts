import type { RepairTargetEnvironment } from "./contracts.js";
import { RepairError, repairValidation } from "./errors.js";
import { ARCA_TERMINAL_PORT, validateArcaProxyConnection, type ArcaConnectionProvider } from "./arca-command-transport.js";

export type ArcaOwnerIdentity = { cookie: string; userId: string };
export type OcbOwnerArcaConnectionOptions = {
  baseUrls: Partial<Record<RepairTargetEnvironment, string>>;
  getIdentity: () => ArcaOwnerIdentity;
  timeoutMs: number;
};

/** Local control-plane adapter. Owner credentials never enter tasks or AIS inputs. */
export class OcbOwnerArcaConnectionProvider implements ArcaConnectionProvider {
  private readonly baseUrls: Partial<Record<RepairTargetEnvironment, URL>>;
  constructor(private readonly options: OcbOwnerArcaConnectionOptions) {
    this.baseUrls = Object.fromEntries(Object.entries(options.baseUrls).map(([environment, value]) => {
      const url = new URL(value);
      if (url.protocol !== "https:" || url.username || url.password || url.pathname !== "/" || url.search || url.hash) {
        throw new Error("OCB connection endpoint must be a credential-free HTTPS origin");
      }
      return [environment, url];
    }));
    if (!Number.isSafeInteger(options.timeoutMs) || options.timeoutMs < 1) throw new Error("Invalid OCB connection timeout");
  }

  async getConnection(input: Parameters<ArcaConnectionProvider["getConnection"]>[0]) {
    if (!/^[1-9][0-9]*$/u.test(input.bindingId)) repairValidation("invalid_arca_binding", "ARCA binding ID 不合法");
    if (!Number.isSafeInteger(input.ttlSeconds) || input.ttlSeconds < 30 || input.ttlSeconds > 600) {
      repairValidation("invalid_arca_connection_ttl", "ARCA 连接凭据 TTL 必须在 30 到 600 秒之间");
    }
    const baseUrl = this.baseUrls[input.environment];
    if (!baseUrl) throw new RepairError(503, "repair_ocb_not_configured", "目标环境未配置 OCB connection 接口");
    const identity = this.options.getIdentity();
    if (!identity.cookie?.trim() || !identity.userId?.trim()
      || /[\r\n\0]/u.test(identity.cookie + identity.userId)) {
      throw new RepairError(401, "repair_ocb_identity_required", "本地 ARCA 连接需要有效的 Owner 登录身份");
    }
    const url = new URL(`/api/v1/devices/${input.bindingId}/connection`, baseUrl);
    url.searchParams.set("port", String(ARCA_TERMINAL_PORT));
    url.searchParams.set("ttl", String(input.ttlSeconds));
    try {
      const response = await fetch(url, {
        method: "GET", redirect: "manual",
        headers: { Accept: "application/json", Cookie: identity.cookie, "x-user-id": identity.userId },
        signal: AbortSignal.timeout(this.options.timeoutMs),
      });
      if (response.status >= 300 && response.status < 400 || response.status === 401 || response.status === 403) {
        throw new RepairError(401, "repair_ocb_identity_rejected", "OCB 拒绝本地登录身份，请刷新登录后重试");
      }
      // Do not include upstream bodies or login URLs in errors: they can contain credentials.
      if (!response.ok) throw new RepairError(502, "repair_ocb_connection_unavailable", "OCB 获取连接失败");
      const reader = response.body?.getReader();
      const chunks: Uint8Array[] = [];
      let size = 0;
      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 32 * 1024) {
            await reader.cancel();
            throw new RepairError(502, "repair_ocb_connection_invalid", "OCB 连接响应过大");
          }
          chunks.push(value);
        }
      }
      const body = JSON.parse(Buffer.concat(chunks).toString("utf8"));
      if (body?.buserviceErrorCode === "USER_NOT_LOGIN") {
        throw new RepairError(401, "repair_ocb_identity_rejected", "OCB 拒绝本地登录身份，请刷新登录后重试");
      }
      if (body?.success !== true || body.data?.available !== true) {
        throw new RepairError(502, "repair_ocb_connection_unavailable", "OCB 未返回可用连接，请检查登录权限和目标状态");
      }
      if (body.data.engine_type !== "openclaw") {
        throw new RepairError(409, "repair_arca_target_mismatch", "目标 Engine 已变化，请重新解析目标");
      }
      const connection = validateArcaProxyConnection(body.data, input.sandboxId);
      if (input.arcaInstanceId && connection.target !== `ARCA_${input.arcaInstanceId}:${ARCA_TERMINAL_PORT}`) {
        throw new RepairError(409, "repair_arca_target_mismatch", "OCB 返回实例与冻结目标不一致，请重新解析目标");
      }
      return { ...connection, localOwnerIdentity: { cookie: identity.cookie, userId: identity.userId } };
    } catch (error) {
      if (error instanceof RepairError) throw error;
      throw new RepairError(502, "repair_ocb_connection_failed", "OCB 连接请求失败或响应无效，未执行容器命令");
    }
  }
}
