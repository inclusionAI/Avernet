package a2a.agent.client;

/**
 * 流式调用的回调。所有方法都有空的默认实现，按需覆盖。
 *
 * <p><b>线程</b>：默认在网络 IO 线程上回调，必须快速返回、不能阻塞；需要做耗时处理时，
 * 在 {@link ClientOptions#callbackExecutor()} 中配置业务线程池，SDK 会把回调转到那里执行（仍然保证顺序）。
 * 一条流同一时刻只有一个回调在执行，处理完上一个才会读取下一个事件（背压）。
 *
 * <p>回调中抛出异常会取消这条流，并以 {@link A2aCallException.Kind#CANCELLED} 结束。
 */
public interface StreamListener {

    /** 新增的正文文本，已按 append / lastChunk 规则处理，只包含新的部分。 */
    default void onText(String delta) {
    }

    /** 一条工具调用记录。只有本次调用请求了 toolcall 扩展、且对方支持时才会收到。 */
    default void onTool(ToolRecord record) {
    }

    /** 状态变化或过程说明（非工具记录的状态消息）。text 可能为 null。 */
    default void onStatus(Reply.State state, String text) {
    }

    /** 收到终态（或形态 A 的那条 Message）后回调一次。 */
    default void onComplete(Reply reply) {
    }

    /** 失败时回调一次。与 onComplete 二者只会有一个。 */
    default void onError(A2aCallException error) {
    }
}
