package bcn.a2a.server;

import java.time.Duration;

/**
 * SDK 的运行参数。都有默认值，业务一般不需要设置。默认值待按示例 Agent 压测结果调整。
 *
 * @param maxRequestBytes     单个请求编码后的大小上限。还应同时配置 gRPC 服务端的 maxInboundMessageSize，
 *                            让超大请求在反序列化之前就被拦下；这里只是兜底
 * @param maxConcurrentCalls  同时处理的调用数上限，超出时返回 UNAVAILABLE（BUSY），保护业务线程池
 * @param unaryTimeout        SendMessage / GetTask / CancelTask 的处理时限
 * @param streamTimeout       一条流的最长持续时间
 * @param streamBufferEvents  流式输出的发送缓冲（事件数）。调用方读得慢时，写满后阻塞业务线程，直到可写或超时
 */
public record A2aServiceOptions(int maxRequestBytes, int maxConcurrentCalls, Duration unaryTimeout,
                                Duration streamTimeout, int streamBufferEvents) {

    public static A2aServiceOptions defaults() {
        return new A2aServiceOptions(4 * 1024 * 1024, 64, Duration.ofSeconds(60), Duration.ofMinutes(10), 256);
    }

    public A2aServiceOptions {
        if (maxRequestBytes <= 0 || maxConcurrentCalls <= 0 || streamBufferEvents <= 0) {
            throw new IllegalArgumentException("limits must be positive");
        }
        if (unaryTimeout == null || unaryTimeout.isNegative() || unaryTimeout.isZero()
                || streamTimeout == null || streamTimeout.isNegative() || streamTimeout.isZero()) {
            throw new IllegalArgumentException("timeouts must be positive");
        }
    }
}
