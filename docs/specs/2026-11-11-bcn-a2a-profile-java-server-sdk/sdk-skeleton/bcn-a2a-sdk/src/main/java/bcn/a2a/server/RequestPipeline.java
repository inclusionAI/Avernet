package bcn.a2a.server;

import java.time.Duration;
import java.time.Instant;
import java.util.Optional;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.Executor;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.Semaphore;
import java.util.concurrent.TimeUnit;
import java.util.function.UnaryOperator;

import bcn.a2a.ext.BcnKeys;
import bcn.a2a.spi.A2aListener;
import bcn.a2a.spi.TaskEngine;
import io.grpc.Context;
import io.grpc.Deadline;
import io.grpc.stub.ServerCallStreamObserver;
import io.grpc.stub.StreamObserver;
import org.a2aproject.sdk.grpc.CancelTaskRequest;
import org.a2aproject.sdk.grpc.GetTaskRequest;
import org.a2aproject.sdk.grpc.SendMessageRequest;
import org.a2aproject.sdk.grpc.SendMessageResponse;
import org.a2aproject.sdk.grpc.StreamResponse;
import org.a2aproject.sdk.grpc.SubscribeToTaskRequest;
import org.a2aproject.sdk.grpc.utils.ProtoUtils;
import org.a2aproject.sdk.spec.A2AError;
import org.a2aproject.sdk.spec.CancelTaskParams;
import org.a2aproject.sdk.spec.Message;
import org.a2aproject.sdk.spec.MessageSendParams;
import org.a2aproject.sdk.spec.Task;
import org.a2aproject.sdk.spec.TaskIdParams;
import org.a2aproject.sdk.spec.TaskNotFoundError;
import org.a2aproject.sdk.spec.TaskQueryParams;
import org.a2aproject.sdk.spec.VersionNotSupportedError;

/**
 * 请求处理流水线。所有 A2A 方法都经过这里，顺序固定：
 *
 * <ol>
 *   <li><b>开始</b>：计算截止时间，挂上取消回调，通知监听器（{@link #begin}）。</li>
 *   <li><b>准备</b>（在 RPC 线程上同步完成，失败直接返回错误）：大小、协议版本、转换为 a2a-java 模型、
 *       按 tenant 分发、扩展协商、入站校验、续交检查、contextId、回写激活结果（{@link #prepare}）。</li>
 *   <li><b>派发</b>：在业务线程池中执行业务代码，每个请求只提交一次，不在池内再次提交（避免线程池自锁）；
 *       并发已满或线程池拒绝时返回 UNAVAILABLE（{@link #dispatch}）。</li>
 *   <li><b>执行与出站检查</b>：调用业务方法；第一档检查回复，流式档位由 {@link TaskStreamWriter} 维护状态机。</li>
 *   <li><b>结束</b>：映射错误码、通知监听器（{@link CallScope}）。</li>
 * </ol>
 */
final class RequestPipeline {

    @FunctionalInterface
    interface Body {
        void run() throws Exception;
    }

    /** 准备阶段的结果。 */
    record Prepared(AgentEntry entry, DefaultBcnCall call) {
    }

    private final AgentRegistry registry;
    private final A2aServiceOptions options;
    private final Executor executor;
    private final UnaryOperator<Runnable> taskDecorator;
    private final A2aListener listener;
    private final Semaphore permits;

    RequestPipeline(AgentRegistry registry, A2aServiceOptions options, Executor executor,
                    UnaryOperator<Runnable> taskDecorator, A2aListener listener) {
        this.registry = registry;
        this.options = options;
        this.executor = executor;
        this.taskDecorator = taskDecorator;
        this.listener = listener;
        this.permits = new Semaphore(options.maxConcurrentCalls());
    }

    // ================================================================ SendMessage

    void sendMessage(SendMessageRequest request, StreamObserver<SendMessageResponse> out) {
        CallScope scope = begin("SendMessage", request.getTenant(), out, options.unaryTimeout());
        final Prepared p;
        try {
            p = prepare(request, scope);
        } catch (Throwable e) {
            scope.fail(out, e);
            return;
        }
        // TODO(实现)：unary 超时。SDK 不能中断业务线程：到期后先回复 DEADLINE_EXCEEDED（需要一个定时器，
        //   由 Builder 传入或 SDK 自建守护线程），业务稍后返回的结果丢弃；BcnCall.isCancelled() 届时已为 true。
        dispatch(scope, out, () -> {
            switch (p.entry().mode()) {
                case MESSAGE -> {
                    Message reply = OutboundGuard.checkReply(p.entry().agent().onMessage(p.call()), p.call());
                    out.onNext(ProtoUtils.ToProto.taskOrMessage(reply));
                    out.onCompleted();
                    scope.end(A2aListener.Outcome.OK, null);
                }
                case STREAM -> {
                    // 同一个 onStream 服务非流式调用：收集全部输出，返回最终 Task。
                    CollectingStream collector = new CollectingStream(p.call().contextId(),
                            p.call().activatedExtensions(), scope.cancel, p.entry().taskEngine().isPresent());
                    runStream(p, collector);
                    out.onNext(ProtoUtils.ToProto.taskOrMessage(collector.result()));
                    out.onCompleted();
                    scope.end(scope.outcomeOf(collector.lastState()), null);
                }
            }
        });
    }

    // ================================================================ SendStreamingMessage

    void sendStreamingMessage(SendMessageRequest request, StreamObserver<StreamResponse> out) {
        CallScope scope = begin("SendStreamingMessage", request.getTenant(), out, options.streamTimeout());
        final Prepared p;
        try {
            p = prepare(request, scope);
        } catch (Throwable e) {
            scope.fail(out, e);
            return;
        }
        dispatch(scope, out, () -> {
            switch (p.entry().mode()) {
                case MESSAGE -> {
                    // 没有 Task 的流：恰好一条 Message 后结束（A2A 3.1.2）。
                    Message reply = OutboundGuard.checkReply(p.entry().agent().onMessage(p.call()), p.call());
                    out.onNext(ProtoUtils.ToProto.taskOrMessageStream(reply));
                    out.onCompleted();
                    scope.end(A2aListener.Outcome.OK, null);
                }
                case STREAM -> {
                    StreamBridge bridge = new StreamBridge(p.call().contextId(), p.call().activatedExtensions(),
                            scope.cancel, p.entry().taskEngine().isPresent(), out);
                    runStream(p, bridge);
                    scope.end(scope.outcomeOf(bridge.lastState()), null);
                }
            }
        });
    }

    // ================================================================ GetTask / SubscribeToTask / CancelTask

    void getTask(GetTaskRequest request, StreamObserver<org.a2aproject.sdk.grpc.Task> out) {
        CallScope scope = begin("GetTask", request.getTenant(), out, options.unaryTimeout());
        final TaskQueryParams params;
        final TaskEngine engine;
        try {
            params = ProtoUtils.FromProto.taskQueryParams(request);
            engine = engineOf(params.tenant());
        } catch (Throwable e) {
            scope.fail(out, e);
            return;
        }
        dispatch(scope, out, () -> {
            Task task = engine.getTask(params);
            out.onNext(ProtoUtils.ToProto.task(task));
            out.onCompleted();
            scope.end(A2aListener.Outcome.OK, null);
        });
    }

    void subscribeToTask(SubscribeToTaskRequest request, StreamObserver<StreamResponse> out) {
        CallScope scope = begin("SubscribeToTask", request.getTenant(), out, options.streamTimeout());
        final TaskIdParams params;
        final TaskEngine engine;
        final Set<String> activated;
        try {
            params = ProtoUtils.FromProto.taskIdParams(request);
            AgentEntry entry = registry.resolve(params.tenant());
            engine = entry.taskEngine().orElseThrow(TaskNotFoundError::new);
            activated = ExtensionNegotiator.negotiate(CallHeaders.requested(), entry);
            CallHeaders.confirm(activated);
        } catch (Throwable e) {
            scope.fail(out, e);
            return;
        }
        dispatch(scope, out, () -> {
            // contextId 由引擎在 begin 中给出。
            StreamBridge bridge = new StreamBridge(null, activated, scope.cancel, true, out);
            try {
                engine.subscribe(params, bridge);
                bridge.closeIfOpen();
            } catch (Throwable e) {
                if (!bridge.abort()) {
                    throw asException(e);
                }
            }
            scope.end(scope.outcomeOf(bridge.lastState()), null);
        });
    }

    void cancelTask(CancelTaskRequest request, StreamObserver<org.a2aproject.sdk.grpc.Task> out) {
        CallScope scope = begin("CancelTask", request.getTenant(), out, options.unaryTimeout());
        final CancelTaskParams params;
        final TaskEngine engine;
        try {
            params = ProtoUtils.FromProto.cancelTaskParams(request);
            engine = engineOf(params.tenant());
        } catch (Throwable e) {
            scope.fail(out, e);
            return;
        }
        dispatch(scope, out, () -> {
            Task task = engine.cancel(params);
            out.onNext(ProtoUtils.ToProto.task(task));
            out.onCompleted();
            scope.end(A2aListener.Outcome.OK, null);
        });
    }

    /** Profile 不支持的方法（ListTasks、推送配置、扩展 Card）：直接返回对应的标准错误。 */
    <T> void reject(String method, String tenant, StreamObserver<T> out, A2AError error) {
        CallScope scope = begin(method, tenant, out, options.unaryTimeout());
        scope.fail(out, error);
    }

    // ================================================================ 各步骤

    private CallScope begin(String method, String tenant, StreamObserver<?> out, Duration timeout) {
        CancelSignal cancel = new CancelSignal(deadline(timeout));
        if (out instanceof ServerCallStreamObserver<?> serverOut) {
            // 必须在服务方法返回前设置。调用方取消后，BcnCall / BcnStream 的 isCancelled() 变为 true。
            serverOut.setOnCancelHandler(cancel::cancel);
        }
        A2aListener.CallInfo info = new A2aListener.CallInfo(method, tenant, UUID.randomUUID().toString(), System.nanoTime());
        try {
            listener.onStart(info);
        } catch (RuntimeException ignored) {
            // TODO(实现)：记日志。监听器的异常不能影响调用。
        }
        return new CallScope(info, cancel, listener);
    }

    /** 准备阶段：在 RPC 线程上同步执行，任何一步失败都直接返回错误，不进入业务线程池。 */
    private Prepared prepare(SendMessageRequest request, CallScope scope) {
        // 1. 大小（兜底；还应配置 gRPC 服务端 maxInboundMessageSize，在反序列化之前拦截）
        if (request.getSerializedSize() > options.maxRequestBytes()) {
            throw ProtocolErrors.invalid("REQUEST_TOO_LARGE", "request exceeds " + options.maxRequestBytes() + " bytes");
        }
        // 2. 协议版本
        Optional<String> version = CallHeaders.version();
        if (version.isPresent() && !BcnKeys.PROTOCOL_VERSION.equals(version.get())) {
            // TODO(待定)：是否接受同一大版本的其他小版本（例如 1.1）。
            throw new VersionNotSupportedError(null, "unsupported A2A version " + version.get(), null);
        }
        // 3. 转换为 a2a-java 模型
        MessageSendParams params = ProtoUtils.FromProto.messageSendParams(request);
        // 4. 按 tenant 分发
        AgentEntry entry = registry.resolve(params.tenant());
        // 5. 扩展协商（本期只有工具观察，且是可选的）
        Set<String> activated = ExtensionNegotiator.negotiate(CallHeaders.requested(), entry);
        // 6. 入站校验
        InboundValidator.validate(params, activated);
        // 7. 续交：只有引擎模式能识别 taskId，其他档位的 Task 只存在于原来那条流内
        String taskId = params.message().taskId();
        if (taskId != null && !taskId.isEmpty() && entry.taskEngine().isEmpty()) {
            throw new TaskNotFoundError();
        }
        // 8. contextId：首轮缺省时生成
        String contextId = params.message().contextId();
        if (contextId == null || contextId.isEmpty()) {
            contextId = UUID.randomUUID().toString();
        }
        // 9. 回写激活结果（随第一条响应的响应头发出）
        CallHeaders.confirm(activated);
        DefaultBcnCall call = new DefaultBcnCall(entry.id(), params, contextId,
                taskId == null || taskId.isEmpty() ? null : taskId,
                BcnKeys.businessView(params.metadata()), activated, scope.cancel);
        return new Prepared(entry, call);
    }

    /** 在业务线程池中执行一次。gRPC Context（含请求头状态、deadline）随任务传递，其他线程上下文由 taskDecorator 负责。 */
    private <T> void dispatch(CallScope scope, StreamObserver<T> out, Body body) {
        if (!permits.tryAcquire()) {
            scope.busy(out);
            return;
        }
        Runnable task = Context.current().wrap(() -> {
            try {
                body.run();
            } catch (Throwable e) {
                scope.fail(out, e);
            } finally {
                permits.release();
            }
        });
        try {
            executor.execute(taskDecorator.apply(task));
        } catch (RejectedExecutionException e) {
            permits.release();
            scope.busy(out);
        }
    }

    /**
     * 运行流式档位的业务方法。Task 已经发出后业务抛异常：转成 FAILED 终态并正常结束流；
     * 还没有发出任何事件：把异常抛给 dispatch，作为 gRPC 错误返回。
     */
    private static void runStream(Prepared p, TaskStreamWriter writer) throws Exception {
        try {
            p.entry().agent().onStream(p.call(), writer);
            writer.finishIfOpen();
        } catch (Throwable e) {
            if (!writer.abort()) {
                throw asException(e);
            }
            // TODO(实现)：记录业务异常（带 requestId）；调用方只看到 FAILED 和固定原因。
        }
    }

    private TaskEngine engineOf(String tenant) {
        return registry.resolve(tenant).taskEngine().orElseThrow(TaskNotFoundError::new);
    }

    /** 截止时间：取调用方 deadline 与本地超时中较早的一个。 */
    private static Instant deadline(Duration timeout) {
        Instant local = Instant.now().plus(timeout);
        Deadline remote = Context.current().getDeadline();
        if (remote == null) {
            return local;
        }
        Instant remoteAt = Instant.now().plusNanos(remote.timeRemaining(TimeUnit.NANOSECONDS));
        return remoteAt.isBefore(local) ? remoteAt : local;
    }

    private static Exception asException(Throwable e) {
        return e instanceof Exception ex ? ex : new java.util.concurrent.ExecutionException(e);
    }
}
