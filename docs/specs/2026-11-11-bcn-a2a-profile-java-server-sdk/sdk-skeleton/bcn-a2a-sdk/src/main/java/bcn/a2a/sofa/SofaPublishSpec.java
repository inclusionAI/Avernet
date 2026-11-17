package bcn.a2a.sofa;

/**
 * 发布规范：所有应用发布 A2A 服务时必须使用的固定值。
 *
 * <p>BCS 按“应用名 + 服务名”经 MOSN 寻址。只要有一个应用填得不一样，BCS 就得为它单独配置，
 * 所以这些值不是可选项。业务在 SOFA 的发布配置里引用这里的常量，不要手写。
 *
 * <p>一个应用只发布一个 A2A 服务（{@link bcn.a2a.server.A2aService}），按请求 tenant 分发到各个 Agent，
 * 因此不需要 unique-id。
 *
 * <p>Card 由平台方注册：其中 {@code SOFA-GRPC} 接口项的 url 按 Profile 约定为
 * {@code sofa-grpc://{应用名}/{VERSION}}，tenant 为 Agent 的 id。
 */
public final class SofaPublishSpec {

    private SofaPublishSpec() {
    }

    /** 服务接口名。与 Harness 经 MOSN 联调时验证过的服务名一致。 */
    public static final String INTERFACE = "org.a2aproject.sdk.grpc.A2AServiceGrpc$AsyncService";

    /** 服务版本。 */
    public static final String VERSION = "1.0";

    /** SOFARPC 协议。 */
    public static final String PROTOCOL = "tri";
}
