package a2a.agent.client.internal;

import java.util.Set;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;

import a2a.agent.client.CallerContext;
import a2a.agent.client.ClientOptions;

/**
 * 一个客户端内所有调用共享的运行时：参数、限流、调用上下文提供者、定时器。
 *
 * <p>定时器只有一个守护线程，用于流的整体时限（HTTP 的请求超时只约束响应头）；不为每次调用创建线程。
 */
public final class ClientRuntime implements AutoCloseable {

    private final ClientOptions options;
    private final CallLimiter limiter;
    private final CallerContext.Provider callerProvider;
    private final ScheduledExecutorService timer;

    public ClientRuntime(ClientOptions options, CallerContext.Provider callerProvider) {
        this.options = options;
        this.limiter = new CallLimiter(options.maxInFlightCalls());
        this.callerProvider = callerProvider;
        this.timer = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "a2a-agent-client-timer");
            t.setDaemon(true);
            return t;
        });
    }

    public ClientOptions options() {
        return options;
    }

    public CallLimiter limiter() {
        return limiter;
    }

    public ScheduledExecutorService timer() {
        return timer;
    }

    /** 当前调用的调用上下文；没有配置提供者时为 null。 */
    public CallerContext caller() {
        return callerProvider == null ? null : callerProvider.current();
    }

    /** 本客户端支持的扩展：Card 中 required 的扩展必须都在这里。 */
    public Set<String> supportedExtensions() {
        return Set.of(PayloadKeys.TOOLCALL_EXTENSION);
    }

    @Override
    public void close() {
        timer.shutdownNow();
    }
}
