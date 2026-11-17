package bcn.a2a.server;

import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.Objects;
import java.util.concurrent.Executor;
import java.util.function.UnaryOperator;

import bcn.a2a.spi.A2aListener;
import bcn.a2a.spi.BcnAgent;
import com.google.protobuf.Empty;
import io.grpc.BindableService;
import io.grpc.ServerInterceptors;
import io.grpc.ServerServiceDefinition;
import io.grpc.stub.StreamObserver;
import org.a2aproject.sdk.grpc.A2AServiceGrpc;
import org.a2aproject.sdk.grpc.AgentCard;
import org.a2aproject.sdk.grpc.CancelTaskRequest;
import org.a2aproject.sdk.grpc.DeleteTaskPushNotificationConfigRequest;
import org.a2aproject.sdk.grpc.GetExtendedAgentCardRequest;
import org.a2aproject.sdk.grpc.GetTaskPushNotificationConfigRequest;
import org.a2aproject.sdk.grpc.GetTaskRequest;
import org.a2aproject.sdk.grpc.ListTaskPushNotificationConfigsRequest;
import org.a2aproject.sdk.grpc.ListTaskPushNotificationConfigsResponse;
import org.a2aproject.sdk.grpc.ListTasksRequest;
import org.a2aproject.sdk.grpc.ListTasksResponse;
import org.a2aproject.sdk.grpc.SendMessageRequest;
import org.a2aproject.sdk.grpc.SendMessageResponse;
import org.a2aproject.sdk.grpc.StreamResponse;
import org.a2aproject.sdk.grpc.SubscribeToTaskRequest;
import org.a2aproject.sdk.grpc.Task;
import org.a2aproject.sdk.grpc.TaskPushNotificationConfig;
import org.a2aproject.sdk.spec.ExtendedAgentCardNotConfiguredError;
import org.a2aproject.sdk.spec.PushNotificationNotSupportedError;
import org.a2aproject.sdk.spec.UnsupportedOperationError;

/**
 * 标准 A2A gRPC 服务（lf.a2a.v1.A2AService）的实现，也是业务要用 SOFARPC 发布的对象。
 *
 * <p>一个应用只构造、发布一个 A2aService，按请求 tenant 分发到各个 {@link BcnAgent}。
 * 发布时的接口名、版本、协议必须使用 {@link bcn.a2a.sofa.SofaPublishSpec} 中的固定值。
 *
 * <p>所有 A2A 方法都在这里收口，交给 {@link RequestPipeline}。类是 final 的，业务无法绕过 SDK 的校验和协议处理。
 *
 * <p>TODO(SOFA)：SOFA Triple 通过生成接口代理调用时，本类还需要实现该生成接口，见 {@code bcn.a2a.sofa} 包说明。
 */
public final class A2aService implements BindableService, A2AServiceGrpc.AsyncService {

    private final RequestPipeline pipeline;

    private A2aService(RequestPipeline pipeline) {
        this.pipeline = pipeline;
    }

    public static Builder builder() {
        return new Builder();
    }

    /** 原生 gRPC 路径：挂上请求头拦截器，读取 A2A-Extensions / A2A-Version，并在响应头中回写协商结果。 */
    @Override
    public ServerServiceDefinition bindService() {
        return ServerInterceptors.intercept(A2AServiceGrpc.bindService(this), CallHeaders.interceptor());
    }

    // ---------------------------------------------------------------- Profile 支持的五个方法

    @Override
    public void sendMessage(SendMessageRequest request, StreamObserver<SendMessageResponse> out) {
        pipeline.sendMessage(request, out);
    }

    @Override
    public void sendStreamingMessage(SendMessageRequest request, StreamObserver<StreamResponse> out) {
        pipeline.sendStreamingMessage(request, out);
    }

    @Override
    public void getTask(GetTaskRequest request, StreamObserver<Task> out) {
        pipeline.getTask(request, out);
    }

    @Override
    public void subscribeToTask(SubscribeToTaskRequest request, StreamObserver<StreamResponse> out) {
        pipeline.subscribeToTask(request, out);
    }

    @Override
    public void cancelTask(CancelTaskRequest request, StreamObserver<Task> out) {
        pipeline.cancelTask(request, out);
    }

    // ---------------------------------------------------------------- Profile 不支持的方法：返回标准错误

    @Override
    public void listTasks(ListTasksRequest request, StreamObserver<ListTasksResponse> out) {
        pipeline.reject("ListTasks", request.getTenant(), out, new UnsupportedOperationError());
    }

    @Override
    public void createTaskPushNotificationConfig(TaskPushNotificationConfig request,
                                                 StreamObserver<TaskPushNotificationConfig> out) {
        pipeline.reject("CreateTaskPushNotificationConfig", request.getTenant(), out, new PushNotificationNotSupportedError());
    }

    @Override
    public void getTaskPushNotificationConfig(GetTaskPushNotificationConfigRequest request,
                                              StreamObserver<TaskPushNotificationConfig> out) {
        pipeline.reject("GetTaskPushNotificationConfig", request.getTenant(), out, new PushNotificationNotSupportedError());
    }

    @Override
    public void listTaskPushNotificationConfigs(ListTaskPushNotificationConfigsRequest request,
                                                StreamObserver<ListTaskPushNotificationConfigsResponse> out) {
        pipeline.reject("ListTaskPushNotificationConfigs", request.getTenant(), out, new PushNotificationNotSupportedError());
    }

    @Override
    public void deleteTaskPushNotificationConfig(DeleteTaskPushNotificationConfigRequest request, StreamObserver<Empty> out) {
        pipeline.reject("DeleteTaskPushNotificationConfig", request.getTenant(), out, new PushNotificationNotSupportedError());
    }

    @Override
    public void getExtendedAgentCard(GetExtendedAgentCardRequest request, StreamObserver<AgentCard> out) {
        pipeline.reject("GetExtendedAgentCard", request.getTenant(), out,
                new ExtendedAgentCardNotConfiguredError(null, null, null));
    }

    // ---------------------------------------------------------------- 构造

    public static final class Builder {

        private final List<BcnAgent> agents = new ArrayList<>();
        private Executor executor;
        private UnaryOperator<Runnable> taskDecorator = UnaryOperator.identity();
        private A2aListener listener = A2aListener.NOOP;
        private A2aServiceOptions options = A2aServiceOptions.defaults();

        private Builder() {
        }

        /** 本应用的全部 Agent（通常直接注入 Spring 容器中的 {@code List<BcnAgent>}）。 */
        public Builder agents(Collection<? extends BcnAgent> agents) {
            this.agents.addAll(agents);
            return this;
        }

        public Builder agent(BcnAgent agent) {
            this.agents.add(agent);
            return this;
        }

        /** 必填：执行业务代码的线程池，由业务按自己的容量规划提供。 */
        public Builder executor(Executor executor) {
            this.executor = executor;
            return this;
        }

        /** 可选：提交到线程池前包装任务，用于传递调用链等线程上下文（例如 SOFATracer 提供的包装）。 */
        public Builder taskDecorator(UnaryOperator<Runnable> taskDecorator) {
            this.taskDecorator = Objects.requireNonNull(taskDecorator);
            return this;
        }

        /** 可选：日志、指标、告警的接入点。 */
        public Builder listener(A2aListener listener) {
            this.listener = Objects.requireNonNull(listener);
            return this;
        }

        /** 可选：运行参数，默认见 {@link A2aServiceOptions#defaults()}。 */
        public Builder options(A2aServiceOptions options) {
            this.options = Objects.requireNonNull(options);
            return this;
        }

        /** 校验所有 Agent 的声明；有问题直接抛异常，让应用启动失败。 */
        public A2aService build() {
            Objects.requireNonNull(executor, "executor is required");
            AgentRegistry registry = AgentRegistry.of(agents);
            return new A2aService(new RequestPipeline(registry, options, executor, taskDecorator, listener));
        }
    }
}
