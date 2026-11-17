package bcn.a2a.server;

import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.UndeclaredThrowableException;
import java.util.concurrent.CompletionException;
import java.util.concurrent.ExecutionException;

import bcn.a2a.spi.BcnException;
import com.google.protobuf.Any;
import com.google.rpc.ErrorInfo;
import io.grpc.Status;
import io.grpc.StatusRuntimeException;
import io.grpc.protobuf.StatusProto;
import org.a2aproject.sdk.spec.A2AError;
import org.a2aproject.sdk.spec.ContentTypeNotSupportedError;
import org.a2aproject.sdk.spec.ExtendedAgentCardNotConfiguredError;
import org.a2aproject.sdk.spec.ExtensionSupportRequiredError;
import org.a2aproject.sdk.spec.InvalidAgentResponseError;
import org.a2aproject.sdk.spec.InvalidParamsError;
import org.a2aproject.sdk.spec.PushNotificationNotSupportedError;
import org.a2aproject.sdk.spec.TaskNotCancelableError;
import org.a2aproject.sdk.spec.TaskNotFoundError;
import org.a2aproject.sdk.spec.UnsupportedOperationError;
import org.a2aproject.sdk.spec.VersionNotSupportedError;

/**
 * 把任意异常映射为 gRPC 错误：状态码按 A2A 规范的映射表，原因写进 {@code google.rpc.ErrorInfo}。
 *
 * <p>标准错误的 domain 为 {@code a2a-protocol.org}；SDK 自定义原因（{@link ProtocolErrors}）使用 BCN 的错误域。
 * 非 A2A、非 {@link BcnException} 的异常一律为 INTERNAL，描述固定，不泄露异常信息。
 */
final class ErrorMapper {

    static final String A2A_DOMAIN = "a2a-protocol.org";
    /** TODO(待定，HQ-6)：BCN 自定义原因使用的错误域。 */
    static final String BCN_DOMAIN = "bcn";

    private ErrorMapper() {
    }

    static StatusRuntimeException toStatus(Throwable error) {
        Throwable e = unwrap(error);
        if (e instanceof StatusRuntimeException sre) {
            return sre;
        }
        if (e instanceof BcnException be) {
            return switch (be.code()) {
                case INVALID_INPUT -> build(Status.Code.INVALID_ARGUMENT, "INVALID_INPUT", BCN_DOMAIN, be.getMessage());
                case UNSUPPORTED_CONTENT -> build(Status.Code.INVALID_ARGUMENT, "CONTENT_TYPE_NOT_SUPPORTED", A2A_DOMAIN, be.getMessage());
                case UNAVAILABLE -> build(Status.Code.UNAVAILABLE, "UNAVAILABLE", BCN_DOMAIN, be.getMessage());
            };
        }
        if (e instanceof A2AError a2a) {
            return fromA2a(a2a);
        }
        // TODO(实现)：记录原始异常（带 requestId），这里只返回固定描述。
        return build(Status.Code.INTERNAL, "INTERNAL", BCN_DOMAIN, "internal error");
    }

    /** 并发已满或线程池拒绝。 */
    static StatusRuntimeException busy() {
        return build(Status.Code.UNAVAILABLE, "BUSY", BCN_DOMAIN, "agent is busy, retry later");
    }

    private static StatusRuntimeException fromA2a(A2AError e) {
        String message = e.getMessage();
        if (e instanceof TaskNotFoundError) {
            return build(Status.Code.NOT_FOUND, "TASK_NOT_FOUND", A2A_DOMAIN, message);
        }
        if (e instanceof TaskNotCancelableError) {
            return build(Status.Code.FAILED_PRECONDITION, "TASK_NOT_CANCELABLE", A2A_DOMAIN, message);
        }
        if (e instanceof PushNotificationNotSupportedError) {
            return build(Status.Code.FAILED_PRECONDITION, "PUSH_NOTIFICATION_NOT_SUPPORTED", A2A_DOMAIN, message);
        }
        if (e instanceof UnsupportedOperationError) {
            return build(Status.Code.FAILED_PRECONDITION, "UNSUPPORTED_OPERATION", A2A_DOMAIN, message);
        }
        if (e instanceof ContentTypeNotSupportedError) {
            return build(Status.Code.INVALID_ARGUMENT, "CONTENT_TYPE_NOT_SUPPORTED", A2A_DOMAIN, message);
        }
        if (e instanceof InvalidAgentResponseError) {
            return build(Status.Code.INTERNAL, "INVALID_AGENT_RESPONSE", A2A_DOMAIN, message);
        }
        if (e instanceof ExtendedAgentCardNotConfiguredError) {
            return build(Status.Code.FAILED_PRECONDITION, "EXTENDED_AGENT_CARD_NOT_CONFIGURED", A2A_DOMAIN, message);
        }
        if (e instanceof ExtensionSupportRequiredError) {
            return build(Status.Code.FAILED_PRECONDITION, "EXTENSION_SUPPORT_REQUIRED", A2A_DOMAIN, message);
        }
        if (e instanceof VersionNotSupportedError) {
            return build(Status.Code.FAILED_PRECONDITION, "VERSION_NOT_SUPPORTED", A2A_DOMAIN, message);
        }
        if (e instanceof InvalidParamsError) {
            Object reason = e.getDetails().get(ProtocolErrors.REASON);
            return reason instanceof String r
                    ? build(Status.Code.INVALID_ARGUMENT, r, BCN_DOMAIN, message)
                    : build(Status.Code.INVALID_ARGUMENT, "INVALID_PARAMS", A2A_DOMAIN, message);
        }
        // TODO(实现)：其余 A2AError（InternalError、JSON 解析类错误等）按规范补全；默认按 INTERNAL。
        return build(Status.Code.INTERNAL, "INTERNAL", A2A_DOMAIN, "internal error");
    }

    private static StatusRuntimeException build(Status.Code code, String reason, String domain, String message) {
        ErrorInfo info = ErrorInfo.newBuilder().setReason(reason).setDomain(domain).build();
        com.google.rpc.Status status = com.google.rpc.Status.newBuilder()
                .setCode(code.value())
                .setMessage(message == null ? reason : message)
                .addDetails(Any.pack(info))
                .build();
        return StatusProto.toStatusRuntimeException(status);
    }

    /** 还原被反射、代理或并发框架包装过的异常（SOFA 代理路径同样适用，见 bcn.a2a.sofa 包说明）。 */
    private static Throwable unwrap(Throwable error) {
        Throwable e = error;
        while ((e instanceof InvocationTargetException || e instanceof UndeclaredThrowableException
                || e instanceof ExecutionException || e instanceof CompletionException) && e.getCause() != null) {
            e = e.getCause();
        }
        // TODO(SOFA)：补充 SOFARPC 自己的包装异常类型。
        return e;
    }
}
