package a2a.agent.client;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 调用上下文：放在请求 metadata 的平台字段 {@code bcn_caller} 中（Payload 规范 7.3）。
 *
 * <p><b>必须由接入方根据自己已认证的上下文生成</b>（例如当前登录用户），不能取自用户输入。
 * 对方 Agent 不验签，只把它当作“来自内网可信应用”的自述信息。
 *
 * @param userId         业务用户 ID，可为 null
 * @param sourcePlatform 调用来源平台，可为 null
 * @param extra          其他平台字段，可为空
 */
public record CallerContext(String userId, String sourcePlatform, Map<String, Object> extra) {

    public static CallerContext ofUser(String userId) {
        return new CallerContext(userId, null, Map.of());
    }

    /** 转成 {@code bcn_caller} 的值：{@code {"userId": ..., "source": {"platform": ...}, ...}}。 */
    public Map<String, Object> toPayload() {
        Map<String, Object> payload = new LinkedHashMap<>();
        if (extra != null) {
            payload.putAll(extra);
        }
        if (userId != null) {
            payload.put("userId", userId);
        }
        if (sourcePlatform != null) {
            payload.put("source", Map.of("platform", sourcePlatform));
        }
        return payload;
    }

    /** 每次调用时由客户端调用，返回当前的调用上下文；返回 null 表示这次不带。必须快速、线程安全。 */
    @FunctionalInterface
    public interface Provider {
        CallerContext current();
    }
}
