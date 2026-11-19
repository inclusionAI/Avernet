package a2a.agent.client.transport;

import org.a2aproject.sdk.grpc.AgentInterface;

/**
 * 一种 A2A 绑定的传输实现（SOFA-GRPC、GRPC、HTTP+JSON、JSONRPC）。客户端按 Card 中接口项的
 * {@code protocolBinding} 找到对应的传输，再用它为该接口打开一个 {@link TransportEndpoint}。
 *
 * <p><b>性能约定</b>：传输对象是重量级、长生命周期的，整个应用共用一个。连接（gRPC Channel、HttpClient
 * 连接池）在传输内部复用，绝不按调用创建。{@link #open} 只做预计算（路由头、URL），不建连接。
 */
public interface A2aTransport extends AutoCloseable {

    /** 对应 Card 接口项的 protocolBinding，例如 {@code SOFA-GRPC}。 */
    String binding();

    /**
     * 地址校验：只看格式和白名单，不联网。返回 false 时选接口逻辑会跳过这一项、继续看下一项。
     * TODO(内部)：按公司要求补充白名单（允许的域名、应用名），防止 Card 把调用引向任意地址。
     */
    boolean accepts(AgentInterface agentInterface);

    /**
     * 为一个接口项创建调用端点。端点线程安全、可复用，由 {@code RemoteAgent} 持有，不要按调用创建。
     */
    TransportEndpoint open(AgentInterface agentInterface);

    /** 释放连接。应用关闭时调用一次。 */
    @Override
    void close();
}
