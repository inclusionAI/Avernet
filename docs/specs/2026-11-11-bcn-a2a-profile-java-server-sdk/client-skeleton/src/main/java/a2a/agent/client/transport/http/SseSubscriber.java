package a2a.agent.client.transport.http;

import java.util.concurrent.Flow;
import java.util.function.Function;

import a2a.agent.client.transport.RemoteError;
import a2a.agent.client.transport.StreamSink;

/**
 * 按行接收 SSE，组装成事件后交给接收方，并实现按需读取（背压）：
 * 接收方没有要下一个事件时，不再向网络层要新的行，TCP 窗口随之停住。
 *
 * <p>只处理 {@code data:} 字段（多行 data 用换行拼接）；{@code event:}、{@code id:}、{@code retry:} 和注释行忽略。
 * 空行表示一个事件结束。
 */
final class SseSubscriber<T> implements Flow.Subscriber<String> {

    private final StreamSink<T> sink;
    private final Function<String, T> decoder;
    private final StringBuilder data = new StringBuilder();

    private Flow.Subscription subscription;
    /** 接收方还要多少个事件。初始为 1（StreamSink 约定只自动投递第一个事件）。 */
    private long demand = 1;
    /** 因为没有需求而暂停了读取。 */
    private boolean paused;
    private boolean cancelled;

    SseSubscriber(StreamSink<T> sink, Function<String, T> decoder) {
        this.sink = sink;
        this.decoder = decoder;
    }

    synchronized void request(int n) {
        demand += n;
        if (paused && subscription != null && !cancelled) {
            paused = false;
            subscription.request(1);
        }
    }

    synchronized void cancel() {
        cancelled = true;
        if (subscription != null) {
            subscription.cancel();
        }
    }

    synchronized boolean isCancelled() {
        return cancelled;
    }

    @Override
    public void onSubscribe(Flow.Subscription s) {
        synchronized (this) {
            subscription = s;
            if (cancelled) {
                s.cancel();
                return;
            }
        }
        s.request(1);
    }

    @Override
    public void onNext(String line) {
        String payload = null;
        synchronized (this) {
            if (cancelled) {
                return;
            }
            if (line.isEmpty()) {
                if (data.length() > 0) {
                    payload = data.toString();
                    data.setLength(0);
                    demand--;
                }
            } else if (line.startsWith("data:")) {
                if (data.length() > 0) {
                    data.append('\n');
                }
                data.append(line, line.length() > 5 && line.charAt(5) == ' ' ? 6 : 5, line.length());
            }
        }
        if (payload != null && !deliver(payload)) {
            return;
        }
        boolean more;
        synchronized (this) {
            if (cancelled) {
                return;
            }
            more = demand > 0;
            paused = !more;
        }
        if (more) {
            subscription.request(1);
        }
    }

    @Override
    public void onError(Throwable error) {
        if (!isCancelled()) {
            sink.onError(RemoteError.network(error));
        }
    }

    @Override
    public void onComplete() {
        String rest;
        synchronized (this) {
            if (cancelled) {
                return;
            }
            rest = data.length() > 0 ? data.toString() : null;
            data.setLength(0);
        }
        if (rest != null && !deliver(rest)) {
            return;
        }
        sink.onCompleted();
    }

    /** @return false 表示解码失败，已取消并通知了错误 */
    private boolean deliver(String payload) {
        T event;
        try {
            event = decoder.apply(payload);
        } catch (RuntimeException e) {
            cancel();
            sink.onError(e instanceof RemoteError ? e : RemoteError.protocol("cannot decode event", e));
            return false;
        }
        sink.onNext(event);
        return true;
    }
}
