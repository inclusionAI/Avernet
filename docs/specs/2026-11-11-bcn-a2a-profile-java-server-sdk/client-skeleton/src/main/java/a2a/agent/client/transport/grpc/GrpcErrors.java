package a2a.agent.client.transport.grpc;

import a2a.agent.client.transport.RemoteError;
import com.google.protobuf.Any;
import com.google.protobuf.InvalidProtocolBufferException;
import com.google.rpc.ErrorInfo;
import io.grpc.Status;
import io.grpc.StatusException;
import io.grpc.StatusRuntimeException;
import io.grpc.protobuf.StatusProto;

/** gRPC 异常 → {@link RemoteError}：取状态码，并从 google.rpc.ErrorInfo 中取出 reason。 */
final class GrpcErrors {

    private GrpcErrors() {
    }

    static RemoteError toRemote(Throwable t) {
        if (!(t instanceof StatusRuntimeException) && !(t instanceof StatusException)) {
            return RemoteError.network(t);
        }
        Status status = Status.fromThrowable(t);
        return RemoteError.grpc(status.getCode().name(), reasonOf(t), status.getDescription(), t);
    }

    private static String reasonOf(Throwable t) {
        com.google.rpc.Status rich = StatusProto.fromThrowable(t);
        if (rich == null) {
            return null;
        }
        for (Any detail : rich.getDetailsList()) {
            if (detail.is(ErrorInfo.class)) {
                try {
                    return detail.unpack(ErrorInfo.class).getReason();
                } catch (InvalidProtocolBufferException ignored) {
                    return null;
                }
            }
        }
        return null;
    }
}
