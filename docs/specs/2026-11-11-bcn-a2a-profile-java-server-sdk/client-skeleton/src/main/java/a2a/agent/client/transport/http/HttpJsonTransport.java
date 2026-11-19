package a2a.agent.client.transport.http;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.CompletableFuture;

import a2a.agent.client.transport.A2aTransport;
import a2a.agent.client.transport.CallContext;
import a2a.agent.client.transport.StreamSink;
import a2a.agent.client.transport.TransportEndpoint;
import org.a2aproject.sdk.grpc.AgentInterface;
import org.a2aproject.sdk.grpc.CancelTaskRequest;
import org.a2aproject.sdk.grpc.GetTaskRequest;
import org.a2aproject.sdk.grpc.SendMessageRequest;
import org.a2aproject.sdk.grpc.SendMessageResponse;
import org.a2aproject.sdk.grpc.StreamResponse;
import org.a2aproject.sdk.grpc.SubscribeToTaskRequest;
import org.a2aproject.sdk.grpc.Task;

/**
 * HTTP+JSON 绑定（A2A 1.0 第 11 节）。路径按 a2a.proto 的 HTTP 注解：
 * <pre>
 *   POST {base}[/{tenant}]/message:send
 *   POST {base}[/{tenant}]/message:stream          （SSE）
 *   GET  {base}[/{tenant}]/tasks/{id}[?historyLength=n]
 *   POST {base}[/{tenant}]/tasks/{id}:cancel
 *   POST {base}[/{tenant}]/tasks/{id}:subscribe    （SSE）
 * </pre>
 * 请求体和响应体是对应 protobuf 消息的 ProtoJSON，解析后与 gRPC 走同一条处理路径。
 */
public final class HttpJsonTransport implements A2aTransport {

    public static final String BINDING = "HTTP+JSON";

    private final HttpClient http;

    private HttpJsonTransport(HttpClient http) {
        this.http = http;
    }

    public static HttpJsonTransport create() {
        return new HttpJsonTransport(HttpSupport.defaultClient());
    }

    /** 与其他 HTTP 传输共用一个 HttpClient（推荐）。 */
    public static HttpJsonTransport create(HttpClient sharedClient) {
        return new HttpJsonTransport(sharedClient);
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
        String base = agentInterface.getUrl().replaceAll("/+$", "");
        if (!agentInterface.getTenant().isEmpty()) {
            base = base + "/" + encode(agentInterface.getTenant());
        }
        return new Endpoint(base);
    }

    @Override
    public void close() {
        // JDK 17 的 HttpClient 没有 close；连接随空闲超时释放
    }

    private static String encode(String segment) {
        // TODO(实现)：URLEncoder 按表单编码（空格变 +），路径段严格编码时替换为 RFC 3986 编码
        return URLEncoder.encode(segment, StandardCharsets.UTF_8);
    }

    private final class Endpoint implements TransportEndpoint {

        private final String base;

        Endpoint(String base) {
            this.base = base;
        }

        @Override
        public CompletableFuture<SendMessageResponse> sendMessage(SendMessageRequest request, CallContext context) {
            HttpRequest r = post(base + "/message:send", HttpSupport.print(request), context).build();
            return HttpSupport.unary(http, r, context,
                    body -> HttpSupport.parse(body, SendMessageResponse.newBuilder()).build());
        }

        @Override
        public void sendStreamingMessage(SendMessageRequest request, CallContext context, StreamSink<StreamResponse> sink) {
            HttpRequest r = post(base + "/message:stream", HttpSupport.print(request), context)
                    .header("Accept", "text/event-stream").build();
            HttpSupport.stream(http, r, context, sink, data -> HttpSupport.parse(data, StreamResponse.newBuilder()).build());
        }

        @Override
        public CompletableFuture<Task> getTask(GetTaskRequest request, CallContext context) {
            String url = base + "/tasks/" + encode(request.getId())
                    + (request.hasHistoryLength() ? "?historyLength=" + request.getHistoryLength() : "");
            HttpRequest r = HttpSupport.request(URI.create(url), context).GET().build();
            return HttpSupport.unary(http, r, context, body -> HttpSupport.parse(body, Task.newBuilder()).build());
        }

        @Override
        public CompletableFuture<Task> cancelTask(CancelTaskRequest request, CallContext context) {
            HttpRequest r = post(base + "/tasks/" + encode(request.getId()) + ":cancel", HttpSupport.print(request), context).build();
            return HttpSupport.unary(http, r, context, body -> HttpSupport.parse(body, Task.newBuilder()).build());
        }

        @Override
        public void subscribeToTask(SubscribeToTaskRequest request, CallContext context, StreamSink<StreamResponse> sink) {
            // TODO(规范)：a2a.proto 的注解为 GET，规范正文和 BCS 现有实现为 POST，这里先与 BCS 保持一致
            HttpRequest r = post(base + "/tasks/" + encode(request.getId()) + ":subscribe", "{}", context)
                    .header("Accept", "text/event-stream").build();
            HttpSupport.stream(http, r, context, sink, data -> HttpSupport.parse(data, StreamResponse.newBuilder()).build());
        }

        private HttpRequest.Builder post(String url, String json, CallContext context) {
            return HttpSupport.request(URI.create(url), context)
                    .header("Content-Type", HttpSupport.JSON)
                    .POST(HttpRequest.BodyPublishers.ofString(json, StandardCharsets.UTF_8));
        }
    }
}
