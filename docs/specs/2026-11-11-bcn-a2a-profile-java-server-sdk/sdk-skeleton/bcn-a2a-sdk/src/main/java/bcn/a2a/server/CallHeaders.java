package bcn.a2a.server;

import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.Optional;
import java.util.Set;

import io.grpc.Context;
import io.grpc.Contexts;
import io.grpc.ForwardingServerCall;
import io.grpc.Metadata;
import io.grpc.ServerCall;
import io.grpc.ServerCallHandler;
import io.grpc.ServerInterceptor;

/**
 * A2A 的请求头和响应头：
 * <ul>
 *   <li>读取 {@code a2a-extensions}（调用方请求激活的扩展 URI，逗号分隔）和 {@code a2a-version}；</li>
 *   <li>在响应头中回写本次实际激活的扩展（协商结果）。</li>
 * </ul>
 *
 * <p>原生 gRPC 路径通过 {@link #interceptor()} 接入。没有经过拦截器时（例如 SOFA 代理路径、测试直接调用），
 * 一律按“没有请求任何扩展、没有声明版本”处理，不会出现空指针。
 */
final class CallHeaders {

    static final Metadata.Key<String> EXTENSIONS = Metadata.Key.of("a2a-extensions", Metadata.ASCII_STRING_MARSHALLER);
    static final Metadata.Key<String> VERSION = Metadata.Key.of("a2a-version", Metadata.ASCII_STRING_MARSHALLER);

    private static final Context.Key<State> STATE = Context.key("bcn-a2a-call-headers");

    /** 一次调用的头信息。activated 由请求处理线程写入，发送响应头的线程读取。 */
    private static final class State {
        final Set<String> requested;
        final String version;
        volatile Set<String> activated = Set.of();

        State(Set<String> requested, String version) {
            this.requested = requested;
            this.version = version;
        }
    }

    private CallHeaders() {
    }

    static ServerInterceptor interceptor() {
        return new ServerInterceptor() {
            @Override
            public <Q, R> ServerCall.Listener<Q> interceptCall(ServerCall<Q, R> call, Metadata headers,
                                                               ServerCallHandler<Q, R> next) {
                State state = new State(parse(headers.getAll(EXTENSIONS)), headers.get(VERSION));
                ServerCall<Q, R> withResponseHeaders = new ForwardingServerCall.SimpleForwardingServerCall<>(call) {
                    @Override
                    public void sendHeaders(Metadata responseHeaders) {
                        // 响应头在第一次 onNext 时发出；RequestPipeline 在派发业务之前已调用 confirm()，顺序有保证。
                        if (!state.activated.isEmpty()) {
                            responseHeaders.put(EXTENSIONS, String.join(",", state.activated));
                        }
                        super.sendHeaders(responseHeaders);
                    }
                };
                return Contexts.interceptCall(Context.current().withValue(STATE, state), withResponseHeaders, headers, next);
            }
        };
    }

    /** 调用方请求激活的扩展；没有经过拦截器时为空集合。 */
    static Set<String> requested() {
        State state = STATE.get();
        return state == null ? Set.of() : state.requested;
    }

    /** 调用方声明的协议版本；没有声明时为空。 */
    static Optional<String> version() {
        State state = STATE.get();
        return state == null ? Optional.empty() : Optional.ofNullable(state.version);
    }

    /** 记录本次实际激活的扩展，随响应头返回。必须在发出第一条响应之前调用。 */
    static void confirm(Set<String> activated) {
        State state = STATE.get();
        if (state != null) {
            state.activated = Set.copyOf(activated);
        }
    }

    private static Set<String> parse(Iterable<String> values) {
        Set<String> uris = new LinkedHashSet<>();
        if (values != null) {
            for (String value : values) {
                Arrays.stream(value.split(",")).map(String::trim).filter(s -> !s.isEmpty()).forEach(uris::add);
            }
        }
        return Set.copyOf(uris);
    }
}
