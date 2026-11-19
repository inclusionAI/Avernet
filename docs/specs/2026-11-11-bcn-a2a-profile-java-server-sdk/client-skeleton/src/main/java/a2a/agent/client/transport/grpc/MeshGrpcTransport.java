package a2a.agent.client.transport.grpc;

import a2a.agent.client.transport.A2aTransport;
import a2a.agent.client.transport.CallContext;
import a2a.agent.client.transport.TransportEndpoint;
import io.grpc.ManagedChannel;
import io.grpc.ManagedChannelBuilder;
import io.grpc.Metadata;
import org.a2aproject.sdk.grpc.AgentInterface;

/**
 * SOFA-GRPC 绑定：经本地 MOSN 调用对方应用发布的标准 A2A gRPC 服务（SOFARPC Triple）。
 *
 * <p>Card 接口项的 url 形如 {@code sofa-grpc://{应用名}/{服务版本}}。目标服务接口名固定为标准 A2A 服务，
 * Card 只能决定“调哪个应用”，不能把调用引向其他接口。
 *
 * <p><b>性能</b>：整个应用只连本地 MOSN 一个地址，因此只建<b>一个</b> {@link ManagedChannel}，
 * 所有 Agent、所有调用共用（HTTP/2 多路复用）。每个 Agent 的路由头在 {@link #open} 时算好一次。
 */
public final class MeshGrpcTransport implements A2aTransport {

    public static final String BINDING = "SOFA-GRPC";

    /** 标准 A2A 服务的接口名，与服务端发布规范一致。 */
    static final String SERVICE_INTERFACE = "org.a2aproject.sdk.grpc.A2AServiceGrpc$AsyncService";

    private static final String SCHEME = "sofa-grpc://";

    private final ManagedChannel channel;
    private final boolean ownsChannel;

    private MeshGrpcTransport(ManagedChannel channel, boolean ownsChannel) {
        this.channel = channel;
        this.ownsChannel = ownsChannel;
    }

    /**
     * 连接本地 MOSN。
     *
     * @param mosnTarget 本地 MOSN 的 gRPC 地址，例如 {@code 127.0.0.1:50002}
     */
    public static MeshGrpcTransport localMosn(String mosnTarget) {
        ManagedChannel channel = ManagedChannelBuilder.forTarget(mosnTarget)
                .usePlaintext()
                // TODO(内部)：keepAlive、最大入站消息大小、空闲超时等按 MOSN 和基座的要求配置
                .build();
        return new MeshGrpcTransport(channel, true);
    }

    /** 使用调用方已有的 Channel（例如基座统一管理的 Channel）；关闭由调用方负责。 */
    public static MeshGrpcTransport using(ManagedChannel channel) {
        return new MeshGrpcTransport(channel, false);
    }

    @Override
    public String binding() {
        return BINDING;
    }

    @Override
    public boolean accepts(AgentInterface agentInterface) {
        // TODO(内部)：按公司要求校验应用名白名单
        return MeshAddress.parse(agentInterface.getUrl()) != null;
    }

    @Override
    public TransportEndpoint open(AgentInterface agentInterface) {
        MeshAddress address = MeshAddress.parse(agentInterface.getUrl());
        if (address == null) {
            throw new IllegalArgumentException("not a SOFA-GRPC address");
        }
        Metadata headers = new Metadata();
        headers.put(GrpcEndpoint.VERSION, CallContext.PROTOCOL_VERSION);
        // 路由头与 BCS 经 MOSN 调用时使用的相同（apps/bcs/.../grpc_mesh.rs），已在联调中验证。
        // TODO(内部)：在基座 5.14.1 上确认头名与取值；需要 VIP 直连等特殊路由时在这里调整。
        headers.put(key("tri-type"), "rpc");
        headers.put(key("mesh_vip_only"), "false");
        headers.put(key("mesh_vip_enforce"), "false");
        headers.put(key("sofa_head_target_app"), address.app());
        headers.put(key("sofa_head_target_service"), SERVICE_INTERFACE + ":" + address.version());
        return new GrpcEndpoint(channel, headers);
    }

    @Override
    public void close() {
        if (ownsChannel) {
            channel.shutdown();
        }
    }

    private static Metadata.Key<String> key(String name) {
        return Metadata.Key.of(name, Metadata.ASCII_STRING_MARSHALLER);
    }

    /** {@code sofa-grpc://{应用名}/{服务版本}}。应用名可能含下划线，所以不用 java.net.URI 解析。 */
    record MeshAddress(String app, String version) {

        static MeshAddress parse(String url) {
            if (url == null || !url.startsWith(SCHEME)) {
                return null;
            }
            String rest = url.substring(SCHEME.length());
            int slash = rest.indexOf('/');
            if (slash <= 0 || slash == rest.length() - 1) {
                return null;
            }
            String app = rest.substring(0, slash);
            String version = rest.substring(slash + 1);
            if (app.chars().anyMatch(Character::isWhitespace) || version.contains("/")) {
                return null;
            }
            return new MeshAddress(app, version);
        }
    }
}
