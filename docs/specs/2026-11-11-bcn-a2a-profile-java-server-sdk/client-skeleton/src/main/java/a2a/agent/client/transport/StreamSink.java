package a2a.agent.client.transport;

/**
 * 流式响应的接收方。
 *
 * <p><b>背压约定</b>（高并发下防止慢消费者把事件堆在内存里）：
 * <ul>
 *   <li>传输在调用开始时回调 {@link #onStart}，并且只自动投递<b>第一个</b>事件；</li>
 *   <li>接收方每处理完一个事件，调用 {@link StreamControl#request(int) request(1)} 再要下一个；</li>
 *   <li>gRPC 用手动流控实现，HTTP（SSE）用按需订阅实现，网络层据此停止读取。</li>
 * </ul>
 *
 * <p>{@link #onError} 和 {@link #onCompleted} 二者只会回调一个，且只回调一次。
 */
public interface StreamSink<T> {

    void onStart(StreamControl control);

    void onNext(T event);

    /** @param error 一般是 {@link RemoteError} */
    void onError(Throwable error);

    void onCompleted();

    /** 流的控制句柄。线程安全。 */
    interface StreamControl {

        /** 再要 n 个事件。 */
        void request(int n);

        /** 取消调用；gRPC 会把取消传给服务端，HTTP 会断开连接。之后不再回调接收方。 */
        void cancel();
    }
}
