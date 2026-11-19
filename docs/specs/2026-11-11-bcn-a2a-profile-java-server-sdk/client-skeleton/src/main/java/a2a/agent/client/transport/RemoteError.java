package a2a.agent.client.transport;

/**
 * 传输层统一的错误表示。各传输把自己的错误（gRPC 状态、HTTP 状态码、JSON-RPC 错误、网络异常）
 * 转成它，上层再按 Payload 规范第 9 节分类成用户可见的 {@code A2aCallException}。
 *
 * <p>只带安全的诊断信息：状态码、错误原因（ErrorInfo.reason）、服务端给出的描述。不携带请求内容。
 */
public final class RemoteError extends RuntimeException {

    public enum Origin {
        /** gRPC 状态，code 为状态名，例如 NOT_FOUND。 */
        GRPC_STATUS,
        /** HTTP 状态码，code 为数字字符串，例如 404。 */
        HTTP_STATUS,
        /** JSON-RPC 错误对象，code 为错误码字符串，例如 -32001。 */
        JSON_RPC_ERROR,
        /** 网络异常（连接失败、中断、超时等），code 为 null。 */
        NETWORK,
        /** 响应无法解析或不符合协议，code 为 null。 */
        PROTOCOL
    }

    private final Origin origin;
    private final String code;
    private final String reason;

    private RemoteError(Origin origin, String code, String reason, String message, Throwable cause) {
        super(message, cause);
        this.origin = origin;
        this.code = code;
        this.reason = reason;
    }

    public static RemoteError grpc(String statusCode, String reason, String message, Throwable cause) {
        return new RemoteError(Origin.GRPC_STATUS, statusCode, reason, message, cause);
    }

    public static RemoteError http(int status, String reason, String message) {
        return new RemoteError(Origin.HTTP_STATUS, Integer.toString(status), reason, message, null);
    }

    public static RemoteError jsonRpc(long code, String reason, String message) {
        return new RemoteError(Origin.JSON_RPC_ERROR, Long.toString(code), reason, message, null);
    }

    public static RemoteError network(Throwable cause) {
        return new RemoteError(Origin.NETWORK, null, null, String.valueOf(cause), cause);
    }

    public static RemoteError protocol(String message, Throwable cause) {
        return new RemoteError(Origin.PROTOCOL, null, null, message, cause);
    }

    public Origin origin() {
        return origin;
    }

    /** gRPC 状态名 / HTTP 状态码 / JSON-RPC 错误码；网络和协议错误为 null。 */
    public String code() {
        return code;
    }

    /** google.rpc.ErrorInfo 中的 reason，例如 TASK_NOT_FOUND；没有时为 null。 */
    public String reason() {
        return reason;
    }
}
