package a2a.agent.client;

import java.util.concurrent.CompletableFuture;

/** 一条进行中的流式调用。 */
public interface StreamHandle {

    /** 取消调用：gRPC 会把取消传给服务端，HTTP 会断开连接。之后只会收到 onError(CANCELLED)。 */
    void cancel();

    /** 与回调等价的 Future：成功时得到最终结果，失败时以 {@link A2aCallException} 完成。 */
    CompletableFuture<Reply> completion();
}
