package a2a.agent.client;

import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.Executor;

import a2a.agent.client.card.SelectedInterface;
import a2a.agent.client.internal.ClientRuntime;
import a2a.agent.client.internal.ErrorClassifier;
import a2a.agent.client.internal.ProtocolViolation;
import a2a.agent.client.internal.RequestFactory;
import a2a.agent.client.internal.ResponseProjector;
import a2a.agent.client.internal.StreamCall;
import a2a.agent.client.transport.CallContext;
import a2a.agent.client.transport.TransportEndpoint;
import org.a2aproject.sdk.grpc.AgentCard;
import org.a2aproject.sdk.grpc.SendMessageRequest;
import org.a2aproject.sdk.grpc.Task;

/**
 * 一个远端 Agent：由 Card 选定了接口、打开了端点。
 *
 * <p><b>线程安全、可复用，应当缓存</b>：Card 解析、接口选择、路由头都在创建时算好一次，
 * 之后每次调用只构造请求。高并发场景下不要每次调用都重新创建。
 */
public final class RemoteAgent {

    private final ClientRuntime runtime;
    private final SelectedInterface selected;
    private final TransportEndpoint endpoint;

    RemoteAgent(ClientRuntime runtime, SelectedInterface selected) {
        this.runtime = runtime;
        this.selected = selected;
        this.endpoint = selected.transport().open(selected.agentInterface());
    }

    // ------------------------------------------------------------------ 一次性调用

    /** 同步发送，等到结束。失败抛 {@link A2aCallException}。 */
    public Reply send(String text) {
        return send(AgentRequest.text(text).build());
    }

    public Reply send(AgentRequest request) {
        return join(sendAsync(request));
    }

    /** 异步发送：不占用调用线程，适合高并发场景。 */
    public CompletableFuture<Reply> sendAsync(AgentRequest request) {
        Runnable release;
        try {
            release = runtime.limiter().acquire();
        } catch (A2aCallException busy) {
            return CompletableFuture.failedFuture(busy);
        }
        boolean tools = RequestFactory.toolsEnabled(selected, request, runtime.options());
        SendMessageRequest wire;
        try {
            wire = RequestFactory.sendMessage(selected, request, runtime.caller());
        } catch (RuntimeException e) {
            release.run();
            return CompletableFuture.failedFuture(ErrorClassifier.classify(e));
        }
        CallContext context = new CallContext(timeoutOf(request, runtime.options().callTimeout()),
                RequestFactory.extensionsHeader(tools), null);
        return endpoint.sendMessage(wire, context).handle((response, error) -> {
            release.run();
            if (error != null) {
                throw new CompletionException(ErrorClassifier.classify(error));
            }
            ResponseProjector projector = new ResponseProjector(tools, ResponseProjector.Events.NONE);
            try {
                projector.acceptUnary(response);
            } catch (ProtocolViolation violation) {
                throw new CompletionException(ErrorClassifier.classify(violation));
            }
            return projector.reply(response);
        });
    }

    // ------------------------------------------------------------------ 流式调用

    public StreamHandle stream(String text, StreamListener listener) {
        return stream(AgentRequest.text(text).build(), listener);
    }

    /**
     * 流式发送。对方 Card 没有声明 streaming 时，自动改用一元调用，并以一次 onText + onComplete 回调结果，
     * 调用方写法不变。
     */
    public StreamHandle stream(AgentRequest request, StreamListener listener) {
        if (!selected.streaming()) {
            return unaryAsStream(request, listener);
        }
        boolean tools = RequestFactory.toolsEnabled(selected, request, runtime.options());
        SendMessageRequest wire;
        try {
            wire = RequestFactory.sendMessage(selected, request, runtime.caller());
        } catch (RuntimeException e) {
            return failedHandle(ErrorClassifier.classify(e), listener);
        }
        Duration timeout = timeoutOf(request, runtime.options().streamTimeout());
        CallContext context = new CallContext(timeout, RequestFactory.extensionsHeader(tools), null);
        return StreamCall.start(runtime, endpoint, selected, listener, tools, timeout,
                sink -> endpoint.sendStreamingMessage(wire, context, sink));
    }

    /** 订阅一个进行中的 Task（只对引擎模式的 Agent 有意义，其他 Agent 返回 TASK_NOT_FOUND）。 */
    public StreamHandle subscribe(String taskId, StreamListener listener) {
        boolean tools = runtime.options().requestToolCalls()
                && selected.declaresExtension(a2a.agent.client.internal.PayloadKeys.TOOLCALL_EXTENSION);
        Duration timeout = runtime.options().streamTimeout();
        CallContext context = new CallContext(timeout, RequestFactory.extensionsHeader(tools), null);
        return StreamCall.start(runtime, endpoint, selected, listener, tools, timeout,
                sink -> endpoint.subscribeToTask(RequestFactory.subscribe(selected, taskId), context, sink));
    }

    // ------------------------------------------------------------------ 任务类（引擎模式的 Agent）

    /** 查询 Task。TODO(实现)：只读操作，可在 UNKNOWN_OUTCOME / BUSY 时退避重试一次。 */
    public CompletableFuture<Task> getTask(String taskId) {
        return mapErrors(endpoint.getTask(RequestFactory.getTask(selected, taskId), CallContext.of(runtime.options().callTimeout())));
    }

    /** 取消 Task。返回的状态不是 CANCELED 时，表示取消未确认。 */
    public CompletableFuture<Task> cancelTask(String taskId) {
        return mapErrors(endpoint.cancelTask(RequestFactory.cancelTask(selected, taskId), CallContext.of(runtime.options().callTimeout())));
    }

    // ------------------------------------------------------------------ 其他

    /** 多轮对话：自动沿用 contextId。 */
    public Conversation conversation() {
        return new Conversation(this);
    }

    /** 低层接口：标准 5 个方法，收发 protobuf 对象，协议细节由调用方自己处理。 */
    public TransportEndpoint raw() {
        return endpoint;
    }

    public AgentCard card() {
        return selected.card();
    }

    /** 实际使用的绑定，例如 SOFA-GRPC。 */
    public String binding() {
        return selected.binding();
    }

    // ------------------------------------------------------------------ 内部

    private StreamHandle unaryAsStream(AgentRequest request, StreamListener listener) {
        Executor executor = runtime.options().callbackExecutor();
        CompletableFuture<Reply> result = new CompletableFuture<>();
        sendAsync(request).whenComplete((reply, error) -> {
            Runnable deliver = () -> {
                if (error != null) {
                    A2aCallException e = ErrorClassifier.classify(error);
                    safe(() -> listener.onError(e));
                    result.completeExceptionally(e);
                } else {
                    if (!reply.text().isEmpty()) {
                        safe(() -> listener.onText(reply.text()));
                    }
                    safe(() -> listener.onComplete(reply));
                    result.complete(reply);
                }
            };
            if (executor == null) {
                deliver.run();
            } else {
                executor.execute(deliver);
            }
        });
        return new StreamHandle() {
            @Override
            public void cancel() {
                // TODO(实现)：一元调用的取消需要传输支持（gRPC 可取消 ClientCall），目前只停止回调
                result.cancel(false);
            }

            @Override
            public CompletableFuture<Reply> completion() {
                return result;
            }
        };
    }

    private static StreamHandle failedHandle(A2aCallException error, StreamListener listener) {
        safe(() -> listener.onError(error));
        CompletableFuture<Reply> failed = CompletableFuture.failedFuture(error);
        return new StreamHandle() {
            @Override
            public void cancel() {
            }

            @Override
            public CompletableFuture<Reply> completion() {
                return failed;
            }
        };
    }

    private static <T> CompletableFuture<T> mapErrors(CompletableFuture<T> future) {
        return future.handle((value, error) -> {
            if (error != null) {
                throw new CompletionException(ErrorClassifier.classify(error));
            }
            return value;
        });
    }

    private static Duration timeoutOf(AgentRequest request, Duration fallback) {
        return request.timeout() != null ? request.timeout() : fallback;
    }

    static <T> T join(CompletableFuture<T> future) {
        try {
            return future.join();
        } catch (CompletionException e) {
            throw ErrorClassifier.classify(e.getCause());
        }
    }

    private static void safe(Runnable r) {
        try {
            r.run();
        } catch (RuntimeException ignored) {
            // TODO(实现)：记日志。回调异常不影响结果
        }
    }
}
