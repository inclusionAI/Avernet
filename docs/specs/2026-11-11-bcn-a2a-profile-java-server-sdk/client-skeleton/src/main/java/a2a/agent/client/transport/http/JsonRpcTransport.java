package a2a.agent.client.transport.http;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.atomic.AtomicLong;
import java.util.function.Supplier;

import a2a.agent.client.transport.A2aTransport;
import a2a.agent.client.transport.CallContext;
import a2a.agent.client.transport.RemoteError;
import a2a.agent.client.transport.StreamSink;
import a2a.agent.client.transport.TransportEndpoint;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.protobuf.Message;
import com.google.protobuf.MessageOrBuilder;
import org.a2aproject.sdk.grpc.AgentInterface;
import org.a2aproject.sdk.grpc.CancelTaskRequest;
import org.a2aproject.sdk.grpc.GetTaskRequest;
import org.a2aproject.sdk.grpc.SendMessageRequest;
import org.a2aproject.sdk.grpc.SendMessageResponse;
import org.a2aproject.sdk.grpc.StreamResponse;
import org.a2aproject.sdk.grpc.SubscribeToTaskRequest;
import org.a2aproject.sdk.grpc.Task;

/**
 * JSONRPC 绑定（A2A 1.0 第 9 节）：所有方法 POST 到 Card 中的 url。
 * <pre>
 *   请求：{"jsonrpc":"2.0","id":N,"method":"SendMessage","params":{SendMessageRequest 的 ProtoJSON}}
 *   响应：{"jsonrpc":"2.0","id":N,"result":{SendMessageResponse / Task 的 ProtoJSON}} 或 {"error":{...}}
 *   流式：SSE，每个事件的 data 是一个上面的响应，result 为 StreamResponse
 * </pre>
 */
public final class JsonRpcTransport implements A2aTransport {

    public static final String BINDING = "JSONRPC";

    private final HttpClient http;
    private final AtomicLong ids = new AtomicLong();

    private JsonRpcTransport(HttpClient http) {
        this.http = http;
    }

    public static JsonRpcTransport create() {
        return new JsonRpcTransport(HttpSupport.defaultClient());
    }

    /** 与其他 HTTP 传输共用一个 HttpClient（推荐）。 */
    public static JsonRpcTransport create(HttpClient sharedClient) {
        return new JsonRpcTransport(sharedClient);
    }

    @Override
    public String binding() {
        return BINDING;
    }

    @Override
    public boolean accepts(AgentInterface agentInterface) {
        // TODO(内部)：域名白名单
        String url = agentInterface.getUrl();
        return url.startsWith("http://") || url.startsWith("https://");
    }

    @Override
    public TransportEndpoint open(AgentInterface agentInterface) {
        return new Endpoint(URI.create(agentInterface.getUrl()));
    }

    @Override
    public void close() {
        // JDK 17 的 HttpClient 没有 close
    }

    /** 直接拼接信封，避免把 params 先解析成 JSON 树再序列化一遍。 */
    private String envelope(String method, MessageOrBuilder params) {
        return "{\"jsonrpc\":\"2.0\",\"id\":" + ids.incrementAndGet() + ",\"method\":\"" + method
                + "\",\"params\":" + HttpSupport.print(params) + "}";
    }

    /** 解析响应信封：有 error 抛 RemoteError，否则把 result 解析成指定的 protobuf 消息。 */
    private static <B extends Message.Builder> B result(String body, Supplier<B> builder) {
        JsonObject envelope;
        try {
            envelope = JsonParser.parseString(body).getAsJsonObject();
        } catch (RuntimeException e) {
            throw RemoteError.protocol("invalid JSON-RPC response", e);
        }
        JsonElement error = envelope.get("error");
        if (error != null && error.isJsonObject()) {
            JsonObject e = error.getAsJsonObject();
            long code = e.has("code") ? e.get("code").getAsLong() : 0;
            String message = e.has("message") ? e.get("message").getAsString() : null;
            // TODO(规范)：A2A 1.0 JSON-RPC 错误 data 中 ErrorInfo 的确切位置
            throw RemoteError.jsonRpc(code, HttpSupport.reasonInDetails(e.get("data")), message);
        }
        JsonElement result = envelope.get("result");
        if (result == null || result.isJsonNull()) {
            throw RemoteError.protocol("JSON-RPC response has neither result nor error", null);
        }
        // TODO(性能)：result 先转成字符串再交给 JsonFormat，多一次序列化；压测确认为热点时改为流式解析
        return HttpSupport.parse(result.toString(), builder.get());
    }

    private final class Endpoint implements TransportEndpoint {

        private final URI uri;

        Endpoint(URI uri) {
            this.uri = uri;
        }

        @Override
        public CompletableFuture<SendMessageResponse> sendMessage(SendMessageRequest request, CallContext context) {
            return call("SendMessage", request, context, SendMessageResponse::newBuilder).thenApply(SendMessageResponse.Builder::build);
        }

        @Override
        public void sendStreamingMessage(SendMessageRequest request, CallContext context, StreamSink<StreamResponse> sink) {
            stream("SendStreamingMessage", request, context, sink);
        }

        @Override
        public CompletableFuture<Task> getTask(GetTaskRequest request, CallContext context) {
            return call("GetTask", request, context, Task::newBuilder).thenApply(Task.Builder::build);
        }

        @Override
        public CompletableFuture<Task> cancelTask(CancelTaskRequest request, CallContext context) {
            return call("CancelTask", request, context, Task::newBuilder).thenApply(Task.Builder::build);
        }

        @Override
        public void subscribeToTask(SubscribeToTaskRequest request, CallContext context, StreamSink<StreamResponse> sink) {
            stream("SubscribeToTask", request, context, sink);
        }

        private <B extends Message.Builder> CompletableFuture<B> call(String method, MessageOrBuilder params,
                                                                      CallContext context, Supplier<B> builder) {
            return HttpSupport.unary(http, post(envelope(method, params), context).build(), context, body -> result(body, builder));
        }

        private void stream(String method, MessageOrBuilder params, CallContext context, StreamSink<StreamResponse> sink) {
            HttpRequest r = post(envelope(method, params), context).header("Accept", "text/event-stream").build();
            HttpSupport.stream(http, r, context, sink, data -> result(data, StreamResponse::newBuilder).build());
        }

        private HttpRequest.Builder post(String json, CallContext context) {
            return HttpSupport.request(uri, context)
                    .header("Content-Type", HttpSupport.JSON)
                    .POST(HttpRequest.BodyPublishers.ofString(json, StandardCharsets.UTF_8));
        }
    }
}
