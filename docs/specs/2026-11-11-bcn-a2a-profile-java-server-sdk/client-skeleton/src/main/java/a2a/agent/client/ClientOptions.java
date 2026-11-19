package a2a.agent.client;

import java.time.Duration;
import java.util.concurrent.Executor;

/**
 * 客户端运行参数。默认值待按压测结果调整。
 *
 * @param callTimeout      一元调用（发送消息、查询、取消）的默认时限
 * @param streamTimeout    一条流的最长持续时间；到期取消并以 TIMEOUT 结束
 * @param maxInFlightCalls 同时进行中的调用上限（含流），超过时直接以 BUSY 失败，保护调用方自身；0 表示不限制
 * @param callbackExecutor 流式回调的执行线程池；null 表示直接在网络 IO 线程回调（回调必须很快）
 * @param requestToolCalls 默认是否请求工具调用记录（toolcall 扩展），可按次覆盖
 * @param cardTtl          从 URL 拉取的 Card 的缓存时间；过期后先返回旧值，后台刷新
 */
public record ClientOptions(Duration callTimeout, Duration streamTimeout, int maxInFlightCalls,
                            Executor callbackExecutor, boolean requestToolCalls, Duration cardTtl) {

    public static ClientOptions defaults() {
        return new ClientOptions(Duration.ofSeconds(60), Duration.ofMinutes(10), 0, null, true, Duration.ofMinutes(5));
    }

    public ClientOptions withCallTimeout(Duration v) {
        return new ClientOptions(v, streamTimeout, maxInFlightCalls, callbackExecutor, requestToolCalls, cardTtl);
    }

    public ClientOptions withStreamTimeout(Duration v) {
        return new ClientOptions(callTimeout, v, maxInFlightCalls, callbackExecutor, requestToolCalls, cardTtl);
    }

    public ClientOptions withMaxInFlightCalls(int v) {
        return new ClientOptions(callTimeout, streamTimeout, v, callbackExecutor, requestToolCalls, cardTtl);
    }

    public ClientOptions withCallbackExecutor(Executor v) {
        return new ClientOptions(callTimeout, streamTimeout, maxInFlightCalls, v, requestToolCalls, cardTtl);
    }

    public ClientOptions withRequestToolCalls(boolean v) {
        return new ClientOptions(callTimeout, streamTimeout, maxInFlightCalls, callbackExecutor, v, cardTtl);
    }

    public ClientOptions withCardTtl(Duration v) {
        return new ClientOptions(callTimeout, streamTimeout, maxInFlightCalls, callbackExecutor, requestToolCalls, v);
    }
}
