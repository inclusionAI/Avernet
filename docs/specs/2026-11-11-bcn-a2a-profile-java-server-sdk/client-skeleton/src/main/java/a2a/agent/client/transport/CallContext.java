package a2a.agent.client.transport;

import java.time.Duration;
import java.util.Arrays;
import java.util.Set;
import java.util.function.Consumer;
import java.util.stream.Collectors;

/**
 * 一次调用的传输参数。
 *
 * @param timeout               调用时限；gRPC 设为 deadline 并传给服务端，HTTP 设为请求超时
 * @param extensionsHeader      请求头 A2A-Extensions 的值（逗号分隔的 URI），没有请求任何扩展时为 null
 * @param onActivatedExtensions 收到响应头时回调服务端确认激活的扩展，可为 null。
 *                              服务端回写是 SHOULD，可能收不到，上层不能只依赖它
 */
public record CallContext(Duration timeout, String extensionsHeader, Consumer<Set<String>> onActivatedExtensions) {

    /** A2A 线协议版本，所有请求都带 A2A-Version 头。 */
    public static final String PROTOCOL_VERSION = "1.0";

    public static CallContext of(Duration timeout) {
        return new CallContext(timeout, null, null);
    }

    /** 解析响应头 A2A-Extensions 的值；供各传输复用。 */
    public static Set<String> parseExtensions(String headerValue) {
        if (headerValue == null || headerValue.isBlank()) {
            return Set.of();
        }
        return Arrays.stream(headerValue.split(",")).map(String::trim).filter(s -> !s.isEmpty())
                .collect(Collectors.toUnmodifiableSet());
    }

    /** 通知上层激活结果（有回调时）。 */
    public void notifyActivated(String headerValue) {
        if (onActivatedExtensions != null) {
            onActivatedExtensions.accept(parseExtensions(headerValue));
        }
    }
}
