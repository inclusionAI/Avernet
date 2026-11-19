package a2a.agent.client;

/**
 * 调用失败。所有对外方法只抛这一种异常（同步方法直接抛出，异步方法以它完成 Future）。
 *
 * <p>最重要的是 {@link #kind()}：调用方据此判断能不能重试。
 * <b>发送消息不会自动重试</b>：服务端没有提交去重，结果未知时重发可能导致执行两次。
 */
public class A2aCallException extends RuntimeException {

    public enum Kind {
        /** 请求被拒绝，确定没有执行：参数不合法、Task 不存在、操作不支持、业务拒绝等。改了请求可以重发。 */
        REJECTED,
        /** 对方或本客户端繁忙，确定没有执行，可以稍后重试。 */
        BUSY,
        /** 超时。结果未知，可能已经执行。 */
        TIMEOUT,
        /** 结果未知：连接中断、服务端内部错误、流在终态之前结束等。可能已经执行。 */
        UNKNOWN_OUTCOME,
        /** 对方的响应不符合 Payload 规范（例如流的首帧不是 Task）。 */
        PROTOCOL_VIOLATION,
        /** 调用方主动取消。 */
        CANCELLED,
        /** AgentCard 不可用：格式错误、没有可用接口、要求了本客户端不支持的扩展等。确定没有执行。 */
        CARD_INVALID
    }

    private final Kind kind;
    private final String reason;
    private final String code;

    public A2aCallException(Kind kind, String reason, String code, String message, Throwable cause) {
        super(message, cause);
        this.kind = kind;
        this.reason = reason;
        this.code = code;
    }

    public Kind kind() {
        return kind;
    }

    /** 错误原因，例如 TASK_NOT_FOUND、INVALID_INPUT、CLIENT_BUSY；没有时为 null。 */
    public String reason() {
        return reason;
    }

    /** 原始错误码：gRPC 状态名 / HTTP 状态码 / JSON-RPC 错误码；没有时为 null。 */
    public String code() {
        return code;
    }

    /** 确定没有执行（可以放心修改后重发或稍后重试）。 */
    public boolean definitelyNotExecuted() {
        return kind == Kind.REJECTED || kind == Kind.BUSY || kind == Kind.CARD_INVALID;
    }

    @Override
    public String toString() {
        return "A2aCallException{" + kind + (reason != null ? ", " + reason : "") + (code != null ? ", code=" + code : "")
                + (getMessage() != null ? ", " + getMessage() : "") + "}";
    }
}
