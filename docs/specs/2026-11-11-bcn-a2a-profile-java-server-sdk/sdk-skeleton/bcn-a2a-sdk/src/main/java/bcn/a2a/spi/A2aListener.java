package bcn.a2a.spi;

/**
 * 调用监听：业务把日志、指标、告警接到自己的系统里。SDK 不依赖任何具体的指标库。
 *
 * <p>约定：在处理调用的线程上同步回调，必须快速返回、不能阻塞；抛出的异常会被 SDK 吞掉并记日志，不影响调用。
 */
public interface A2aListener {

    A2aListener NOOP = new A2aListener() { };

    /** 请求通过解析和分发之前回调（此时 agentId 可能还未校验）。 */
    default void onStart(CallInfo info) {
    }

    /**
     * 调用结束时回调，每个调用恰好一次。
     *
     * @param error 失败时的原始异常，成功时为 null；只用于日志，不要把内容返回给调用方
     */
    default void onEnd(CallInfo info, Outcome outcome, Throwable error) {
    }

    /**
     * @param method     A2A 方法名，例如 SendMessage
     * @param agentId    请求中的 tenant
     * @param requestId  SDK 生成的请求 ID，用于串联日志
     * @param startNanos {@link System#nanoTime()} 起点，用于计算耗时
     */
    record CallInfo(String method, String agentId, String requestId, long startNanos) {
    }

    enum Outcome {
        /** 正常完成（包括流式输出以 COMPLETED 结束）。 */
        OK,
        /** 请求被拒绝：协议校验失败、业务抛 BcnException、Task 不存在等。 */
        REJECTED,
        /** 执行失败：业务异常、流以 FAILED 结束、SDK 内部错误。 */
        FAILED,
        /** 调用方取消。 */
        CANCELLED,
        /** 超过截止时间。 */
        TIMEOUT,
        /** 并发已满或线程池拒绝。 */
        BUSY
    }
}
