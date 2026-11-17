package bcn.a2a.spi;

import java.util.Map;

/**
 * 调用上下文：请求级 metadata 中的固定字段 {@code bcn_caller}。不是 URI 扩展，不需要协商，请求里带了就解析。
 *
 * <p>由 BCS（或直连时的调用方 Agent）从自己已认证的上下文生成；Agent 不验签，
 * 可信度等同于“本次调用来自公司内网中的可信应用”（D1、P-100、P-101）。
 *
 * @param userId         业务用户 ID，可能为 null（例如系统触发的调用）
 * @param sourcePlatform 调用来源平台，例如 {@code bcn}，可能为 null
 * @param attributes     原始字段，原样保留，只读
 */
public record CallerContext(String userId, String sourcePlatform, Map<String, Object> attributes) {
}
