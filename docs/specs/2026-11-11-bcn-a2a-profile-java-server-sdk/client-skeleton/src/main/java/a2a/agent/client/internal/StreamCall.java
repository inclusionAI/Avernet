package a2a.agent.client.internal;

import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.function.Consumer;

import a2a.agent.client.A2aCallException;
import a2a.agent.client.A2aCallException.Kind;
import a2a.agent.client.Reply;
import a2a.agent.client.StreamHandle;
import a2a.agent.client.StreamListener;
import a2a.agent.client.ToolRecord;
import a2a.agent.client.card.SelectedInterface;
import a2a.agent.client.transport.CallContext;
import a2a.agent.client.transport.StreamSink;
import a2a.agent.client.transport.TransportEndpoint;
import org.a2aproject.sdk.grpc.StreamResponse;

/**
 * 一条流式调用（SendStreamingMessage 或 SubscribeToTask）的编排：
 * <ol>
 *   <li>限流：拿到许可才发起，流结束时释放；</li>
 *   <li>逐个事件交给 {@link ResponseProjector}，处理完再向传输要下一个（背压）；</li>
 *   <li>回调经 {@link SerialRunner} 串行执行，可选转到业务线程池；</li>
 *   <li>整体时限：定时器到期取消调用，以 TIMEOUT 结束；</li>
 *   <li>断流（连接中断、流在终态前结束）且已经拿到 taskId：用 GetTask 尝试恢复一次，
 *       拿到终态就正常结束；收到 TASK_NOT_FOUND 等错误则以原错误失败，不重连（Payload 规范 PL-23）。</li>
 * </ol>
 */
public final class StreamCall implements StreamSink<StreamResponse>, StreamHandle, ResponseProjector.Events {

    private final ClientRuntime runtime;
    private final TransportEndpoint endpoint;
    private final SelectedInterface selected;
    private final StreamListener listener;
    private final ResponseProjector projector;
    private final Runnable releasePermit;
    private final SerialRunner callbacks;
    private final CompletableFuture<Reply> completion = new CompletableFuture<>();
    private final AtomicBoolean done = new AtomicBoolean();

    private volatile StreamControl control;
    private volatile ScheduledFuture<?> timeout;

    private StreamCall(ClientRuntime runtime, TransportEndpoint endpoint, SelectedInterface selected,
                       StreamListener listener, boolean toolsEnabled, Runnable releasePermit) {
        this.runtime = runtime;
        this.endpoint = endpoint;
        this.selected = selected;
        this.listener = listener;
        this.projector = new ResponseProjector(toolsEnabled, this);
        this.releasePermit = releasePermit;
        this.callbacks = new SerialRunner(runtime.options().callbackExecutor());
    }

    /**
     * 发起一条流。
     *
     * @param starter 调用传输的 sendStreamingMessage 或 subscribeToTask，把本对象作为接收方传进去
     */
    public static StreamHandle start(ClientRuntime runtime, TransportEndpoint endpoint, SelectedInterface selected,
                                     StreamListener listener, boolean toolsEnabled, Duration streamTimeout,
                                     Consumer<StreamSink<StreamResponse>> starter) {
        Runnable release;
        try {
            release = runtime.limiter().acquire();
        } catch (A2aCallException busy) {
            StreamCall rejected = new StreamCall(runtime, endpoint, selected, listener, toolsEnabled, () -> { });
            rejected.callbacks.execute(() -> rejected.fail(busy));
            return rejected;
        }
        StreamCall call = new StreamCall(runtime, endpoint, selected, listener, toolsEnabled, release);
        call.timeout = runtime.timer().schedule(call::onTimeout, streamTimeout.toMillis(), TimeUnit.MILLISECONDS);
        try {
            starter.accept(call);
        } catch (RuntimeException e) {
            call.callbacks.execute(() -> call.fail(ErrorClassifier.classify(e)));
        }
        return call;
    }

    // ------------------------------------------------------------------ StreamSink（传输线程回调）

    @Override
    public void onStart(StreamControl control) {
        this.control = control;
        if (done.get()) {
            control.cancel(); // 发起之前就已经超时或取消
        }
    }

    @Override
    public void onNext(StreamResponse event) {
        callbacks.execute(() -> process(event));
    }

    @Override
    public void onError(Throwable error) {
        callbacks.execute(() -> handleBreak(ErrorClassifier.classify(error)));
    }

    @Override
    public void onCompleted() {
        callbacks.execute(() -> {
            if (done.get()) {
                return;
            }
            if (projector.finished()) {
                finishOk();
            } else {
                handleBreak(new A2aCallException(Kind.UNKNOWN_OUTCOME, "STREAM_ENDED", null,
                        "stream ended before a terminal state", null));
            }
        });
    }

    // ------------------------------------------------------------------ StreamHandle

    @Override
    public void cancel() {
        if (done.get()) {
            return;
        }
        cancelTransport();
        callbacks.execute(() -> fail(new A2aCallException(Kind.CANCELLED, null, null, "cancelled by caller", null)));
    }

    @Override
    public CompletableFuture<Reply> completion() {
        return completion;
    }

    // ------------------------------------------------------------------ ResponseProjector.Events（在回调线程上）

    @Override
    public void text(String delta) {
        listener.onText(delta);
    }

    @Override
    public void tool(ToolRecord record) {
        listener.onTool(record);
    }

    @Override
    public void status(Reply.State state, String text) {
        listener.onStatus(state, text);
    }

    // ------------------------------------------------------------------ 内部（都在 callbacks 上串行执行）

    private void process(StreamResponse event) {
        if (done.get()) {
            return;
        }
        try {
            projector.accept(event);
        } catch (ProtocolViolation violation) {
            cancelTransport();
            fail(ErrorClassifier.classify(violation));
            return;
        } catch (RuntimeException listenerError) {
            cancelTransport();
            fail(new A2aCallException(Kind.CANCELLED, "LISTENER_ERROR", null, "listener threw", listenerError));
            return;
        }
        if (projector.finished()) {
            finishOk();
        } else {
            StreamControl c = control;
            if (c != null) {
                c.request(1); // 处理完一个再要下一个
            }
        }
    }

    private void handleBreak(A2aCallException error) {
        if (done.get()) {
            return;
        }
        if (projector.taskId() != null && error.kind() == Kind.UNKNOWN_OUTCOME) {
            recover(error);
        } else {
            fail(error);
        }
    }

    /** 断流后用 GetTask 尝试恢复一次。 */
    private void recover(A2aCallException original) {
        CallContext context = CallContext.of(runtime.options().callTimeout());
        endpoint.getTask(RequestFactory.getTask(selected, projector.taskId()), context).whenComplete((task, error) ->
                callbacks.execute(() -> {
                    if (done.get()) {
                        return;
                    }
                    if (error != null) {
                        fail(original); // 包括 TASK_NOT_FOUND：形态 A、B 的 Task 只存在于原来那条流内
                        return;
                    }
                    try {
                        projector.acceptSnapshot(task);
                    } catch (ProtocolViolation violation) {
                        fail(ErrorClassifier.classify(violation));
                        return;
                    } catch (RuntimeException listenerError) {
                        fail(new A2aCallException(Kind.CANCELLED, "LISTENER_ERROR", null, "listener threw", listenerError));
                        return;
                    }
                    if (projector.finished()) {
                        finishOk();
                    } else {
                        // TODO(实现)：形态 C（支持订阅）可以在这里改用 SubscribeToTask 继续接收
                        fail(original);
                    }
                }));
    }

    private void onTimeout() {
        if (done.get()) {
            return;
        }
        cancelTransport();
        callbacks.execute(() -> fail(new A2aCallException(Kind.TIMEOUT, "STREAM_TIMEOUT", null, "stream timeout", null)));
    }

    private void finishOk() {
        if (!done.compareAndSet(false, true)) {
            return;
        }
        cleanup();
        Reply reply = projector.reply(null);
        try {
            listener.onComplete(reply);
        } catch (RuntimeException ignored) {
            // TODO(实现)：记日志。回调异常不影响结果
        }
        completion.complete(reply);
    }

    private void fail(A2aCallException error) {
        if (!done.compareAndSet(false, true)) {
            return;
        }
        cleanup();
        try {
            listener.onError(error);
        } catch (RuntimeException ignored) {
            // TODO(实现)：记日志
        }
        completion.completeExceptionally(error);
    }

    private void cleanup() {
        ScheduledFuture<?> t = timeout;
        if (t != null) {
            t.cancel(false);
        }
        releasePermit.run();
    }

    private void cancelTransport() {
        StreamControl c = control;
        if (c != null) {
            c.cancel();
        }
    }
}
