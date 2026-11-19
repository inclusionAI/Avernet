package a2a.agent.client.internal;

import java.net.http.HttpTimeoutException;
import java.util.Set;
import java.util.concurrent.CancellationException;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeoutException;

import a2a.agent.client.A2aCallException;
import a2a.agent.client.A2aCallException.Kind;
import a2a.agent.client.transport.RemoteError;

/**
 * 把任意异常分类成 {@link A2aCallException}。核心是区分“确定没执行”和“结果未知”，规则与 BCS 一致：
 * 明确的 4xx / 参数类错误为 REJECTED；408、409、429、5xx、超时、连接中断、不完整的响应为结果未知。
 */
public final class ErrorClassifier {

    /** 业务或对方 Adapter 用来表示“繁忙、未执行”的原因。 */
    private static final String BUSY = "BUSY";

    private static final Set<String> GRPC_REJECTED = Set.of("INVALID_ARGUMENT", "NOT_FOUND", "FAILED_PRECONDITION",
            "ALREADY_EXISTS", "PERMISSION_DENIED", "UNAUTHENTICATED", "OUT_OF_RANGE", "UNIMPLEMENTED");
    private static final Set<Integer> HTTP_REJECTED = Set.of(400, 401, 403, 404, 405, 406, 415, 422);

    private ErrorClassifier() {
    }

    public static A2aCallException classify(Throwable error) {
        Throwable e = unwrap(error);
        if (e instanceof A2aCallException a) {
            return a;
        }
        if (e instanceof ProtocolViolation v) {
            return new A2aCallException(Kind.PROTOCOL_VIOLATION, v.reason(), null, v.getMessage(), v);
        }
        if (e instanceof RemoteError r) {
            return fromRemote(r);
        }
        if (e instanceof IllegalArgumentException) {
            return new A2aCallException(Kind.REJECTED, "INVALID_REQUEST", null, e.getMessage(), e);
        }
        if (e instanceof HttpTimeoutException || e instanceof TimeoutException) {
            return new A2aCallException(Kind.TIMEOUT, null, null, e.getMessage(), e);
        }
        if (e instanceof CancellationException) {
            return new A2aCallException(Kind.CANCELLED, null, null, e.getMessage(), e);
        }
        return new A2aCallException(Kind.UNKNOWN_OUTCOME, null, null, String.valueOf(e), e);
    }

    private static A2aCallException fromRemote(RemoteError r) {
        Kind kind = switch (r.origin()) {
            case GRPC_STATUS -> grpc(r.code(), r.reason());
            case HTTP_STATUS -> http(Integer.parseInt(r.code()), r.reason());
            case JSON_RPC_ERROR -> jsonRpc(Long.parseLong(r.code()), r.reason());
            case NETWORK -> unwrap(r.getCause()) instanceof HttpTimeoutException ? Kind.TIMEOUT : Kind.UNKNOWN_OUTCOME;
            case PROTOCOL -> Kind.PROTOCOL_VIOLATION;
        };
        return new A2aCallException(kind, r.reason(), r.code(), r.getMessage(), r);
    }

    private static Kind grpc(String code, String reason) {
        if ("DEADLINE_EXCEEDED".equals(code)) {
            return Kind.TIMEOUT;
        }
        if ("CANCELLED".equals(code)) {
            return Kind.CANCELLED;
        }
        if (GRPC_REJECTED.contains(code)) {
            return Kind.REJECTED;
        }
        // UNAVAILABLE 可能发生在请求已送达之后，只有明确标明繁忙才算没执行
        if (("UNAVAILABLE".equals(code) || "RESOURCE_EXHAUSTED".equals(code)) && BUSY.equals(reason)) {
            return Kind.BUSY;
        }
        return Kind.UNKNOWN_OUTCOME;
    }

    private static Kind http(int status, String reason) {
        if (HTTP_REJECTED.contains(status)) {
            return Kind.REJECTED;
        }
        if (status == 503 && BUSY.equals(reason)) {
            return Kind.BUSY;
        }
        return Kind.UNKNOWN_OUTCOME;
    }

    private static Kind jsonRpc(long code, String reason) {
        if (code == -32603) {
            return BUSY.equals(reason) ? Kind.BUSY : Kind.UNKNOWN_OUTCOME;
        }
        // -32700..-32600：JSON-RPC 协议错误；-32009..-32001：A2A 定义的错误。都表示请求被拒绝、没有执行
        if ((code >= -32700 && code <= -32600) || (code >= -32009 && code <= -32001)) {
            return Kind.REJECTED;
        }
        return Kind.UNKNOWN_OUTCOME;
    }

    private static Throwable unwrap(Throwable error) {
        Throwable e = error;
        while ((e instanceof CompletionException || e instanceof ExecutionException) && e.getCause() != null) {
            e = e.getCause();
        }
        return e;
    }
}
