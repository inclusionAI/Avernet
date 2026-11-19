package a2a.agent.client;

import java.net.URI;
import java.net.http.HttpClient;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;

import a2a.agent.client.card.AgentCards;
import a2a.agent.client.card.CardLoader;
import a2a.agent.client.card.InterfaceSelector;
import a2a.agent.client.internal.ClientRuntime;
import a2a.agent.client.transport.A2aTransport;
import a2a.agent.client.transport.grpc.DirectGrpcTransport;
import a2a.agent.client.transport.grpc.MeshGrpcTransport;
import a2a.agent.client.transport.http.HttpClients;
import a2a.agent.client.transport.http.HttpJsonTransport;
import a2a.agent.client.transport.http.JsonRpcTransport;
import org.a2aproject.sdk.grpc.AgentCard;

/**
 * A2A Agent Client：按 AgentCard 调用任意标准 A2A Agent。
 *
 * <pre>{@code
 * A2aAgentClient client = A2aAgentClient.builder()
 *         .standardTransports("127.0.0.1:50002")                       // 本地 MOSN 地址
 *         .callerContext(() -> CallerContext.ofUser(currentUserId()))  // 可选
 *         .build();
 *
 * RemoteAgent agent = client.agent(cardJson);       // 缓存起来复用
 * Reply reply = agent.send("订单 1001 发货了吗？");
 * }</pre>
 *
 * <p><b>一个应用一个客户端</b>：客户端持有连接（gRPC Channel、HttpClient）和定时器，是重量级对象，
 * 应用启动时创建、关闭时 {@link #close()}。
 */
public final class A2aAgentClient implements AutoCloseable {

    private final Map<String, A2aTransport> transports;
    private final ClientRuntime runtime;
    private final CardLoader cardLoader;
    private final ConcurrentHashMap<URI, RemoteAgent> agentsByCardUrl = new ConcurrentHashMap<>();

    private A2aAgentClient(Builder builder) {
        this.transports = Map.copyOf(builder.transports);
        this.runtime = new ClientRuntime(builder.options, builder.callerProvider);
        HttpClient cardHttp = builder.cardHttpClient != null ? builder.cardHttpClient : HttpClients.newDefault();
        this.cardLoader = new CardLoader(cardHttp, builder.options.cardTtl(), Duration.ofSeconds(10));
    }

    public static Builder builder() {
        return new Builder();
    }

    /** 用已有的 Card 创建远端 Agent。Card 不可用时抛 {@link A2aCallException}（CARD_INVALID）。 */
    public RemoteAgent agent(AgentCard card) {
        return new RemoteAgent(runtime, InterfaceSelector.select(card, transports, runtime.supportedExtensions()));
    }

    /** Card 的 JSON（ProtoJSON）。 */
    public RemoteAgent agent(String cardJson) {
        return agent(AgentCards.parse(cardJson));
    }

    /**
     * 从 URL 拉取 Card（带缓存）后创建远端 Agent。Card 没有变化时返回同一个 RemoteAgent 实例，
     * 所以高频调用这个方法也不会反复建对象。
     */
    public CompletableFuture<RemoteAgent> agent(URI cardUrl) {
        return cardLoader.load(cardUrl).thenApply(card -> {
            RemoteAgent cached = agentsByCardUrl.get(cardUrl);
            if (cached != null && cached.card() == card) {
                return cached;
            }
            RemoteAgent fresh = agent(card);
            agentsByCardUrl.put(cardUrl, fresh);
            return fresh;
        });
    }

    @Override
    public void close() {
        transports.values().forEach(A2aTransport::close);
        runtime.close();
    }

    public static final class Builder {

        private final Map<String, A2aTransport> transports = new LinkedHashMap<>();
        private ClientOptions options = ClientOptions.defaults();
        private CallerContext.Provider callerProvider;
        private HttpClient cardHttpClient;

        private Builder() {
        }

        /** 注册一种传输。同一绑定只能注册一个。 */
        public Builder transport(A2aTransport transport) {
            if (transports.putIfAbsent(transport.binding(), transport) != null) {
                throw new IllegalArgumentException("duplicate transport for " + transport.binding());
            }
            return this;
        }

        /**
         * 一次注册四种标准传输：SOFA-GRPC（经本地 MOSN）、直连 GRPC、HTTP+JSON、JSONRPC。
         * 两种 HTTP 传输和 Card 拉取共用一个 HttpClient。
         */
        public Builder standardTransports(String mosnTarget) {
            HttpClient http = HttpClients.newDefault();
            this.cardHttpClient = http;
            return transport(MeshGrpcTransport.localMosn(mosnTarget))
                    .transport(DirectGrpcTransport.create())
                    .transport(HttpJsonTransport.create(http))
                    .transport(JsonRpcTransport.create(http));
        }

        /** 调用上下文提供者：每次调用时取当前的 {@code bcn_caller}。 */
        public Builder callerContext(CallerContext.Provider provider) {
            this.callerProvider = provider;
            return this;
        }

        public Builder options(ClientOptions options) {
            this.options = options;
            return this;
        }

        /** 拉取 Card 用的 HttpClient；不设置时新建一个（{@link #standardTransports} 会自动共用）。 */
        public Builder cardHttpClient(HttpClient http) {
            this.cardHttpClient = http;
            return this;
        }

        public A2aAgentClient build() {
            if (transports.isEmpty()) {
                throw new IllegalStateException("register at least one transport");
            }
            return new A2aAgentClient(this);
        }
    }
}
