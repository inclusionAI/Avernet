package a2a.agent.client.transport.grpc;

import java.util.concurrent.ConcurrentHashMap;
import java.util.function.Function;

import a2a.agent.client.transport.A2aTransport;
import a2a.agent.client.transport.CallContext;
import a2a.agent.client.transport.TransportEndpoint;
import io.grpc.ManagedChannel;
import io.grpc.ManagedChannelBuilder;
import io.grpc.Metadata;
import org.a2aproject.sdk.grpc.AgentInterface;

/**
 * GRPC 绑定：直连 Card 中的地址（本地联调、外部 Agent）。
 *
 * <p><b>性能</b>：按目标地址缓存 {@link ManagedChannel}，同一地址的所有 Agent 和调用共用一个。
 */
public final class DirectGrpcTransport implements A2aTransport {

    public static final String BINDING = "GRPC";

    private final Function<String, ManagedChannel> channelFactory;
    private final ConcurrentHashMap<String, ManagedChannel> channels = new ConcurrentHashMap<>();

    private DirectGrpcTransport(Function<String, ManagedChannel> channelFactory) {
        this.channelFactory = channelFactory;
    }

    public static DirectGrpcTransport create() {
        return new DirectGrpcTransport(DirectGrpcTransport::defaultChannel);
    }

    /** @param channelFactory 按 Card 中的 url 创建 Channel；测试中用来接入 in-process 服务端 */
    public static DirectGrpcTransport create(Function<String, ManagedChannel> channelFactory) {
        return new DirectGrpcTransport(channelFactory);
    }

    @Override
    public String binding() {
        return BINDING;
    }

    @Override
    public boolean accepts(AgentInterface agentInterface) {
        // TODO(内部)：地址白名单（例如只允许公司域名），防止 Card 把调用引向任意地址
        return !agentInterface.getUrl().isBlank();
    }

    @Override
    public TransportEndpoint open(AgentInterface agentInterface) {
        String url = agentInterface.getUrl();
        ManagedChannel channel = channels.computeIfAbsent(channelKey(url), k -> channelFactory.apply(url));
        Metadata headers = new Metadata();
        headers.put(GrpcEndpoint.VERSION, CallContext.PROTOCOL_VERSION);
        return new GrpcEndpoint(channel, headers);
    }

    @Override
    public void close() {
        channels.values().forEach(ManagedChannel::shutdown);
        channels.clear();
    }

    /** 同一 scheme + host:port 共用一个 Channel；路径不影响 gRPC 寻址。 */
    private static String channelKey(String url) {
        int schemeEnd = url.indexOf("://");
        return (schemeEnd > 0 ? url.substring(0, schemeEnd) : "") + "://" + authority(url);
    }

    /** url 中的 host:port 部分。 */
    private static String authority(String url) {
        int schemeEnd = url.indexOf("://");
        String rest = schemeEnd > 0 ? url.substring(schemeEnd + 3) : url;
        int slash = rest.indexOf('/');
        return slash < 0 ? rest : rest.substring(0, slash);
    }

    private static ManagedChannel defaultChannel(String url) {
        ManagedChannelBuilder<?> builder = ManagedChannelBuilder.forTarget(authority(url));
        if (url.startsWith("https://") || url.startsWith("grpcs://")) {
            builder.useTransportSecurity();
        } else {
            builder.usePlaintext();
        }
        // TODO(内部)：keepAlive、最大消息大小、TLS 证书等
        return builder.build();
    }
}
