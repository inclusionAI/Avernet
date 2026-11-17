package bcn.a2a.server;

import java.util.Map;

import org.a2aproject.sdk.spec.InvalidAgentResponseError;
import org.a2aproject.sdk.spec.InvalidParamsError;

/**
 * SDK 自己产生的协议错误。带 BCN 自定义原因（reason）的错误都从这里构造，
 * reason 放在 details 中，由 {@link ErrorMapper} 写进 {@code google.rpc.ErrorInfo}。
 */
final class ProtocolErrors {

    /** details 中存放自定义原因的键。 */
    static final String REASON = "reason";

    private ProtocolErrors() {
    }

    /** 请求不合法（INVALID_ARGUMENT）。reason 用大写下划线，例如 AGENT_NOT_FOUND、REQUEST_TOO_LARGE。 */
    static InvalidParamsError invalid(String reason, String message) {
        return new InvalidParamsError(null, message, Map.of(REASON, reason));
    }

    /** 业务返回的内容不符合 Profile（INTERNAL）。message 只进日志和错误描述，不含业务数据。 */
    static InvalidAgentResponseError badResponse(String message) {
        return new InvalidAgentResponseError(null, message, null);
    }
}
