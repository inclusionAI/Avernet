package a2a.agent.client.transport;

import java.util.concurrent.CompletableFuture;

import org.a2aproject.sdk.grpc.CancelTaskRequest;
import org.a2aproject.sdk.grpc.GetTaskRequest;
import org.a2aproject.sdk.grpc.SendMessageRequest;
import org.a2aproject.sdk.grpc.SendMessageResponse;
import org.a2aproject.sdk.grpc.StreamResponse;
import org.a2aproject.sdk.grpc.SubscribeToTaskRequest;
import org.a2aproject.sdk.grpc.Task;

/**
 * 绑定到一个具体接口（地址 + tenant）的调用端点，提供 Profile 中的 5 个标准方法。
 * 这也是客户端的“低层接口”：需要完全自己控制请求和响应的调用方，可以通过 {@code RemoteAgent.raw()} 直接使用。
 *
 * <p><b>统一用 protobuf 模型</b>：三种绑定在线上的数据结构相同（HTTP 绑定是同一 protobuf 的 ProtoJSON），
 * 所以所有传输都收发 {@code org.a2aproject.sdk.grpc} 中的对象，上层只有一条处理路径，也不需要再转换成 a2a-java 的 POJO。
 *
 * <p><b>异步约定</b>：所有方法都不阻塞调用线程。失败时，Future 以 {@link RemoteError} 异常完成，
 * 流式调用通过 {@link StreamSink#onError} 通知；由上层统一分类成用户可见的异常。
 */
public interface TransportEndpoint {

    CompletableFuture<SendMessageResponse> sendMessage(SendMessageRequest request, CallContext context);

    void sendStreamingMessage(SendMessageRequest request, CallContext context, StreamSink<StreamResponse> sink);

    CompletableFuture<Task> getTask(GetTaskRequest request, CallContext context);

    CompletableFuture<Task> cancelTask(CancelTaskRequest request, CallContext context);

    void subscribeToTask(SubscribeToTaskRequest request, CallContext context, StreamSink<StreamResponse> sink);
}
