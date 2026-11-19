package a2a.agent.client.transport.http;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpHeaders;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionException;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Function;

import a2a.agent.client.transport.CallContext;
import a2a.agent.client.transport.RemoteError;
import a2a.agent.client.transport.StreamSink;
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.google.protobuf.InvalidProtocolBufferException;
import com.google.protobuf.Message;
import com.google.protobuf.MessageOrBuilder;
import com.google.protobuf.util.JsonFormat;

/**
 * 两种 HTTP 绑定的公共部分：共享 HttpClient、ProtoJSON 编解码、请求头、SSE 流式调用、错误转换。
 *
 * <p><b>性能</b>：
 * <ul>
 *   <li>一个应用共用一个 {@link HttpClient}（HTTP/2 多路复用 + 连接池），全部用 {@code sendAsync}，不占业务线程；</li>
 *   <li>{@link JsonFormat.Printer} / {@link JsonFormat.Parser} 不可变、线程安全，全局复用；</li>
 *   <li>SSE 按需读取（{@link SseSubscriber}），慢消费者不会让事件堆积在内存里。</li>
 * </ul>
 */
final class HttpSupport {

    static final JsonFormat.Printer PRINTER = JsonFormat.printer().omittingInsignificantWhitespace();
    static final JsonFormat.Parser PARSER = JsonFormat.parser().ignoringUnknownFields();

    /** TODO(待定)：规范建议 application/a2a+json；BCS 拉取 Card 时只接受 application/json。实现时按服务端实际情况确定。 */
    static final String JSON = "application/json";

    private HttpSupport() {
    }

    /** 应用级共享的 HttpClient。TODO(内部)：代理、TLS 证书、连接池上限、执行线程池按公司要求配置。 */
    static HttpClient defaultClient() {
        return HttpClient.newBuilder()
                .version(HttpClient.Version.HTTP_2)
                .connectTimeout(Duration.ofSeconds(3))
                .build();
    }

    static HttpRequest.Builder request(URI uri, CallContext context) {
        HttpRequest.Builder builder = HttpRequest.newBuilder(uri)
                // 对流式调用，这个超时只约束“收到响应头”的时间；整条流的时限由上层定时器控制
                .timeout(context.timeout())
                .header("A2A-Version", CallContext.PROTOCOL_VERSION);
        if (context.extensionsHeader() != null) {
            builder.header("A2A-Extensions", context.extensionsHeader());
        }
        return builder;
    }

    static String print(MessageOrBuilder message) {
        try {
            return PRINTER.print(message);
        } catch (InvalidProtocolBufferException e) {
            throw RemoteError.protocol("cannot encode request", e);
        }
    }

    static <B extends Message.Builder> B parse(String json, B builder) {
        try {
            PARSER.merge(json, builder);
            return builder;
        } catch (InvalidProtocolBufferException | RuntimeException e) {
            throw RemoteError.protocol("cannot decode response", e);
        }
    }

    /** 非 2xx 响应 → RemoteError，尽量从错误体中取出 ErrorInfo.reason。 */
    static RemoteError httpError(int status, String body) {
        return RemoteError.http(status, reasonFrom(body), "HTTP " + status);
    }

    /**
     * 从错误体中找 ErrorInfo.reason。按 google.rpc.Status 的 JSON 形式尝试：
     * {@code {"error":{"details":[{"reason":"TASK_NOT_FOUND", ...}]}}} 或顶层就是 Status。
     * TODO(规范)：按 A2A 1.0 HTTP+JSON 绑定最终确定的错误体格式收紧。
     */
    static String reasonFrom(String body) {
        if (body == null || body.isBlank()) {
            return null;
        }
        try {
            JsonElement root = JsonParser.parseString(body);
            JsonObject status = root.isJsonObject() && root.getAsJsonObject().has("error")
                    && root.getAsJsonObject().get("error").isJsonObject()
                    ? root.getAsJsonObject().getAsJsonObject("error") : root.isJsonObject() ? root.getAsJsonObject() : null;
            return status == null ? null : reasonInDetails(status.get("details"));
        } catch (RuntimeException e) {
            return null;
        }
    }

    /** JSON-RPC 错误的 data、google.rpc.Status 的 details：找第一个带 reason 的对象。 */
    static String reasonInDetails(JsonElement details) {
        if (details == null || details.isJsonNull()) {
            return null;
        }
        if (details.isJsonObject()) {
            JsonElement reason = details.getAsJsonObject().get("reason");
            return reason != null && reason.isJsonPrimitive() ? reason.getAsString() : null;
        }
        if (details.isJsonArray()) {
            JsonArray array = details.getAsJsonArray();
            for (JsonElement item : array) {
                String reason = reasonInDetails(item);
                if (reason != null) {
                    return reason;
                }
            }
        }
        return null;
    }

    static boolean isEventStream(HttpHeaders headers) {
        return headers.firstValue("Content-Type").map(v -> v.toLowerCase().startsWith("text/event-stream")).orElse(false);
    }

    /** 一元调用：发请求、检查状态码、交给 decoder 解析 2xx 响应体。 */
    static <T> CompletableFuture<T> unary(HttpClient http, HttpRequest request, CallContext context, Function<String, T> decoder) {
        return http.sendAsync(request, HttpResponse.BodyHandlers.ofString(StandardCharsets.UTF_8))
                .handle((response, error) -> {
                    if (error != null) {
                        throw new CompletionException(RemoteError.network(unwrap(error)));
                    }
                    context.notifyActivated(response.headers().firstValue("A2A-Extensions").orElse(null));
                    if (response.statusCode() / 100 != 2) {
                        throw new CompletionException(httpError(response.statusCode(), response.body()));
                    }
                    return decoder.apply(response.body());
                });
    }

    /**
     * 流式调用：响应是 SSE 时按事件解码后交给接收方；否则读出错误体并以错误结束。
     *
     * @param decoder 把一个 SSE 事件的 data 解码成事件对象；解码失败抛 {@link RemoteError}
     */
    static <T> void stream(HttpClient http, HttpRequest request, CallContext context,
                           StreamSink<T> target, Function<String, T> decoder) {
        StreamSink<T> sink = new OnceSink<>(target);
        SseSubscriber<T> subscriber = new SseSubscriber<>(sink, decoder);
        AtomicReference<CompletableFuture<?>> pendingRef = new AtomicReference<>();
        // 先把控制句柄交给接收方，再发请求：否则响应很快时，事件可能先于 onStart 到达
        sink.onStart(new StreamSink.StreamControl() {
            @Override
            public void request(int n) {
                subscriber.request(n);
            }

            @Override
            public void cancel() {
                subscriber.cancel();
                CompletableFuture<?> p = pendingRef.get();
                if (p != null) {
                    p.cancel(true);
                }
            }
        });
        if (subscriber.isCancelled()) {
            return;
        }
        CompletableFuture<HttpResponse<Void>> pending = http.sendAsync(request, info -> {
            context.notifyActivated(info.headers().firstValue("A2A-Extensions").orElse(null));
            if (info.statusCode() == 200 && isEventStream(info.headers())) {
                return HttpResponse.BodySubscribers.fromLineSubscriber(subscriber);
            }
            return HttpResponse.BodySubscribers.mapping(HttpResponse.BodySubscribers.ofString(StandardCharsets.UTF_8), body -> {
                sink.onError(httpError(info.statusCode(), body));
                return null;
            });
        });
        pendingRef.set(pending);
        if (subscriber.isCancelled()) {
            pending.cancel(true);
        }
        pending.whenComplete((response, error) -> {
            if (error != null && !subscriber.isCancelled()) {
                sink.onError(RemoteError.network(unwrap(error)));
            }
        });
    }

    static Throwable unwrap(Throwable error) {
        Throwable e = error;
        while (e instanceof CompletionException && e.getCause() != null) {
            e = e.getCause();
        }
        return e;
    }

    /** 保证 onError / onCompleted 只回调一次（JDK 在流中途出错时，订阅者和 Future 都会收到错误）。 */
    private static final class OnceSink<T> implements StreamSink<T> {
        private final StreamSink<T> delegate;
        private final AtomicBoolean finished = new AtomicBoolean();

        OnceSink(StreamSink<T> delegate) {
            this.delegate = delegate;
        }

        @Override
        public void onStart(StreamControl control) {
            delegate.onStart(control);
        }

        @Override
        public void onNext(T event) {
            if (!finished.get()) {
                delegate.onNext(event);
            }
        }

        @Override
        public void onError(Throwable error) {
            if (finished.compareAndSet(false, true)) {
                delegate.onError(error);
            }
        }

        @Override
        public void onCompleted() {
            if (finished.compareAndSet(false, true)) {
                delegate.onCompleted();
            }
        }
    }
}
