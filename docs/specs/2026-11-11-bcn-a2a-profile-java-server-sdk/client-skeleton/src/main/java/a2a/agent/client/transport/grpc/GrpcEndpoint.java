package a2a.agent.client.transport.grpc;

import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.BiConsumer;

import a2a.agent.client.transport.CallContext;
import a2a.agent.client.transport.RemoteError;
import a2a.agent.client.transport.StreamSink;
import a2a.agent.client.transport.TransportEndpoint;
import io.grpc.CallOptions;
import io.grpc.Channel;
import io.grpc.ClientCall;
import io.grpc.ClientInterceptor;
import io.grpc.ForwardingClientCall;
import io.grpc.ForwardingClientCallListener;
import io.grpc.Metadata;
import io.grpc.MethodDescriptor;
import io.grpc.stub.ClientCallStreamObserver;
import io.grpc.stub.ClientResponseObserver;
import io.grpc.stub.MetadataUtils;
import io.grpc.stub.StreamObserver;
import org.a2aproject.sdk.grpc.A2AServiceGrpc;
import org.a2aproject.sdk.grpc.CancelTaskRequest;
import org.a2aproject.sdk.grpc.GetTaskRequest;
import org.a2aproject.sdk.grpc.SendMessageRequest;
import org.a2aproject.sdk.grpc.SendMessageResponse;
import org.a2aproject.sdk.grpc.StreamResponse;
import org.a2aproject.sdk.grpc.SubscribeToTaskRequest;
import org.a2aproject.sdk.grpc.Task;

/**
 * 基于标准 A2A gRPC 桩的端点，SOFA-GRPC 和直连 GRPC 共用。
 *
 * <p><b>性能</b>：
 * <ul>
 *   <li>Channel 由传输共享，这里只持有一个预先挂好静态头（版本、路由）的 stub；</li>
 *   <li>每次调用只额外设置 deadline，以及在请求了扩展时追加一个头，开销是几个小对象；</li>
 *   <li>流式调用关闭自动流控，只预取一个事件，处理完再要下一个（见 {@link StreamSink}）。</li>
 * </ul>
 */
final class GrpcEndpoint implements TransportEndpoint {

    static final Metadata.Key<String> VERSION = Metadata.Key.of("a2a-version", Metadata.ASCII_STRING_MARSHALLER);
    static final Metadata.Key<String> EXTENSIONS = Metadata.Key.of("a2a-extensions", Metadata.ASCII_STRING_MARSHALLER);

    private final A2AServiceGrpc.A2AServiceStub stub;

    /**
     * @param staticHeaders 每次调用都带的头：A2A-Version，以及 SOFA-GRPC 的路由头。只在打开端点时构造一次
     */
    GrpcEndpoint(Channel channel, Metadata staticHeaders) {
        this.stub = A2AServiceGrpc.newStub(channel)
                .withInterceptors(MetadataUtils.newAttachHeadersInterceptor(staticHeaders));
    }

    @Override
    public CompletableFuture<SendMessageResponse> sendMessage(SendMessageRequest request, CallContext context) {
        return unary(context, (s, o) -> s.sendMessage(request, o));
    }

    @Override
    public void sendStreamingMessage(SendMessageRequest request, CallContext context, StreamSink<StreamResponse> sink) {
        prepare(context).sendStreamingMessage(request, new FlowControlledObserver<>(sink));
    }

    @Override
    public CompletableFuture<Task> getTask(GetTaskRequest request, CallContext context) {
        return unary(context, (s, o) -> s.getTask(request, o));
    }

    @Override
    public CompletableFuture<Task> cancelTask(CancelTaskRequest request, CallContext context) {
        return unary(context, (s, o) -> s.cancelTask(request, o));
    }

    @Override
    public void subscribeToTask(SubscribeToTaskRequest request, CallContext context, StreamSink<StreamResponse> sink) {
        prepare(context).subscribeToTask(request, new FlowControlledObserver<>(sink));
    }

    // ------------------------------------------------------------------ 内部

    /** 按本次调用设置 deadline、扩展头和响应头回调。 */
    private A2AServiceGrpc.A2AServiceStub prepare(CallContext context) {
        A2AServiceGrpc.A2AServiceStub s = stub.withDeadlineAfter(context.timeout().toMillis(), TimeUnit.MILLISECONDS);
        if (context.extensionsHeader() != null) {
            Metadata extra = new Metadata();
            extra.put(EXTENSIONS, context.extensionsHeader());
            s = s.withInterceptors(MetadataUtils.newAttachHeadersInterceptor(extra));
        }
        if (context.onActivatedExtensions() != null) {
            s = s.withInterceptors(new ResponseHeaderInterceptor(context));
        }
        return s;
    }

    private <T> CompletableFuture<T> unary(CallContext context, BiConsumer<A2AServiceGrpc.A2AServiceStub, StreamObserver<T>> call) {
        CompletableFuture<T> result = new CompletableFuture<>();
        call.accept(prepare(context), new StreamObserver<>() {
            private T value;

            @Override
            public void onNext(T v) {
                value = v;
            }

            @Override
            public void onError(Throwable t) {
                result.completeExceptionally(GrpcErrors.toRemote(t));
            }

            @Override
            public void onCompleted() {
                if (value == null) {
                    result.completeExceptionally(RemoteError.protocol("empty unary response", null));
                } else {
                    result.complete(value);
                }
            }
        });
        return result;
    }

    /** 手动流控的观察者：只预取一个事件，接收方处理完再 request(1)。 */
    private static final class FlowControlledObserver<Q> implements ClientResponseObserver<Q, StreamResponse> {

        private final StreamSink<StreamResponse> sink;
        private volatile boolean cancelledByClient;

        FlowControlledObserver(StreamSink<StreamResponse> sink) {
            this.sink = sink;
        }

        @Override
        public void beforeStart(ClientCallStreamObserver<Q> call) {
            call.disableAutoRequestWithInitial(1);
            sink.onStart(new StreamSink.StreamControl() {
                @Override
                public void request(int n) {
                    call.request(n);
                }

                @Override
                public void cancel() {
                    cancelledByClient = true;
                    call.cancel("cancelled by client", null);
                }
            });
        }

        @Override
        public void onNext(StreamResponse event) {
            sink.onNext(event);
        }

        @Override
        public void onError(Throwable t) {
            if (!cancelledByClient) {
                sink.onError(GrpcErrors.toRemote(t));
            }
        }

        @Override
        public void onCompleted() {
            sink.onCompleted();
        }
    }

    /** 读取响应头中的 A2A-Extensions（服务端确认激活的扩展）。 */
    private static final class ResponseHeaderInterceptor implements ClientInterceptor {

        private final CallContext context;

        ResponseHeaderInterceptor(CallContext context) {
            this.context = context;
        }

        @Override
        public <Q, R> ClientCall<Q, R> interceptCall(MethodDescriptor<Q, R> method, CallOptions options, Channel next) {
            return new ForwardingClientCall.SimpleForwardingClientCall<>(next.newCall(method, options)) {
                @Override
                public void start(Listener<R> listener, Metadata headers) {
                    super.start(new ForwardingClientCallListener.SimpleForwardingClientCallListener<>(listener) {
                        @Override
                        public void onHeaders(Metadata responseHeaders) {
                            context.notifyActivated(responseHeaders.get(EXTENSIONS));
                            super.onHeaders(responseHeaders);
                        }
                    }, headers);
                }
            };
        }
    }
}
