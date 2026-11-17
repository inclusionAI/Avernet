package bcn.a2a.server;

import java.util.Set;

import io.grpc.stub.ServerCallStreamObserver;
import io.grpc.stub.StreamObserver;
import org.a2aproject.sdk.grpc.StreamResponse;
import org.a2aproject.sdk.grpc.utils.ProtoUtils;
import org.a2aproject.sdk.spec.StreamingEventKind;

/** 把流式输出发到 gRPC 服务端流（SendStreamingMessage、SubscribeToTask）。 */
final class StreamBridge extends TaskStreamWriter {

    private final StreamObserver<StreamResponse> out;
    /** 原生 gRPC 路径下可用，用于判断调用方是否已断开和背压；测试或代理路径下为 null。 */
    private final ServerCallStreamObserver<StreamResponse> serverOut;

    StreamBridge(String contextId, Set<String> activated, CancelSignal cancel, boolean engineMode,
                 StreamObserver<StreamResponse> out) {
        super(contextId, activated, cancel, engineMode);
        this.out = out;
        this.serverOut = out instanceof ServerCallStreamObserver<StreamResponse> s ? s : null;
    }

    @Override
    protected void emit(StreamingEventKind event) {
        if (disconnected()) {
            return; // 调用方已经断开：后续输出全部丢弃，业务通过 isCancelled() 得知并尽快返回
        }
        // TODO(实现)：
        //  1. 背压：serverOut.isReady() 为 false 时放进有界缓冲（A2aServiceOptions.streamBufferEvents），
        //     缓冲写满就阻塞当前（业务）线程，直到 onReady 回调或截止时间；不能让事件无界堆积在 gRPC 内部缓冲。
        //  2. 截止时间：超过 streamTimeout 时发出 FAILED（原因 timeout）并结束流。
        //  3. 单个事件的大小上限（超出时视为 Agent 错误）。
        out.onNext(ProtoUtils.ToProto.taskOrMessageStream(event));
    }

    @Override
    protected void onClosed() {
        if (!disconnected()) {
            out.onCompleted();
        }
    }

    private boolean disconnected() {
        return serverOut != null && serverOut.isCancelled();
    }
}
